import hashlib
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.entities import (
    GlobalProduct,
    PriceHistory,
    Product,
    ProductVariant,
    ScrapeJob,
    Site,
)

log = get_logger(__name__)

# Stopwords and noise terms common in Bangladeshi retailer listings
NOISE_TERMS = {
    "graphics", "card", "gpu", "desktop", "processor", "cpu", "motherboard",
    "mainboard", "casing", "case", "ram", "memory", "ssd", "hdd", "drive",
    "power", "supply", "psu", "cooler", "cooling", "monitor", "display",
    "laptop", "notebook", "bangladesh", "bd", "official", "unofficial", "price",
    "best", "brand", "new", "authentic", "warranty", "edition", "series",
    "gaming", "rgb", "argb", "black", "white", "fan", "dual", "tri", "triple",
    "full", "box", "package", "combo", "retail", "bulk"
}


def normalize_title_tokens(name: str, brand: str | None = None) -> list[str]:
    """Extract and canonicalize distinguishing hardware tokens."""
    text = f"{brand or ''} {name}".lower()
    
    # 1. Standardize common specs and units
    text = re.sub(r'(\d+)\s*gb\b', r'\1gb', text)
    text = re.sub(r'(\d+)\s*g\b', r'\1gb', text)
    text = re.sub(r'(\d+)\s*tb\b', r'\1tb', text)
    text = re.sub(r'(\d+)\s*mhz\b', r'\1mhz', text)
    text = re.sub(r'(\d+)\s*ghz\b', r'\1ghz', text)
    text = re.sub(r'(\d+)\s*w\b', r'\1w', text)
    text = re.sub(r'(\d+)\s*watt\b', r'\1w', text)
    text = re.sub(r'ddr\s*([345])', r'ddr\1', text)
    text = re.sub(r'gddr\s*([56]x?)', r'gddr\1', text)
    text = re.sub(r'rtx\s*(\d{4})', r'rtx\1', text)
    text = re.sub(r'gtx\s*(\d{4})', r'gtx\1', text)
    text = re.sub(r'rx\s*(\d{4})', r'rx\1', text)
    text = re.sub(r'i([3579])[- ](\d{4,5}[a-z]*)', r'i\1-\2', text)
    text = re.sub(r'ryzen\s*([3579])\s*(\d{4,5}[a-z]*)', r'ryzen\1-\2', text)
    text = re.sub(r'(\d+)(?:th|nd|rd|st)\s*gen\b', r'gen\1', text)
    text = re.sub(r'gen\s*(\d+)\b', r'gen\1', text)

    # 2. Clean punctuation
    text = re.sub(r'[^a-z0-9\s-]', ' ', text)
    raw_tokens = text.replace('-', ' ').split()

    # 3. Filter out noise stopwords while keeping essential model identifiers
    meaningful = [
        t for t in raw_tokens
        if len(t) > 1 and t not in NOISE_TERMS
    ]
    
    # Fallback to raw tokens if filtering stripped too much
    return sorted(set(meaningful if len(meaningful) >= 2 else raw_tokens))


def generate_spec_hash(name: str, brand: str | None) -> str:
    """Generate a stable deterministic hash for cross-site product grouping."""
    tokens = normalize_title_tokens(name, brand)
    normalized = " ".join(tokens)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def match_job_products(db: AsyncSession, job_id: uuid.UUID) -> dict[str, int]:
    """Process a finished job's scraped products into the global catalog."""
    job = (await db.execute(select(ScrapeJob).where(ScrapeJob.id == job_id))).scalar_one_or_none()
    if not job:
        return {"matched": 0, "new_global": 0}

    result = await db.execute(select(Product).where(Product.job_id == job_id))
    products = result.scalars().all()

    if not products:
        return {"matched": 0, "new_global": 0}

    site_id = job.site_id
    matched_count = 0
    new_global_count = 0
    job_timestamp = job.finished_at or job.created_at or datetime.now(UTC)

    log.info("matching.started", job_id=str(job_id), count=len(products))

    for product in products:
        spec_hash = generate_spec_hash(product.name, product.brand)

        # 1. Find or create GlobalProduct
        global_prod = (
            await db.execute(select(GlobalProduct).where(GlobalProduct.spec_hash == spec_hash))
        ).scalar_one_or_none()

        if not global_prod:
            global_prod = GlobalProduct(
                name=product.name,
                brand=product.brand,
                spec_hash=spec_hash,
                created_at=job_timestamp,
            )
            db.add(global_prod)
            await db.flush()
            new_global_count += 1

        # 2. Find or create ProductVariant
        variant = (
            await db.execute(
                select(ProductVariant).where(
                    ProductVariant.global_id == global_prod.id,
                    ProductVariant.site_id == site_id,
                )
            )
        ).scalar_one_or_none()

        if not variant:
            variant = ProductVariant(
                global_id=global_prod.id,
                site_id=site_id,
                external_id=product.external_id,
                product_url=product.product_url,
                latest_price=product.price,
                latest_stock=product.stock,
                updated_at=job_timestamp,
            )
            db.add(variant)
            await db.flush()
        else:
            if product.price is not None:
                variant.latest_price = product.price
            if product.stock:
                variant.latest_stock = product.stock
            variant.product_url = product.product_url
            variant.updated_at = job_timestamp

        # 3. Add PriceHistory snapshot if price exists
        if product.price is not None:
            price_history = PriceHistory(
                variant_id=variant.id,
                price=product.price,
                stock=product.stock,
                timestamp=job_timestamp,
            )
            db.add(price_history)

        matched_count += 1
        if matched_count % 100 == 0:
            await db.commit()

    await db.commit()
    log.info("matching.finished", job_id=str(job_id), matched=matched_count, new_global=new_global_count)
    return {"matched": matched_count, "new_global": new_global_count}


