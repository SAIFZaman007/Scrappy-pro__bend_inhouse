"""Job runner - the orchestration layer between a queued job and stored products.

Responsibilities, in order:
  1. Resolve which retailer paths the selected subcategories map to.
  2. Walk each category politely, page by page.
  3. Optionally enrich each product from its detail page, with bounded concurrency.
  4. Persist in batches so a long job never holds one giant transaction open.
  5. Keep ``ScrapeJob`` progress current so the UI has something honest to show.

Changes in this revision
-----------------------
* ``requires_browser`` from the ``sites`` row is now actually passed to
  ``PoliteClient``. It was a column nobody read, which meant Computer Mania BD
  never escalated to a browser no matter how it was configured.
* New ``detail_mode`` option: ``all`` / ``missing_only`` / ``off``.
  ``missing_only`` skips the detail request for listing rows that already carry
  a price, an image and some features - which on StarTech is most of them.
  On a full-catalogue run that is roughly a 5x reduction in requests for the
  same populated columns. ``fetch_details`` is still honoured so existing API
  clients and the current frontend keep working unchanged.
* A category that yields zero products now says so on the job tape instead of
  passing silently. Silent zeros are how the original encoding bug went
  unnoticed through several releases: every run "succeeded" with no rows.

Failure policy is unchanged: a single bad page or product is logged and skipped.
A site-wide problem (anti-bot challenge, robots refusal, a bare 403) stops that
category and is recorded on the job. If several categories in a row are blocked,
the whole run stops rather than grinding through the rest of the selection.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.entities import Product, ScrapeJob, Site, SiteCategoryMap, Subcategory
from app.scrapers.base import ScrapedProduct
from app.scrapers.http import AccessBlocked, BlockedByRobots, ChallengeDetected, PoliteClient
from app.scrapers.registry import get_scraper_class
from app.services.seed import slugify  # noqa: F401  (kept for symmetry with seeding)

log = get_logger(__name__)

BATCH_SIZE = 25
MAX_EVENTS = 200
# After this many categories in a row come back blocked, stop the job rather than
# working through the rest of the selection - the site has already answered.
BLOCK_CIRCUIT_THRESHOLD = 3


class JobCancelled(Exception):
    pass


async def _append_event(db: AsyncSession, job: ScrapeJob, message: str, level: str = "info") -> None:
    """Append to the job's rolling event log, which the UI renders as a live tape."""
    event = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "level": level,
        "message": message,
    }
    job.events = ([*job.events, event])[-MAX_EVENTS:]
    await db.flush()


async def _is_cancelled(db: AsyncSession, job_id: Any) -> bool:
    status = (
        await db.execute(select(ScrapeJob.status).where(ScrapeJob.id == job_id))
    ).scalar_one_or_none()
    return status == "cancelled"


async def _resolve_targets(
    db: AsyncSession, site_id: int, subcategory_ids: list[int]
) -> list[tuple[SiteCategoryMap, Subcategory, str]]:
    """Return (mapping, subcategory, category_name) for every mapped selection."""
    rows = (
        await db.execute(
            select(SiteCategoryMap, Subcategory)
            .join(Subcategory, Subcategory.id == SiteCategoryMap.subcategory_id)
            .where(
                SiteCategoryMap.site_id == site_id,
                SiteCategoryMap.subcategory_id.in_(subcategory_ids),
                SiteCategoryMap.is_enabled.is_(True),
            )
        )
    ).all()

    targets = []
    for mapping, subcategory in rows:
        await db.refresh(subcategory, ["category"])
        targets.append((mapping, subcategory, subcategory.category.name))
    return targets


def _to_model(
    product: ScrapedProduct,
    *,
    job_id: Any,
    site_id: int,
    subcategory: Subcategory,
    category_name: str,
    sequence: int,
) -> Product:
    return Product(
        job_id=job_id,
        site_id=site_id,
        subcategory_id=subcategory.id,
        sequence=sequence,
        external_id=product.external_id,
        product_url=product.product_url,
        name=product.name,
        brand=product.brand,
        category_name=category_name,
        subcategory_name=subcategory.name,
        price=product.price,
        old_price=product.old_price,
        currency=product.currency,
        stock=product.stock,
        rating=product.rating,
        reviews=product.reviews,
        badge=product.badge,
        image=product.image,
        images=product.images,
        specs=product.specs,
        description=product.description,
        scraped_at=product.scraped_at,
    )


