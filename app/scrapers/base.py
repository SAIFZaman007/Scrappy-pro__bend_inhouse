# backend/app/scrapers/base.py
"""The scraper contract.

Adding a fifth retailer still means one subclass and a registry entry. What
changed is where the *reliability* now comes from.

Extraction is layered, highest confidence first:

    1. JSON-LD  (schema.org Product - these sites emit it for Google Shopping)
    2. microdata (itemprop=)
    3. OpenGraph meta tags
    4. this site's hand-written CSS selectors
    5. generic heuristics (any two-column table, structural card sniffing)

Layers 1-3 live in ``structured.py`` and run for free on every detail page. A
theme update that renames every CSS class now degrades a run from excellent to
good rather than from working to zero - which is precisely the failure this
project has been stuck on.

Layer 4 is still worth writing: schema.org has no field for a "Save 3,000৳"
badge, a short-description bullet list, or an old/new price pair, and those are
required export columns.
"""

from __future__ import annotations

import abc
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from selectolax.parser import HTMLParser

from app.core.logging import get_logger
from app.scrapers.http import FetchResult, PoliteClient
from app.scrapers.parsing import (
    all_prices,
    clean_text,
    collect_images,
    first_attr,
    first_text,
    guess_brand,
    normalise_stock,
    parse_html,
    q,
    qa,
    sniff_product_cards,
    specs_from_any_table,
    specs_from_bullets,
    to_decimal,
    to_int,
    to_rating,
)
from app.scrapers.structured import extract_structured_product

log = get_logger(__name__)

# OpenCart renders "Showing 1 to 20 of 122 (7 Pages)" under the grid. That single
# line is a far more reliable stop condition than guessing from pagination links.
_SHOWING_RE = re.compile(
    r"showing\s+\d+\s+to\s+\d+\s+of\s+(\d+)\s*\((\d+)\s*pages?\)", re.IGNORECASE
)


@dataclass(slots=True)
class ScrapedProduct:
    """Site-agnostic product record. Field names mirror the export columns."""

    product_url: str
    name: str
    external_id: str | None = None
    brand: str | None = None
    price: Decimal | None = None
    old_price: Decimal | None = None
    stock: str = "Unknown"
    rating: float | None = None
    reviews: int | None = None
    badge: str | None = None
    image: str | None = None
    images: list[str] = field(default_factory=list)
    specs: dict[str, str] = field(default_factory=dict)
    description: str | None = None
    currency: str = "BDT"
    scraped_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def merge_detail(self, other: ScrapedProduct) -> None:
        """Fold detail-page data into a listing record.

        Rules, in plain terms: the detail page is more authoritative for
        anything it actually found, but it must never blank out something the
        listing already had. Listing pages carry the badge and the old price;
        detail pages carry specs, description, SKU and rating.
        """
        # Fill-if-empty fields.
        for attr in (
            "brand",
            "old_price",
            "rating",
            "reviews",
            "badge",
            "description",
            "external_id",
        ):
            mine = getattr(self, attr)
            theirs = getattr(other, attr)
            if mine in (None, "") and theirs not in (None, ""):
                setattr(self, attr, theirs)

        # A detail-page title is usually the complete one; listings truncate.
        if other.name and len(other.name) > len(self.name or ""):
            self.name = other.name

        if other.price is not None:
            self.price = other.price
        if other.stock != "Unknown":
            self.stock = other.stock
        if other.currency:
            self.currency = other.currency

        if other.specs:
            # Detail specs win; listing bullets fill any gap they left.
            self.specs = {**self.specs, **other.specs}

        if other.images:
            merged = list(dict.fromkeys([*self.images, *other.images]))
            self.images = merged
            self.image = self.image or (merged[0] if merged else None)

    def is_usable(self) -> bool:
        """A row worth persisting: it has a URL, a name, and at least a price."""
        return bool(self.product_url and self.name)


@dataclass(slots=True)
class ListingPage:
    products: list[ScrapedProduct]
    has_next: bool
    total_pages: int | None = None


ProgressHook = Callable[[str, int], Awaitable[None]]


