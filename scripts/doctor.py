#!/usr/bin/env python
"""Scrappy Pro doctor - prove the scraper works before you trust a job.

Runs without Postgres, Redis or the API. It answers, in order, the four
questions that actually decide whether a run will produce data:

    1. Can this Python process decode what these origins send?   (the old bug)
    2. Does the origin answer us at all, or does its WAF refuse? (headers/session)
    3. Does the HTML we get back contain a real product grid?    (URLs/selectors)
    4. Do the parsed rows have the columns the export needs?     (extraction)

Usage
-----
    python scripts/doctor.py                      # all four sites, one category each
    python scripts/doctor.py --site startech
    python scripts/doctor.py --site startech --path /component/processor
    python scripts/doctor.py --site startech --path /component/processor --detail
    python scripts/doctor.py --save               # write the raw HTML to disk

Read the ENCODING line first. If it does not list `br`, stop and run
``pip install brotli`` - nothing downstream will work and every other check will
lie to you about why.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Allow `python scripts/doctor.py` from the backend directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.scrapers.http import (  # noqa: E402
    HAS_BROTLI,
    HAS_ZSTD,
    AccessBlocked,
    BlockedByRobots,
    ChallengeDetected,
    ContentDecodeError,
    PoliteClient,
    supported_accept_encoding,
)
from app.scrapers.parsing import parse_html  # noqa: E402
from app.scrapers.registry import SCRAPERS  # noqa: E402
from app.scrapers.structured import extract_jsonld, find_product_node  # noqa: E402

# One representative, high-volume category per site. These are deliberately
# categories with hundreds of products, so "0 found" is unambiguous.
DEFAULT_PATHS: dict[str, str] = {
    "startech": "/component/processor",
    "techland": "/component/processor",
    "ryans": "/computer-components/processor",
    "computermania": "/product-category/components/processor/",
}

GREEN, RED, YELLOW, BLUE, DIM, RESET = (
    "\033[92m", "\033[91m", "\033[93m", "\033[94m", "\033[2m", "\033[0m"
)


def ok(msg: str) -> None:
    print(f"  {GREEN}PASS{RESET}  {msg}")


def bad(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def info(msg: str) -> None:
    print(f"  {DIM}····{RESET}  {msg}")


def check_environment() -> bool:
    print(f"\n{BLUE}=== 1. Content-encoding capability ==={RESET}")
    print(f"  Accept-Encoding this build will send: {supported_accept_encoding()}")
    healthy = True
    if HAS_BROTLI:
        ok("brotli decoder present")
    else:
        bad(
            "brotli NOT installed. This is the bug that produced 54 KB of mojibake "
            "instead of HTML. Run: pip install brotli"
        )
        healthy = False
    if HAS_ZSTD:
        ok("zstandard decoder present")
    else:
        warn("zstandard not installed (rarely negotiated, but install it anyway)")
    try:
        import playwright  # noqa: F401

        ok("playwright importable (browser fallback available)")
    except ImportError:
        warn(
            "playwright not installed - Computer Mania BD's JS challenge cannot be "
            "answered. Run: pip install playwright && playwright install chromium"
        )
    return healthy


async def check_site(site_key: str, path: str, save: bool, want_detail: bool) -> bool:
    scraper_cls = SCRAPERS[site_key]
    print(f"\n{BLUE}=== {scraper_cls.name} — {path} ==={RESET}")

    async with PoliteClient(
        base_url=scraper_cls.base_url,
        requests_per_second=1.0,
        concurrency=2,
    ) as client:
        scraper = scraper_cls(client)
        listing_path, params = scraper.listing_url(path, 1)

        # -- 2. reachability ------------------------------------------------
        try:
            result = await client.get(listing_path, params=params)
        except BlockedByRobots as exc:
            bad(f"robots.txt refused this path: {exc}")
            info("If you are permitted to read it, set ROBOTS_POLICY=listings")
            return False
        except ChallengeDetected as exc:
            bad(f"anti-bot challenge: {exc}")
            info("Set BROWSER_FALLBACK_ENABLED=true, or requires_browser=true for this site")
            return False
        except AccessBlocked as exc:
            bad(f"refused outright: {exc}")
            return False
        except ContentDecodeError as exc:
            bad(f"body could not be decoded: {exc}")
            return False
        except Exception as exc:  # noqa: BLE001
            bad(f"{type(exc).__name__}: {exc}")
            return False

        ok(f"HTTP {result.status} via {result.via} in {result.elapsed_ms} ms")

        # -- 3. is it real HTML? --------------------------------------------
        html = result.html
        replacement_chars = html.count("\ufffd")
        if "<html" not in html[:5000].lower():
            bad("response does not look like an HTML document")
            info(f"first 120 chars: {html[:120]!r}")
            return False
        if replacement_chars > 20:
            bad(
                f"{replacement_chars} U+FFFD replacement characters - the body is "
                "still arriving undecoded. Check the Accept-Encoding line above."
            )
            return False
        ok(f"decoded {len(html):,} characters of HTML, {replacement_chars} bad chars")

        if "৳" in html or "BDT" in html:
            ok("currency markers present (this is a real catalogue page)")
        else:
            warn(
                "no ৳ or BDT anywhere in the response. A genuine category page on "
                "these sites is saturated with prices - this is likely the wrong "
                "URL, or the site is serving this client different content."
            )

        doc = parse_html(html)
        jsonld = extract_jsonld(doc)
        if find_product_node(jsonld):
            ok(f"schema.org Product markup found ({len(jsonld)} JSON-LD blocks)")
        else:
            info(f"{len(jsonld)} JSON-LD blocks, no Product node on the listing page")

        if save:
            out = Path(f"doctor-{site_key}-{path.strip('/').replace('/', '-') or 'root'}.html")
            out.write_text(html, encoding="utf-8")
            info(f"raw HTML saved to {out}")

        # -- 4. extraction ---------------------------------------------------
        listing, used_fallback = scraper.parse_listing_with_fallback(result)
        if not listing.products:
            bad("0 products parsed from a page that loaded correctly")
            info("The URL is reachable but the card selectors matched nothing.")
            info("Open the saved HTML (--save) and compare against CARD_SELECTORS.")
            return False

        label = "structural sniffer" if used_fallback else "site selectors"
        (warn if used_fallback else ok)(
            f"{len(listing.products)} products parsed via {label}"
        )
        if listing.total_pages:
            info(f"site reports {listing.total_pages} pages for this category")

        # Column-level completeness across the page, which is what the export
        # actually cares about. A 100% name rate with a 0% price rate is a very
        # different problem from "nothing parsed".
        total = len(listing.products)
        for column, getter in (
            ("name", lambda p: p.name),
            ("price", lambda p: p.price),
            ("image", lambda p: p.image),
            ("brand", lambda p: p.brand),
            ("oldPrice", lambda p: p.old_price),
            ("specs", lambda p: p.specs),
        ):
            filled = sum(1 for p in listing.products if getter(p))
            pct = filled * 100 // total
            reporter = ok if pct >= 90 else (warn if pct >= 40 else bad)
            reporter(f"{column:9s} {filled:>3}/{total} ({pct}%)")

        sample = listing.products[0]
        print(f"\n  {DIM}first row:{RESET}")
        print(f"    name      {sample.name[:78]}")
        print(f"    brand     {sample.brand}")
        print(f"    price     {sample.price}   oldPrice: {sample.old_price}")
        print(f"    stock     {sample.stock}")
        print(f"    url       {sample.product_url}")
        print(f"    image     {(sample.image or '')[:78]}")

        # -- 5. detail page ---------------------------------------------------
        if want_detail:
            print(f"\n  {DIM}fetching detail page...{RESET}")
            enriched = await scraper.enrich(sample)
            for column, value in (
                ("sku", enriched.external_id),
                ("rating", enriched.rating),
                ("reviews", enriched.reviews),
                ("images", len(enriched.images)),
                ("specs", len(enriched.specs)),
                ("desc", len(enriched.description or "")),
            ):
                (ok if value else warn)(f"detail {column:8s} {value}")
            if enriched.specs:
                for key, val in list(enriched.specs.items())[:5]:
                    print(f"      {DIM}{key}: {str(val)[:60]}{RESET}")

        return True


async def main() -> int:
    parser = argparse.ArgumentParser(description="Scrappy Pro scraper diagnostics")
    parser.add_argument("--site", choices=sorted(SCRAPERS), help="only check this site")
    parser.add_argument("--path", help="category path to test (defaults per site)")
    parser.add_argument("--detail", action="store_true", help="also fetch one detail page")
    parser.add_argument("--save", action="store_true", help="write raw HTML to disk")
    args = parser.parse_args()

    env_ok = check_environment()
    if not env_ok:
        print(
            f"\n{RED}Stopping: fix the decoder problem first. Every check below "
            f"would fail for that one reason.{RESET}\n"
        )
        return 2

    sites = [args.site] if args.site else list(SCRAPERS)
    results: dict[str, bool] = {}
    for site_key in sites:
        path = args.path if (args.path and args.site) else DEFAULT_PATHS.get(site_key, "/")
        try:
            results[site_key] = await check_site(site_key, path, args.save, args.detail)
        except Exception as exc:  # noqa: BLE001
            bad(f"unhandled error: {type(exc).__name__}: {exc}")
            results[site_key] = False

    print(f"\n{BLUE}=== Summary ==={RESET}")
    for site_key, passed in results.items():
        print(f"  {(GREEN + 'PASS' if passed else RED + 'FAIL') + RESET}  {site_key}")
    print()
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))