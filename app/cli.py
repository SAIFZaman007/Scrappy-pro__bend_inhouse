# backend/app/cli.py
"""Operator CLI.

    python -m app.cli seed                     # (re)load taxonomy, sites, mappings
    python -m app.cli verify  --site startech  # fetch every mapped path, report hits
    python -m app.cli discover --site ryans    # print category links found in the nav
    python -m app.cli sample  --site startech --path /component/processor

``verify`` turns the guessed URL map into a trustworthy one before anybody runs a
real job. It reports four different outcomes, not just pass/fail, because "found
zero products" has more than one real cause and they need different fixes:

    OK      this site's own selectors found products - trustworthy.
    SNIFF   nothing from this site's own selectors, but the structural fallback
            found something - the URL is right, the selectors need attention.
    EMPTY   a real, substantial page with no products found by either method -
            worth a closer look with `sample`.
    TINY    a 200 OK response with no currency symbol anywhere in it. A genuine
            StarTech/Ryans/TechLand/Computer Mania category page is saturated
            with prices - the complete absence of one, despite a 200 status, is
            the signature of a site serving this client different content than
            it serves a browser, not a broken selector or a wrong URL. `sample`
            now saves the raw response to a file for exactly this situation -
            open it and compare it with the same URL in a real browser.
    BLOCKED a 403/challenge - the site refusing the request outright.

It also stops early if a site blocks several categories in a row - that is the
site answering "no" outright, and working through the rest of the list would
not change that answer.
"""
from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

from sqlalchemy import select

from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.models.entities import Site, SiteCategoryMap, Subcategory
from app.scrapers.http import AccessBlocked, BlockedByRobots, ChallengeDetected, PoliteClient
from app.scrapers.parsing import parse_html
from app.scrapers.registry import get_scraper_class
from app.services.seed import run_all

# After this many categories in a row come back blocked, stop rather than working
# through the rest of the list - see the module docstring.
BLOCK_CIRCUIT_THRESHOLD = 3
# After this many "TINY" (no currency symbol at all) results, stop and explain -
# same reasoning: once the pattern is clear, more requests just confirm it again.
TINY_CIRCUIT_THRESHOLD = 3


async def cmd_seed() -> None:
    async with SessionLocal() as db:
        await run_all(db)
    print("Seed complete.")


def _slug_for_filename(site_key: str, path: str) -> Path:
    slug = path.strip("/").replace("/", "-") or "root"
    return Path(f"sample-{site_key}-{slug}.html")