async def sync_all_historical_runs(db: AsyncSession) -> dict[str, int]:
    """Backfill and index all past completed runs into the global catalog."""
    jobs = (
        await db.execute(
            select(ScrapeJob)
            .where(ScrapeJob.status == "completed")
            .order_by(ScrapeJob.created_at.asc())
        )
    ).scalars().all()

    total_jobs = len(jobs)
    total_products = 0
    new_globals = 0

    log.info("sync.history.started", total_jobs=total_jobs)

    for job in jobs:
        stats = await match_job_products(db, job.id)
        total_products += stats["matched"]
        new_globals += stats["new_global"]

    return {
        "jobs_processed": total_jobs,
        "products_processed": total_products,
        "new_global_products": new_globals,
        "variants_updated": total_products,
    }


async def compare_two_runs(
    db: AsyncSession,
    run_a_id: uuid.UUID,
    run_b_id: uuid.UUID,
) -> dict[str, Any]:
    """Perform side-by-side product price and stock comparison between two runs."""
    job_a = (
        await db.execute(
            select(ScrapeJob)
            .where(ScrapeJob.id == run_a_id)
            .options(selectinload(ScrapeJob.site))
        )
    ).scalar_one_or_none()

    job_b = (
        await db.execute(
            select(ScrapeJob)
            .where(ScrapeJob.id == run_b_id)
            .options(selectinload(ScrapeJob.site))
        )
    ).scalar_one_or_none()

    if not job_a or not job_b:
        raise ValueError("One or both runs could not be found.")

    prods_a = (
        await db.execute(select(Product).where(Product.job_id == run_a_id))
    ).scalars().all()

    prods_b = (
        await db.execute(select(Product).where(Product.job_id == run_b_id))
    ).scalars().all()

    # Map by hash key
    map_a: dict[str, Product] = {}
    for p in prods_a:
        key = generate_spec_hash(p.name, p.brand)
        map_a[key] = p

    map_b: dict[str, Product] = {}
    for p in prods_b:
        key = generate_spec_hash(p.name, p.brand)
        map_b[key] = p

    all_keys = set(map_a.keys()) | set(map_b.keys())

    matched = []
    only_in_a = []
    only_in_b = []

    cheaper_in_a_count = 0
    cheaper_in_b_count = 0
    equal_price_count = 0
    max_price_diff = Decimal("0")

    for key in all_keys:
        p_a = map_a.get(key)
        p_b = map_b.get(key)

        if p_a and p_b:
            price_a = p_a.price
            price_b = p_b.price
            diff = None
            pct = None
            cheaper = "unknown"

            if price_a is not None and price_b is not None:
                diff = price_b - price_a
                abs_diff = abs(diff)
                if abs_diff > max_price_diff:
                    max_price_diff = abs_diff

                if price_a > 0:
                    pct = round(float((diff / price_a) * 100), 2)

                if price_a < price_b:
                    cheaper = "A"
                    cheaper_in_a_count += 1
                elif price_b < price_a:
                    cheaper = "B"
                    cheaper_in_b_count += 1
                else:
                    cheaper = "equal"
                    equal_price_count += 1

            matched.append({
                "match_key": key,
                "name": p_a.name,
                "brand": p_a.brand or p_b.brand,
                "product_a_name": p_a.name,
                "product_b_name": p_b.name,
                "product_a_url": p_a.product_url,
                "product_b_url": p_b.product_url,
                "price_a": price_a,
                "price_b": price_b,
                "stock_a": p_a.stock,
                "stock_b": p_b.stock,
                "price_diff": diff,
                "pct_diff": pct,
                "cheaper_run": cheaper,
            })
        elif p_a and not p_b:
            only_in_a.append({
                "match_key": key,
                "name": p_a.name,
                "brand": p_a.brand,
                "product_a_name": p_a.name,
                "product_b_name": None,
                "product_a_url": p_a.product_url,
                "product_b_url": None,
                "price_a": p_a.price,
                "price_b": None,
                "stock_a": p_a.stock,
                "stock_b": None,
                "price_diff": None,
                "pct_diff": None,
                "cheaper_run": "unknown",
            })
        elif p_b and not p_a:
            only_in_b.append({
                "match_key": key,
                "name": p_b.name,
                "brand": p_b.brand,
                "product_a_name": None,
                "product_b_name": p_b.name,
                "product_a_url": None,
                "product_b_url": p_b.product_url,
                "price_a": None,
                "price_b": p_b.price,
                "stock_a": None,
                "stock_b": p_b.stock,
                "price_diff": None,
                "pct_diff": None,
                "cheaper_run": "unknown",
            })

    # Sort matched items by largest price difference first
    matched.sort(
        key=lambda item: abs(item["price_diff"]) if item["price_diff"] is not None else Decimal("0"),
        reverse=True,
    )

    summary = {
        "total_in_a": len(prods_a),
        "total_in_b": len(prods_b),
        "matched_count": len(matched),
        "only_in_a_count": len(only_in_a),
        "only_in_b_count": len(only_in_b),
        "cheaper_in_a_count": cheaper_in_a_count,
        "cheaper_in_b_count": cheaper_in_b_count,
        "equal_price_count": equal_price_count,
        "max_price_diff": max_price_diff,
        "run_a_site_name": job_a.site.name if job_a.site else "Run A",
        "run_b_site_name": job_b.site.name if job_b.site else "Run B",
        "run_a_date": job_a.started_at or job_a.created_at,
        "run_b_date": job_b.started_at or job_b.created_at,
    }

    return {
        "summary": summary,
        "matched": matched,
        "only_in_a": only_in_a,
        "only_in_b": only_in_b,
    }

