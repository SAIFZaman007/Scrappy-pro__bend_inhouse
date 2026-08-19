"""Ryans Computers (ryans.com) - Laravel storefront, ``/category/<slug>`` listings.

Ryans is server rendered and does not challenge ordinary browser traffic, but it
is the fussiest of the four about request shape: a bare non-browser User-Agent
with an empty cookie jar gets a 403 from the edge before the application runs.
The session warm-up and browser-realistic headers in ``http.py`` are what make
this site work; nothing site-specific is required here.

Card markup uses Bootstrap utility classes heavily, which means class names move
around between releases. That is exactly why the detail parser leans on JSON-LD
first and treats the selectors below as enrichment.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.scrapers.base import BaseScraper, ListingPage, ScrapedProduct
from app.scrapers.http import FetchResult
from app.scrapers.parsing import (
    absolutise,
    all_prices,
    clean_text,
    first_attr,
    first_text,
    guess_brand,
    image_url,
    normalise_stock,
    q,
    qa,
    to_decimal,
)


class RyansScraper(BaseScraper):
    key = "ryans"
    name = "Ryans Computers"
    base_url = "https://www.ryans.com"

    CARD_SELECTORS = (
        ".category-single-product",
        ".product-card",
        ".card.h-100",
        ".cus-col-2",
        ".single-product",
    )

    IMAGE_SELECTOR = (
        ".product-image img, .slider-nav img, .gallery img, "
        ".image-box img, .product-details-image img"
    )
    SPEC_TABLE = (
        ".table-responsive tr, #specification tr, .specification-table tr, table.table tr",
        "td:first-child, th",
        "td:last-child",
    )
    SPEC_BULLETS = ".short-description li, .product-feature li, .feature-list li"

    DETAIL_SELECTORS = {
        "name": ("h1.title-detail", ".product-details h1", "h1", ".product-title"),
        "price": (".new-sp-text", ".price-new", "[itemprop=price]", ".pr-text"),
        "old_price": (".old-sp-text", "del", ".regular-price"),
        "price_block": (".price-box", ".product-price", ".pr-text"),
        "stock": (".stock-status", ".product-status", ".status", ".availability"),
        "brand": (".brand-name", ".brand a", "[itemprop=brand]"),
        "sku": (".product-code", ".sku", "[itemprop=sku]"),
        "rating": ("[itemprop=ratingValue]", ".rating-value"),
        "rating_style": (".star-rating span", ".rating span"),
        "reviews": ("[itemprop=reviewCount]", ".review-count"),
        "description": ("#description", ".product-details-text", ".description"),
        "badge": (".product-label", ".badge", ".offer-tag"),
    }

    def listing_url(self, url_path: str, page: int) -> tuple[str, dict[str, str] | None]:
        return url_path, ({"page": str(page)} if page > 1 else None)

    def parse_listing(self, result: FetchResult) -> ListingPage:
        doc = self.doc(result)

        cards = []
        for selector in self.CARD_SELECTORS:
            cards = qa(doc, selector)
            if cards:
                break

        products: list[ScrapedProduct] = []
        for card in cards:
            href = first_attr(
                card, "href", "p.card-text a", ".card-body a", ".image-box a", "a"
            )
            name = first_text(
                card, "p.card-text a", ".card-text", "h5", ".product-title", ".card-title"
            )
            url = absolutise(href, self.base_url)
            if not url or not name:
                continue

            price = to_decimal(first_text(card, ".pr-text", ".new-sp-text", ".price"))
            old_price = to_decimal(first_text(card, ".old-sp-text", "del", ".regular-price"))
            if price is None or old_price is None:
                figures = all_prices(first_text(card, ".price-box", ".card-body") or "")
                if figures:
                    if price is None:
                        price = min(figures)
                    if old_price is None and len(figures) > 1 and max(figures) != price:
                        old_price = max(figures)

            image = image_url(q(card, ".image-box img, img"), self.base_url)
            stock_text = first_text(card, ".stock-status", ".stock", ".availability")

            products.append(
                ScrapedProduct(
                    product_url=url,
                    name=clean_text(name),
                    brand=guess_brand(name),
                    price=price,
                    old_price=old_price,
                    image=image,
                    images=[image] if image else [],
                    badge=first_text(card, ".product-label", ".label", ".badge"),
                    stock=normalise_stock(stock_text),
                )
            )

        return ListingPage(
            products=products,
            has_next=self.has_next_page(doc, self._page_of(result.url)),
            total_pages=self.total_pages(doc),
        )

    @staticmethod
    def _page_of(url: str) -> int:
        try:
            return int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
        except (TypeError, ValueError):
            return 1