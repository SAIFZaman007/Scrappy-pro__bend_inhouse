"""Operator CLI.

    python -m app.cli seed                     # (re)load taxonomy, sites, mappings
    python -m app.cli verify  --site startech  # fetch every mapped path, report hits
    python -m app.cli discover --site ryans    # print category links found in the nav
    python -m app.cli sample  --site startech --path /component/processor

``verify`` is the important one: it turns the guessed URL map into a trustworthy one
before anybody runs a real job.
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.models.entities import Site, SiteCategoryMap, Subcategory
from app.scrapers.http import PoliteClient
from app.scrapers.parsing import parse_html
from app.scrapers.registry import get_scraper_class
from app.services.seed import run_all


async def cmd_seed() -> None:
    async with SessionLocal() as db:
        await run_all(db)
    print("Seed complete.")


async def cmd_verify(site_key: str, mark: bool) -> None:
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.key == site_key))).scalar_one()
        rows = (
            await db.execute(
                select(SiteCategoryMap, Subcategory)
                .join(Subcategory, Subcategory.id == SiteCategoryMap.subcategory_id)
                .where(SiteCategoryMap.site_id == site.id)
            )
        ).all()

        scraper_cls = get_scraper_class(site_key)
        ok = miss = 0
        async with PoliteClient(
            site.base_url, site.requests_per_second, site.concurrency
        ) as client:
            scraper = scraper_cls(client)
            for mapping, sub in rows:
                path, params = scraper.listing_url(mapping.url_path, 1)
                try:
                    result = await client.get(path, params=params)
                    count = len(scraper.parse_listing(result).products)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ERROR  {mapping.url_path:<48} {sub.name}: {exc}")
                    miss += 1
                    continue
                if count:
                    ok += 1
                    print(f"  OK     {mapping.url_path:<48} {count:>4} products  ({sub.name})")
                    if mark:
                        mapping.is_verified = True
                else:
                    miss += 1
                    print(f"  EMPTY  {mapping.url_path:<48}    0 products  ({sub.name})")
            if mark:
                await db.commit()
        print(f"\n{site.name}: {ok} working, {miss} to fix.")


async def cmd_discover(site_key: str) -> None:
    """Print every internal category-looking link on the homepage, to help fix maps."""
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.key == site_key))).scalar_one()
        async with PoliteClient(site.base_url, site.requests_per_second, 2) as client:
            result = await client.get("/")
            doc = parse_html(result.html)
            seen: set[str] = set()
            for anchor in doc.css("a[href]"):
                href = (anchor.attributes.get("href") or "").strip()
                if not href.startswith(("/", site.base_url)):
                    continue
                path = href.replace(site.base_url, "") or "/"
                if path in seen or path.count("/") < 1 or len(path) < 4:
                    continue
                seen.add(path)
                label = " ".join(anchor.text().split())[:48]
                if label:
                    print(f"  {path:<56} {label}")


async def cmd_sample(site_key: str, path: str) -> None:
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.key == site_key))).scalar_one()
        scraper_cls = get_scraper_class(site_key)
        async with PoliteClient(site.base_url, site.requests_per_second, 2) as client:
            scraper = scraper_cls(client)
            url, params = scraper.listing_url(path, 1)
            listing = scraper.parse_listing(await client.get(url, params=params))
            print(f"{len(listing.products)} products, has_next={listing.has_next}\n")
            for product in listing.products[:5]:
                print(f"  {product.name[:70]}")
                print(f"    price={product.price} old={product.old_price} stock={product.stock}")
                print(f"    {product.product_url}\n")
            if listing.products:
                detailed = await scraper.enrich(listing.products[0])
                print("First product enriched:")
                print(f"    brand={detailed.brand} rating={detailed.rating} reviews={detailed.reviews}")
                print(f"    images={len(detailed.images)} specs={len(detailed.specs)}")


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="scrappy")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed")

    verify = sub.add_parser("verify")
    verify.add_argument("--site", required=True)
    verify.add_argument("--mark", action="store_true", help="mark working paths as verified")

    discover = sub.add_parser("discover")
    discover.add_argument("--site", required=True)

    sample = sub.add_parser("sample")
    sample.add_argument("--site", required=True)
    sample.add_argument("--path", required=True)

    args = parser.parse_args()
    if args.command == "seed":
        asyncio.run(cmd_seed())
    elif args.command == "verify":
        asyncio.run(cmd_verify(args.site, args.mark))
    elif args.command == "discover":
        asyncio.run(cmd_discover(args.site))
    elif args.command == "sample":
        asyncio.run(cmd_sample(args.site, args.path))


if __name__ == "__main__":
    main()
