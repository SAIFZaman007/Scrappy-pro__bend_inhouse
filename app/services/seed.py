# backend/app/services/seed.py
"""Idempotent bootstrap: taxonomy, sites, category mappings and the admin account.

Runs on every boot. Re-running is safe - existing rows are updated, not duplicated,
and a mapping that a human has already marked verified is never overwritten.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import hash_password
from app.models.entities import Category, SiteCategoryMap, Site, Subcategory, User
from app.scrapers.registry import SCRAPERS
from app.taxonomy.site_maps import SITE_MAPS

log = get_logger(__name__)

TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "taxonomy" / "taxonomy.json"

# Per-site crawl policy. Slower where the storefront is heavier or more protected.
#
# These were originally far more conservative, but that was compensating for a
# now-fixed bug: the HTTP client's token bucket capped burst capacity at 1
# request regardless of `concurrency`, so every job ran effectively serial no
# matter what these numbers said. With that fixed (app/scrapers/http.py),
# these values now do what they say - raised accordingly, still well short of
# what these storefronts serve real browsers at. If a site starts pushing back
# (429s, or a ChallengeDetected entry in a run's tape), lower that one row
# rather than the whole fleet:
#
#     UPDATE sites SET requests_per_second = 1.0, concurrency = 3 WHERE key = '<site>';
#
# New installs pick these values up directly. Existing databases are updated
# by migration 0002 the first time you run `alembic upgrade head`.
SITE_POLICY = {
    "startech": {"requests_per_second": 3.0, "concurrency": 6},
    "techland": {"requests_per_second": 2.5, "concurrency": 5},
    "ryans": {"requests_per_second": 2.5, "concurrency": 5},
    "computermania": {"requests_per_second": 2.0, "concurrency": 4},
}


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[/&]+", "-", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-{2,}", "-", value).strip("-")


async def seed_taxonomy(db: AsyncSession) -> dict[str, Subcategory]:
    """Load categories/subcategories, returning a "cat/sub" -> row lookup."""
    payload = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    lookup: dict[str, Subcategory] = {}

    for position, item in enumerate(payload["categories"], start=1):
        category = (
            await db.execute(select(Category).where(Category.slug == item["slug"]))
        ).scalar_one_or_none()
        if category is None:
            category = Category(slug=item["slug"], name=item["name"], position=position)
            db.add(category)
            await db.flush()
        else:
            category.name = item["name"]
            category.position = position

        for sub_position, sub_name in enumerate(item["subcategories"], start=1):
            sub_slug = slugify(sub_name)
            subcategory = (
                await db.execute(
                    select(Subcategory).where(
                        Subcategory.category_id == category.id, Subcategory.slug == sub_slug
                    )
                )
            ).scalar_one_or_none()
            if subcategory is None:
                subcategory = Subcategory(
                    category_id=category.id,
                    slug=sub_slug,
                    name=sub_name,
                    position=sub_position,
                )
                db.add(subcategory)
                await db.flush()
            else:
                subcategory.name = sub_name
                subcategory.position = sub_position
            lookup[f"{item['slug']}/{sub_slug}"] = subcategory

    await db.commit()
    log.info("seed.taxonomy", subcategories=len(lookup))
    return lookup


async def seed_sites(db: AsyncSession) -> dict[str, Site]:
    sites: dict[str, Site] = {}
    for key, scraper_cls in SCRAPERS.items():
        policy = SITE_POLICY.get(key, {})
        site = (await db.execute(select(Site).where(Site.key == key))).scalar_one_or_none()
        if site is None:
            site = Site(
                key=key,
                name=scraper_cls.name,
                base_url=scraper_cls.base_url,
                requests_per_second=policy.get("requests_per_second", 1.0),
                concurrency=policy.get("concurrency", 3),
            )
            db.add(site)
            await db.flush()
        else:
            site.name = scraper_cls.name
            site.base_url = scraper_cls.base_url
        sites[key] = site
    await db.commit()
    log.info("seed.sites", count=len(sites))
    return sites


async def seed_mappings(
    db: AsyncSession, sites: dict[str, Site], subcategories: dict[str, Subcategory]
) -> None:
    """Apply site_maps.py to the database.

    A mapping a human has verified (via the CLI's ``--mark`` or the admin API) is
    never overwritten here, no matter what this file says - that confirmation is
    worth more than anything re-derived from a page fetch. Everything else -
    unverified rows, including ones seeded by a previous, less-accurate version of
    this file - is kept in sync: updated if this file's answer changed, and removed
    if this file no longer has an answer for it at all. A category we've since
    decided we can't reliably map should disappear from the picker, not linger
    there pointing at a URL we already know might be wrong.
    """
    created = updated = removed = untouched = 0
    for site_key, mapping in SITE_MAPS.items():
        site = sites.get(site_key)
        if site is None:
            continue

        seen_subcategory_ids: set[int] = set()
        for taxonomy_key, entry in mapping.items():
            subcategory = subcategories.get(taxonomy_key)
            if subcategory is None:
                log.warning("seed.mapping_unknown_key", site=site_key, key=taxonomy_key)
                continue
            seen_subcategory_ids.add(subcategory.id)

            existing = (
                await db.execute(
                    select(SiteCategoryMap).where(
                        SiteCategoryMap.site_id == site.id,
                        SiteCategoryMap.subcategory_id == subcategory.id,
                    )
                )
            ).scalar_one_or_none()

            if existing is None:
                db.add(
                    SiteCategoryMap(
                        site_id=site.id,
                        subcategory_id=subcategory.id,
                        url_path=entry.path,
                        is_verified=entry.verified,
                    )
                )
                created += 1
            elif not existing.is_verified:
                existing.url_path = entry.path
                existing.is_verified = entry.verified
                updated += 1
            else:
                untouched += 1

        # Prune unverified rows this site used to have that are no longer in the
        # file above - e.g. a category we'd guessed at before and have since
        # decided not to, having found no reliable real URL for it. Skipped if a
        # site's mapping is empty (should never happen) so an upstream mistake
        # can't wipe out every mapping for a site in one seed run.
        if seen_subcategory_ids:
            stale = (
                (
                    await db.execute(
                        select(SiteCategoryMap).where(
                            SiteCategoryMap.site_id == site.id,
                            SiteCategoryMap.is_verified.is_(False),
                            SiteCategoryMap.subcategory_id.notin_(seen_subcategory_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for row in stale:
                await db.delete(row)
                removed += 1

    await db.commit()
    log.info(
        "seed.mappings", created=created, updated=updated, removed=removed, untouched=untouched
    )


async def seed_admin(db: AsyncSession) -> None:
    existing = (
        await db.execute(select(User).where(User.email == settings.FIRST_ADMIN_EMAIL))
    ).scalar_one_or_none()
    if existing:
        return
    db.add(
        User(
            email=settings.FIRST_ADMIN_EMAIL,
            hashed_password=hash_password(settings.FIRST_ADMIN_PASSWORD),
            full_name="Administrator",
            is_admin=True,
        )
    )
    await db.commit()
    log.info("seed.admin_created", email=settings.FIRST_ADMIN_EMAIL)


async def run_all(db: AsyncSession) -> None:
    subcategories = await seed_taxonomy(db)
    sites = await seed_sites(db)
    await seed_mappings(db, sites, subcategories)
    await seed_admin(db)