class BaseScraper(abc.ABC):
    """One instance per site."""

    key: str
    name: str
    base_url: str
    page_param: str = "page"
    supports_detail_pages: bool = True

    # Declarative selector map, consumed by ``generic_detail``. Subclasses
    # override only the entries that differ; anything missing falls through to
    # the structured-data layers and the generic heuristics.
    DETAIL_SELECTORS: dict[str, tuple[str, ...]] = {}
    # Selector for the gallery on a detail page.
    IMAGE_SELECTOR: str = "img"
    # Selector for a spec table, tried before the generic table walker.
    SPEC_TABLE: tuple[str, str, str] | None = None
    SPEC_BULLETS: str = ""

    def __init__(self, client: PoliteClient) -> None:
        self.client = client

    # -- to implement per site --------------------------------------------
    @abc.abstractmethod
    def listing_url(self, url_path: str, page: int) -> tuple[str, dict[str, str] | None]:
        """Return ``(path, query_params)`` for a given category page."""

    async def fetch_listing(self, url_path: str, page: int) -> FetchResult:
        """Fetch the category page. Override to use POST for AJAX grids."""
        path, params = self.listing_url(url_path, page)
        return await self.client.get(path, params=params)

    @abc.abstractmethod
    def parse_listing(self, result: FetchResult) -> ListingPage:
        """Extract product cards from a category page."""

    # -- detail parsing ----------------------------------------------------
    def parse_detail(self, result: FetchResult) -> ScrapedProduct:
        """Default detail parser: structured data first, then site selectors.

        Concrete on purpose. Most sites need no override at all; those that do
        should call ``super().parse_detail(result)`` and then patch the two or
        three fields their markup expresses differently.
        """
        return self.generic_detail(result)

    def generic_detail(self, result: FetchResult) -> ScrapedProduct:
        doc = self.doc(result)
        data = extract_structured_product(doc)

        sel = self.DETAIL_SELECTORS
        name = data.get("name") or first_text(doc, *sel.get("name", ("h1",))) or ""

        price = data.get("price")
        if price is None:
            price = to_decimal(first_text(doc, *sel.get("price", ())))

        old_price = to_decimal(first_text(doc, *sel.get("old_price", ())))
        if old_price is None and price is not None:
            # Some themes put both figures in one element. If we find two
            # currency figures and one is larger than the price we already have,
            # that larger one is the pre-discount price.
            blob = first_text(doc, *sel.get("price_block", ())) or ""
            figures = [f for f in all_prices(blob) if f > price]
            old_price = max(figures) if figures else None

        stock = data.get("stock") or normalise_stock(first_text(doc, *sel.get("stock", ())))

        images = list(data.get("images") or [])
        for url in collect_images(doc, self.IMAGE_SELECTOR, self.base_url):
            if url not in images:
                images.append(url)

        specs: dict[str, str] = dict(data.get("specs") or {})
        if self.SPEC_TABLE:
            from app.scrapers.parsing import specs_from_table

            specs.update(specs_from_table(doc, *self.SPEC_TABLE))
        if not specs and self.SPEC_BULLETS:
            specs.update(specs_from_bullets(doc, self.SPEC_BULLETS))
        if not specs:
            specs.update(specs_from_any_table(doc))

        rating = data.get("rating")
        if rating is None:
            rating = to_rating(
                first_text(doc, *sel.get("rating", ()))
                or first_attr(doc, "style", *sel.get("rating_style", ()))
            )

        reviews = data.get("reviews")
        if reviews is None:
            reviews = to_int(first_text(doc, *sel.get("reviews", ())))

        return ScrapedProduct(
            product_url=result.url,
            name=clean_text(name),
            brand=data.get("brand")
            or first_text(doc, *sel.get("brand", ()))
            or guess_brand(name),
            external_id=data.get("external_id") or first_text(doc, *sel.get("sku", ())),
            price=price,
            old_price=old_price,
            stock=stock,
            rating=rating,
            reviews=reviews,
            badge=first_text(doc, *sel.get("badge", ())),
            images=images,
            image=images[0] if images else None,
            specs=specs,
            description=data.get("description")
            or first_text(doc, *sel.get("description", ())),
            currency=data.get("currency") or "BDT",
        )

    # -- pagination --------------------------------------------------------
    def total_pages(self, doc: HTMLParser) -> int | None:
        """Total page count, when the page states it outright.

        OpenCart storefronts (StarTech, TechLand) print "Showing 1 to 20 of 122
        (7 Pages)". Reading that is exact, so we stop on the real last page
        instead of walking until a duplicate happens to appear.
        """
        text = clean_text(doc.body.text() if doc.body else "")
        match = _SHOWING_RE.search(text)
        if match:
            try:
                return int(match.group(2))
            except ValueError:
                return None
        return None

    def has_next_page(self, doc: HTMLParser, page: int) -> bool:
        """Whether a further page exists, from the pagination controls."""
        total = self.total_pages(doc)
        if total is not None:
            return page < total
        # rel=next is the standards-compliant marker; the rest are theme habits.
        if q(doc, "a[rel=next]") is not None:
            return True
        if q(doc, "a.next.page-numbers, li.next:not(.disabled) a, .pagination .next a"):
            return True
        for anchor in qa(doc, ".pagination a, ul.pagination a, .page-link"):
            label = clean_text(anchor.text()).lower()
            if label in (">", "»", "next", "next »", "→"):
                return True
        return False

    # -- shared machinery --------------------------------------------------
    def parse_listing_with_fallback(self, result: FetchResult) -> tuple[ListingPage, bool]:
        """This site's own selectors first; a structural sniff as the backstop.

        Returns ``(listing, used_fallback)``. The fallback only runs when
        ``parse_listing`` found nothing on a page that did return real HTML, so
        a working category never pays for the extra parse.
        """
        listing = self.parse_listing(result)
        if listing.products or not result.html:
            return listing, False

        doc = self.doc(result)
        sniffed = sniff_product_cards(doc, self.base_url)
        if not sniffed:
            return listing, False

        log.info("listing.sniffed_fallback", site=self.key, url=result.url, count=len(sniffed))
        products = [
            ScrapedProduct(
                product_url=item["url"],
                name=item["name"],
                brand=guess_brand(item["name"]),
                price=item["price"],
                image=item["image"],
                images=[item["image"]] if item["image"] else [],
            )
            for item in sniffed
        ]
        return ListingPage(products=products, has_next=True), True

    async def iter_category(
        self,
        url_path: str,
        max_pages: int,
        on_page: ProgressHook | None = None,
    ) -> AsyncIterator[ScrapedProduct]:
        """Walk a category's pages until it runs out or the page cap is hit."""
        seen: set[str] = set()
        empty_streak = 0

        for page in range(1, max_pages + 1):
            result = await self.fetch_listing(url_path, page)
            if result.status == 404:
                log.info("listing.not_found", site=self.key, path=result.url)
                break

            listing, used_fallback = self.parse_listing_with_fallback(result)
            if on_page:
                await on_page(result.url, len(listing.products))

            if not listing.products:
                # One empty page can be a transient hiccup; two in a row means
                # the category really is exhausted or the URL is wrong.
                empty_streak += 1
                if empty_streak >= 2:
                    break
                continue
            empty_streak = 0

            fresh = [p for p in listing.products if p.product_url not in seen]
            if not fresh:
                # Sites that clamp an out-of-range page back to page 1 would
                # otherwise loop forever; an all-duplicate page ends the walk.
                log.info("listing.duplicate_page", site=self.key, page=page)
                break

            for product in fresh:
                seen.add(product.product_url)
                if product.is_usable():
                    yield product

            if used_fallback:
                # The sniffer cannot read pagination reliably, so it only
                # continues while pages keep yielding new URLs.
                continue
            if not listing.has_next:
                break

    async def enrich(self, product: ScrapedProduct) -> ScrapedProduct:
        """Fetch the product page and fold its data into the listing record."""
        if not self.supports_detail_pages:
            return product
        try:
            result = await self.client.get(product.product_url)
            if result.status == 404 or not result.html:
                return product
            product.merge_detail(self.parse_detail(result))
        except Exception as exc:  # noqa: BLE001 - one bad page must not kill a job
            log.warning(
                "detail.failed", site=self.key, url=product.product_url, error=str(exc)
            )
        return product

    def needs_enrichment(self, product: ScrapedProduct) -> bool:
        """True when the listing row is too thin to export as-is.

        Lets a job run in ``missing_only`` detail mode: skip the detail fetch for
        cards that already carry a price, an image and some features, which on
        StarTech is most of them. Roughly a 5x speed-up on a full catalogue run
        with no loss in the columns that matter.
        """
        return not (product.price and product.image and (product.specs or product.description))

    # -- convenience -------------------------------------------------------
    def doc(self, result: FetchResult) -> HTMLParser:
        return parse_html(result.html)