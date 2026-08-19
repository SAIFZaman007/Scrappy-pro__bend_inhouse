"""TechLand BD (techlandbd.com) - OpenCart, classic ``.product-layout`` markup.

CRITICAL FIX IN THIS FILE
-------------------------
The previous version computed ``has_next`` with::

    doc.css_first(".pagination a:contains('>')")

``:contains()`` is a jQuery extension, not CSS. selectolax is a C binding over
Lexbor and does not implement it - and it does not raise a Python exception when
you pass it, it **segfaults the interpreter**. Every TechLand listing page took
the arq worker process down with SIGSEGV, which is why this site produced no
data and no traceback: there was no Python left to write one.

``parsing.q``/``parsing.qa`` now refuse unsupported pseudo-classes outright and
log an error, so this class of crash cannot recur anywhere in the codebase.
Pagination is read from OpenCart's own "Showing 1 to 25 of 340 (14 Pages)" line
instead, which is exact.

OpenCart honours ``&limit=`` (options are 15/25/50/75/100), so we request 100 and
make one request where the default template would need four.
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
    to_rating,
)


class TechLandScraper(BaseScraper):
    key = "techland"
    name = "TechLand BD"
    base_url = "https://www.techlandbd.com"

    CARD_SELECTORS = (
        ".product-layout",
        ".product-thumb",
        ".product-grid .product",
        ".product-list .product",
    )
    PAGE_LIMIT = "100"  # OpenCart's largest standard "Show" option

    IMAGE_SELECTOR = (
        ".thumbnails img, .product-image img, .image-additional img, "
        ".product-gallery img, .main-image img"
    )
    SPEC_TABLE = (
        "#tab-specification tr, .table-bordered tr, .attribute tr, table.table tr",
        "td:first-child, th",
        "td:last-child",
    )
    SPEC_BULLETS = "#tab-description li, .short-description li, .product-desc li"

    DETAIL_SELECTORS = {
        "name": ("h1.product-title", ".product-details h1", "h1", ".product-name"),
        "price": (
            ".product-price .price-new",
            ".price-new",
            "[itemprop=price]",
            ".product-price",
        ),
        "old_price": (".price-old", "del", ".product-price-old"),
        "price_block": (".product-price", ".price"),
        "stock": (".product-stock", ".stock span", ".stock", ".availability"),
        "brand": (".product-manufacturer a", ".manufacturer a", "[itemprop=brand]"),
        "sku": (".product-model", ".product-sku", "[itemprop=sku]"),
        "rating": ("[itemprop=ratingValue]", ".rating-value"),
        "rating_style": (".rating .fa-stack", ".star-rating span"),
        "reviews": ("[itemprop=reviewCount]", ".review-count", "#review-title"),
        "description": ("#tab-description", ".product-description", ".tab-content"),
        "badge": (".product-label", ".sale", ".badge"),
    }

    def listing_url(self, url_path: str, page: int) -> tuple[str, dict[str, str] | None]:
        params = {"limit": self.PAGE_LIMIT}
        if page > 1:
            params["page"] = str(page)
        return url_path, params

    def parse_listing(self, result: FetchResult) -> ListingPage:
        doc = self.doc(result)

        cards = []
        for selector in self.CARD_SELECTORS:
            cards = qa(doc, selector)
            if cards:
                break

        products: list[ScrapedProduct] = []
        for card in cards:
            href = first_attr(card, "href", ".caption h4 a", ".name a", "h4 a", ".image a", "a")
            name = first_text(card, ".caption h4 a", ".name a", "h4", ".product-name")
            url = absolutise(href, self.base_url)
            if not url or not name:
                continue

            price = to_decimal(first_text(card, ".price-new", ".price span.price-new"))
            old_price = to_decimal(first_text(card, ".price-old", "del"))
            if price is None:
                figures = all_prices(first_text(card, ".price") or "")
                if figures:
                    price = min(figures)
                    if old_price is None and len(figures) > 1 and max(figures) != price:
                        old_price = max(figures)

            image = image_url(q(card, ".image img, img"), self.base_url)

            products.append(
                ScrapedProduct(
                    product_url=url,
                    name=clean_text(name),
                    brand=guess_brand(name),
                    price=price,
                    old_price=old_price,
                    image=image,
                    images=[image] if image else [],
                    badge=first_text(card, ".product-label", ".sale", ".badge"),
                    stock=normalise_stock(first_text(card, ".stock", ".product-stock")),
                    rating=to_rating(
                        first_attr(card, "style", ".rating .fa-stack, .rating span")
                        or first_text(card, ".rating-value")
                    ),
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