_WINDOWS_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _msys_mangled_path_hint(path: str) -> str | None:
    """Detect Git Bash / MINGW64's automatic path conversion mangling a --path arg.

    On Windows, Git Bash rewrites any command-line argument that *looks* like a
    Unix absolute path (starts with ``/``) into a Windows path before Python ever
    sees it - ``--path /component/processor`` can arrive here as something like
    ``C:/Program Files/Git/component/processor``. That then breaks three layers
    down inside urllib/httpx with an opaque "missing http:// protocol" error, none
    of which explains what actually happened. This catches the mangled shape up
    front and says so directly instead.
    """
    if _WINDOWS_PATH_RE.match(path) or "\\" in path:
        return (
            f"--path arrived as '{path}', which looks like Git Bash / MINGW64's "
            "automatic path conversion rewrote it - it does this to any argument "
            "starting with '/', assuming it's a Unix path meant for a program "
            "running under it. Three ways around it:\n"
            "  1. MSYS_NO_PATHCONV=1 uv run python -m app.cli sample --site ... --path ...\n"
            "  2. Double the leading slash: --path //component/processor\n"
            "  3. Run this one command from PowerShell or cmd.exe instead."
        )
    return None


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
        ok = sniffed_ok = real_empty = tiny = errored = blocked = 0
        consecutive_blocks = 0
        consecutive_tiny = 0
        stopped_early = False

        async with PoliteClient(
            site.base_url, site.requests_per_second, site.concurrency
        ) as client:
            scraper = scraper_cls(client)
            for mapping, sub in rows:
                path, params = scraper.listing_url(mapping.url_path, 1)
                try:
                    result = await client.get(path, params=params)
                    listing, used_fallback = scraper.parse_listing_with_fallback(result)
                    count = len(listing.products)
                except (ChallengeDetected, BlockedByRobots, AccessBlocked) as exc:
                    print(f"  BLOCKED {mapping.url_path:<47} {sub.name}: {exc}")
                    blocked += 1
                    consecutive_blocks += 1
                    consecutive_tiny = 0
                    if consecutive_blocks >= BLOCK_CIRCUIT_THRESHOLD:
                        print(
                            f"\n{consecutive_blocks} in a row were blocked - "
                            f"{site.name} is refusing these requests outright. "
                            "Stopping here; the rest of the list would not fare "
                            "any differently."
                        )
                        stopped_early = True
                        break
                    continue
                except Exception as exc:  # noqa: BLE001
                    print(f"  ERROR   {mapping.url_path:<47} {sub.name}: {exc}")
                    errored += 1
                    consecutive_blocks = 0
                    consecutive_tiny = 0
                    continue

                consecutive_blocks = 0

                if result.status == 404:
                    print(f"  404     {mapping.url_path:<47} not found  ({sub.name})")
                    real_empty += 1
                    consecutive_tiny = 0
                    continue

                if not count:
                    taka_hits = result.html.count("৳")
                    size_kb = len(result.html) / 1024
                    if taka_hits == 0:
                        tiny += 1
                        consecutive_tiny += 1
                        print(
                            f"  TINY    {mapping.url_path:<47} {size_kb:>6.1f} KB, "
                            f"no ৳ anywhere  ({sub.name})"
                        )
                        if consecutive_tiny >= TINY_CIRCUIT_THRESHOLD:
                            print(
                                f"\n{consecutive_tiny} in a row had no currency symbol "
                                f"at all despite a 200 OK - {site.name} looks to be "
                                "serving this client different content than a browser "
                                "gets, not failing to find products on a real page. "
                                "Stopping here; run `sample` on one of the TINY paths "
                                "above (it saves the raw response to a file) and "
                                "compare it with the same URL open in a real browser."
                            )
                            stopped_early = True
                            break
                        continue
                    real_empty += 1
                    consecutive_tiny = 0
                    print(
                        f"  EMPTY   {mapping.url_path:<47} {size_kb:>6.1f} KB, "
                        f"{taka_hits} × ৳ found  ({sub.name})"
                    )
                    continue

                consecutive_tiny = 0
                if used_fallback:
                    sniffed_ok += 1
                    print(
                        f"  SNIFF   {mapping.url_path:<47} {count:>4} products  ({sub.name})"
                        "  -- via structural fallback, not this site's own selectors"
                    )
                    # Deliberately not auto-marked verified even with --mark: this
                    # confirms the URL and that *something* is there, not that this
                    # site's own CARD_SELECTORS understand the page.
                else:
                    ok += 1
                    print(f"  OK      {mapping.url_path:<47} {count:>4} products  ({sub.name})")
                    if mark:
                        mapping.is_verified = True

            if mark:
                await db.commit()

        print(
            f"\n{site.name}: {ok} working, {sniffed_ok} via fallback, "
            f"{real_empty} empty (real page), {tiny} suspiciously thin, "
            f"{errored} errored, {blocked} blocked."
            + (" (stopped early)" if stopped_early else "")
        )
        if tiny:
            print(
                "'Suspiciously thin' rows returned 200 OK with no currency symbol "
                "anywhere in the response - a genuine listing page from any of these "
                "four sites is full of them. That pattern points to the site serving "
                "this client a different response than a browser gets, not a wrong "
                "URL or a broken selector. `sample` now saves the raw response to a "
                "file so you can compare it directly against what a browser sees."
            )
        if real_empty:
            print(
                "'Empty' rows have real, substantial content but neither this site's "
                "own selectors nor the structural fallback found products in it - "
                "worth a `sample` to see what's actually on the page."
            )
        if blocked:
            print(
                "Blocked means the site's own defences rejected the request outright, "
                "not that the URL is wrong. Slowing down further will not help; that "
                "needs the site owner's permission - or leave those categories unmapped."
            )


async def cmd_discover(site_key: str) -> None:
    """Print every internal category-looking link on the homepage, to help fix maps."""
    async with SessionLocal() as db:
        site = (await db.execute(select(Site).where(Site.key == site_key))).scalar_one()
        async with PoliteClient(site.base_url, site.requests_per_second, 2) as client:
            try:
                result = await client.get("/")
            except (ChallengeDetected, BlockedByRobots, AccessBlocked) as exc:
                print(f"BLOCKED: {exc}")
                return
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
            try:
                result = await client.get(url, params=params)
            except (ChallengeDetected, BlockedByRobots, AccessBlocked) as exc:
                print(f"BLOCKED: {exc}")
                return

            if result.status == 404:
                print(f"404 Not Found: {result.url}")
                return

            taka_hits = result.html.count("৳")
            size_kb = len(result.html) / 1024
            dump_path = _slug_for_filename(site_key, path)
            dump_path.write_text(result.html, encoding="utf-8")

            print(f"Fetched {result.url}")
            print(f"  {size_kb:.1f} KB, {taka_hits} occurrence(s) of ৳, HTTP {result.status}")
            print(f"  Raw response saved to {dump_path.resolve()}")

            if taka_hits == 0:
                print(
                    "\n  No currency symbol anywhere in this response. Open the saved "
                    "file above and load the same URL in a real browser side by side:\n"
                    "  if the browser shows a normal, populated category page and this "
                    "file does not, the site is serving this scraper different content "
                    "than it serves a browser - that is not something a URL fix or a "
                    "selector fix can solve, and is worth stopping to consider rather "
                    "than mapping more categories against it."
                )

            listing, used_fallback = scraper.parse_listing_with_fallback(result)
            source = (
                "structural fallback (this site's own selectors found nothing)"
                if used_fallback
                else "this site's own selectors"
            )
            print(f"\n{len(listing.products)} products via {source}, has_next={listing.has_next}\n")
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
        hint = _msys_mangled_path_hint(args.path)
        if hint:
            print(hint)
            return
        asyncio.run(cmd_sample(args.site, args.path))


if __name__ == "__main__":
    main()