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
SITE_POLICY = {
    "startech": {"requests_per_second": 1.0, "concurrency": 4},
    "techland": {"requests_per_second": 0.8, "concurrency": 3},
    "ryans": {"requests_per_second": 0.8, "concurrency": 3},
    "computermania": {"requests_per_second": 0.6, "concurrency": 2},
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
    created = skipped = 0
    for site_key, mapping in SITE_MAPS.items():
        site = sites.get(site_key)
        if site is None:
            continue
        for taxonomy_key, url_path in mapping.items():
            subcategory = subcategories.get(taxonomy_key)
            if subcategory is None:
                log.warning("seed.mapping_unknown_key", site=site_key, key=taxonomy_key)
                continue
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
                        url_path=url_path,
                        is_verified=False,
                    )
                )
                created += 1
            elif not existing.is_verified:
                existing.url_path = url_path
                skipped += 1
            else:
                skipped += 1
    await db.commit()
    log.info("seed.mappings", created=created, untouched=skipped)


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