def _resolve_detail_mode(options: dict) -> str:
    """``detail_mode`` if given, otherwise derive it from legacy ``fetch_details``."""
    mode = str(options.get("detail_mode") or "").lower().strip()
    if mode in ("all", "missing_only", "off"):
        return mode
    # detail_mode absent or null: honour the legacy boolean so a client that
    # still sends fetch_details=False keeps getting listing-only runs.
    return "all" if bool(options.get("fetch_details", True)) else "off"


async def run_job(db: AsyncSession, job_id: Any) -> None:
    job = (await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))).scalar_one_or_none()
    if job is None:
        log.error("job.missing", job_id=str(job_id))
        return
    if job.status not in ("queued", "running"):
        log.info("job.skipped", job_id=str(job_id), status=job.status)
        return

    site = (await db.execute(select(Site).where(Site.id == job.site_id))).scalar_one()
    options = job.options or {}
    max_pages = min(int(options.get("max_pages", 25)), settings.MAX_PAGES_PER_SUBCATEGORY)
    detail_mode = _resolve_detail_mode(options)
    detail_concurrency = max(1, min(int(options.get("detail_concurrency", 4)), site.concurrency))

    targets = await _resolve_targets(db, site.id, job.subcategory_ids)

    job.status = "running"
    job.started_at = datetime.now(UTC)
    job.total_units = max(len(targets), 1)
    job.completed_units = 0
    job.products_found = 0
    job.pages_fetched = 0
    job.error_message = None
    await _append_event(
        db,
        job,
        f"Run started on {site.name} across {len(targets)} categories "
        f"(details: {detail_mode}, robots: {settings.effective_robots_policy}).",
    )
    await db.commit()

    if not targets:
        job.status = "failed"
        job.finished_at = datetime.now(UTC)
        job.error_message = (
            "None of the selected categories are mapped for this site yet. "
            "Map them under Settings, then run again."
        )
        await db.commit()
        return

    sequence = 0
    scraper_cls = get_scraper_class(site.key)

    try:
        async with PoliteClient(
            base_url=site.base_url,
            requests_per_second=site.requests_per_second,
            concurrency=site.concurrency,
            requires_browser=site.requires_browser,
        ) as client:
            scraper = scraper_cls(client)

            consecutive_blocks = 0
            blocked_out = False

            for mapping, subcategory, category_name in targets:
                if await _is_cancelled(db, job.id):
                    raise JobCancelled

                label = f"{category_name} / {subcategory.name}"
                job.current_step = label
                await _append_event(db, job, f"Collecting {label}")
                await db.commit()

                pages_here = 0
                found_here = 0
                buffer: list[ScrapedProduct] = []

                async def on_page(url: str, count: int) -> None:
                    # Committed immediately - not batched with products - so
                    # "Pages read" moves in real time even before the first
                    # product batch is large enough to flush.
                    nonlocal pages_here
                    pages_here += 1
                    job.pages_fetched += 1
                    await db.commit()
                    log.info("page.done", site=site.key, url=url, products=count)

                try:
                    async for product in scraper.iter_category(
                        mapping.url_path, max_pages=max_pages, on_page=on_page
                    ):
                        buffer.append(product)
                        found_here += 1
                        if len(buffer) >= BATCH_SIZE:
                            sequence = await _flush(
                                db, job, buffer, scraper, site, subcategory,
                                category_name, sequence, detail_mode, detail_concurrency,
                            )
                            buffer = []
                    if buffer:
                        sequence = await _flush(
                            db, job, buffer, scraper, site, subcategory,
                            category_name, sequence, detail_mode, detail_concurrency,
                        )

                    if found_here == 0:
                        # Never let a zero pass quietly. A category that loads
                        # fine and yields nothing is a mapping or selector
                        # problem, and the operator needs to see it now, not
                        # discover it in an empty export later.
                        await _append_event(
                            db,
                            job,
                            f"{label}: 0 products from {pages_here} page(s). The URL "
                            f"loaded but no product cards were recognised - check the "
                            f"mapped path with: python scripts/doctor.py --site "
                            f"{site.key} --path {mapping.url_path} --save",
                            level="warning",
                        )
                    else:
                        await _append_event(
                            db,
                            job,
                            f"Finished {label} — {pages_here} pages read, "
                            f"{found_here} products here, {job.products_found} total.",
                        )
                    consecutive_blocks = 0
                except (ChallengeDetected, BlockedByRobots, AccessBlocked) as exc:
                    await _append_event(db, job, f"{label}: {exc}", level="warning")
                    log.warning("category.blocked", site=site.key, label=label, error=str(exc))
                    consecutive_blocks += 1
                    if consecutive_blocks >= BLOCK_CIRCUIT_THRESHOLD:
                        blocked_out = True
                except Exception as exc:  # noqa: BLE001
                    await _append_event(db, job, f"{label} failed: {exc}", level="error")
                    log.exception("category.failed", site=site.key, label=label)
                    consecutive_blocks = 0

                job.completed_units += 1
                await db.commit()

                if blocked_out:
                    await _append_event(
                        db,
                        job,
                        f"Stopped after {consecutive_blocks} categories in a row were "
                        f"blocked by {site.name}. Continuing would only add more denied "
                        "requests. If this site needs a browser engine, set "
                        "requires_browser on it and re-run; otherwise reduce the "
                        "request rate or seek the site owner's permission.",
                        level="error",
                    )
                    break

            job.current_step = None
            job.finished_at = datetime.now(UTC)
            if blocked_out:
                job.status = "failed"
                job.error_message = (
                    f"{site.name} blocked {consecutive_blocks} categories in a row. "
                    "Stopped the run early rather than continuing to send requests to "
                    "a site that is already refusing every one of them."
                )
            else:
                job.status = "completed"
                await _append_event(
                    db, job, f"Run finished with {job.products_found} products collected."
                )
            await db.commit()

    except JobCancelled:
        job.status = "cancelled"
        job.finished_at = datetime.now(UTC)
        await _append_event(db, job, "Run cancelled.", level="warning")
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.finished_at = datetime.now(UTC)
        job.error_message = str(exc)[:2000]
        await _append_event(db, job, f"Run failed: {exc}", level="error")
        await db.commit()
        log.exception("job.failed", job_id=str(job_id))


