# backend/app/scrapers/base.py
"""The scraper contract.

Adding a fifth retailer means writing one subclass with three methods -
``listing_url``, ``parse_listing`` and ``parse_detail`` - and registering it.
Everything else (paging, politeness, retries, dedupe, progress) is inherited.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from app.core.logging import get_logger
from app.scrapers.http import FetchResult, PoliteClient
from app.scrapers.parsing import parse_html, sniff_product_cards

log = get_logger(__name__)


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
        """Detail-page data wins where the listing page was thin."""
        for attr in (
            "brand",
            "old_price",
            "rating",
            "reviews",
            "badge",
            "description",
            "external_id",
        ):
            if getattr(self, attr) in (None, "") and getattr(other, attr) not in (None, ""):
                setattr(self, attr, getattr(other, attr))
        if other.price is not None:
            self.price = other.price
        if other.stock != "Unknown":
            self.stock = other.stock
        if other.specs:
            self.specs = {**other.specs, **self.specs}
        if other.images:
            merged = list(dict.fromkeys([*self.images, *other.images]))
            self.images = merged
            self.image = self.image or (merged[0] if merged else None)


@dataclass(slots=True)
class ListingPage:
    products: list[ScrapedProduct]
    has_next: bool


ProgressHook = Callable[[str, int], Awaitable[None]]


class BaseScraper(abc.ABC):
    """One instance per site."""

    key: str
    name: str
    base_url: str
    # Some storefronts cap page size; others accept a ``limit`` parameter.
    page_param: str = "page"
    supports_detail_pages: bool = True

    def __init__(self, client: PoliteClient) -> None:
        self.client = client

    # -- to implement per site --------------------------------------------
    @abc.abstractmethod
    def listing_url(self, url_path: str, page: int) -> tuple[str, dict[str, str] | None]:
        """Return ``(path, query_params)`` for a given category page."""

    @abc.abstractmethod
    def parse_listing(self, result: FetchResult) -> ListingPage:
        """Extract product cards from a category page."""

    @abc.abstractmethod
    def parse_detail(self, result: FetchResult) -> ScrapedProduct:
        """Extract the richer fields available only on a product page."""

    # -- shared machinery --------------------------------------------------
    def parse_listing_with_fallback(self, result: FetchResult) -> tuple[ListingPage, bool]:
        """This site's own selectors first; a structural sniff as the backstop.

        Returns ``(listing, used_fallback)``. The fallback only runs when
        ``parse_listing`` found nothing on a page that did return real HTML - a
        working category never pays for the extra parse, and a broken one still
        yields something instead of silently reporting zero products. Both the
        live runner and the ``verify``/``sample`` CLI commands go through this
        same path, so what the CLI reports is what a real run would actually do.
        """
        listing = self.parse_listing(result)
        if listing.products or not result.html:
            return listing, False

        sniffed = sniff_product_cards(self.doc(result), self.base_url)
        if not sniffed:
            return listing, False

        log.info(
            "listing.sniffed_fallback", site=self.key, url=result.url, count=len(sniffed)
        )
        products = [
            ScrapedProduct(
                product_url=item["url"],
                name=item["name"],
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
        for page in range(1, max_pages + 1):
            path, params = self.listing_url(url_path, page)
            result = await self.client.get(path, params=params)
            if result.status == 404:
                log.info("listing.not_found", site=self.key, path=path)
                break

            listing, _used_fallback = self.parse_listing_with_fallback(result)
            if on_page:
                await on_page(result.url, len(listing.products))

            fresh = [p for p in listing.products if p.product_url not in seen]
            if not fresh:
                # Sites that clamp out-of-range pages back to page 1 would
                # otherwise loop forever; an all-duplicate page ends the walk.
                break
            for product in fresh:
                seen.add(product.product_url)
                yield product

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

    # -- convenience -------------------------------------------------------
    def doc(self, result: FetchResult):  # noqa: ANN201
        return parse_html(result.html)