async def _flush(
    db: AsyncSession,
    job: ScrapeJob,
    buffer: list[ScrapedProduct],
    scraper: Any,
    site: Site,
    subcategory: Subcategory,
    category_name: str,
    sequence: int,
    detail_mode: str,
    detail_concurrency: int,
) -> int:
    """Enrich (optionally), de-duplicate, and write one batch."""
    if detail_mode != "off":
        if detail_mode == "missing_only":
            needs = [p for p in buffer if scraper.needs_enrichment(p)]
        else:
            needs = list(buffer)

        if needs:
            limiter = asyncio.Semaphore(detail_concurrency)

            async def enrich(item: ScrapedProduct) -> ScrapedProduct:
                async with limiter:
                    return await scraper.enrich(item)

            await asyncio.gather(*(enrich(item) for item in needs))
            log.info(
                "batch.enriched",
                site=site.key,
                mode=detail_mode,
                enriched=len(needs),
                skipped=len(buffer) - len(needs),
            )

    existing_urls = set(
        (
            await db.execute(
                select(Product.product_url).where(
                    Product.job_id == job.id,
                    Product.product_url.in_([p.product_url for p in buffer]),
                )
            )
        )
        .scalars()
        .all()
    )

    added = 0
    for product in buffer:
        if product.product_url in existing_urls:
            continue
        sequence += 1
        db.add(
            _to_model(
                product,
                job_id=job.id,
                site_id=site.id,
                subcategory=subcategory,
                category_name=category_name,
                sequence=sequence,
            )
        )
        existing_urls.add(product.product_url)
        added += 1

    await db.flush()
    job.products_found += added
    await db.commit()
    return sequence

