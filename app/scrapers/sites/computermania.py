"""Computer Mania BD (computermania.com.bd) - WooCommerce behind Cloudflare.

This is the only one of the four that genuinely needs more than a well-formed
HTTP request. Its Cloudflare configuration intermittently serves a JavaScript
interstitial that no combination of headers can satisfy, because satisfying it
requires executing the challenge script.

The escalation path, in order, is handled entirely by ``PoliteClient``:

    1. Plain HTTP with a realistic browser fingerprint and warmed session.
    2. On 403/503 with challenge markers, one fingerprint rotation and retry.
    3. If still challenged, render the page in headless Chromium
       (``BROWSER_FALLBACK_ENABLED=true``) and hand the DOM back.

Set ``requires_browser=True`` on the ``sites`` row to skip straight to step 3
once you have confirmed the site challenges every request - it removes two
pointless denied requests per page.

WooCommerce paginates by path segment (``/page/2/``), not a query parameter, and
does not offer a per-page size control on most themes, so page counts are higher
here than on the OpenCart sites. Keep the request rate low.
"""

from __future__ import annotations

import re

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

_PAGE_IN_PATH_RE = re.compile(r"/page/(\d+)/?")


class ComputerManiaScraper(BaseScraper):
    key = "computermania"
    name = "Computer Mania BD"
    base_url = "https://computermania.com.bd"

    CARD_SELECTORS = (
        "ul.products li.product",
        ".products .product",
        ".product-grid-item",
        ".wc-block-grid__product",
    )

    IMAGE_SELECTOR = (
        ".woocommerce-product-gallery__image img, .flex-control-thumbs img, "
        ".woocommerce-product-gallery img"
    )
    SPEC_TABLE = (
        "table.woocommerce-product-attributes tr, .shop_attributes tr, table.table tr",
        "th, td:first-child",
        "td, td:last-child",
    )
    SPEC_BULLETS = (
        ".woocommerce-product-details__short-description li, .short-description li"
    )

    DETAIL_SELECTORS = {
        "name": ("h1.product_title", ".product-title", "h1"),
        "price": ("p.price ins .amount", "p.price .amount", ".price ins", ".price"),
        "old_price": ("p.price del .amount", "del .amount", "del"),
        "price_block": ("p.price", ".price"),
        "stock": (".stock", ".availability", "p.stock"),
        "brand": (".posted_in a", ".product_meta .brand a", "[itemprop=brand]"),
        "sku": (".sku", "span.sku", "[itemprop=sku]"),
        "rating": ("[itemprop=ratingValue]", ".rating-value"),
        "rating_style": (".star-rating span", ".woocommerce-product-rating span"),
        "reviews": (".woocommerce-review-link", ".rating-count", "[itemprop=reviewCount]"),
        "description": (
            "#tab-description",
            ".woocommerce-Tabs-panel--description",
            ".product-description",
        ),
        "badge": (".onsale", ".product-label", ".badge"),
    }

    def listing_url(self, url_path: str, page: int) -> tuple[str, dict[str, str] | None]:
        # WooCommerce paginates by path segment rather than a query parameter.
        if page > 1:
            return f"{url_path.rstrip('/')}/page/{page}/", None
        return url_path, None

    async def fetch_listing(self, url_path: str, page: int) -> FetchResult:
        path, params = self.listing_url(url_path, page)
        if page > 1:
            return await self.client.post(path, params=params)
        return await self.client.get(path, params=params)

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
                card, "href", "a.woocommerce-LoopProduct-link", "h2 a", ".product-title a", "a"
            )
            name = first_text(
                card,
                ".woocommerce-loop-product__title",
                "h2",
                ".product-title",
                "h3",
            )
            url = absolutise(href, self.base_url)
            if not url or not name:
                continue

            # Woo renders a discount as <del>old</del><ins>new</ins>.
            price = to_decimal(
                first_text(card, ".price ins .amount", ".price ins", ".price > .amount")
            )
            old_price = to_decimal(first_text(card, ".price del .amount", ".price del", "del"))
            if price is None:
                figures = all_prices(first_text(card, ".price") or "")
                if figures:
                    price = min(figures)
                    if old_price is None and len(figures) > 1 and max(figures) != price:
                        old_price = max(figures)

            image = image_url(
                q(card, "img.attachment-woocommerce_thumbnail, img"), self.base_url
            )

            products.append(
                ScrapedProduct(
                    product_url=url,
                    name=clean_text(name),
                    brand=guess_brand(name),
                    price=price,
                    old_price=old_price,
                    image=image,
                    images=[image] if image else [],
                    badge=first_text(card, ".onsale", ".product-label", ".badge"),
                    stock=normalise_stock(
                        first_text(card, ".stock", ".availability")
                        or ("Out of Stock" if q(card, ".outofstock") else None)
                    ),
                )
            )

        return ListingPage(
            products=products,
            has_next=self._has_next(doc, result.url),
            total_pages=None,
        )

    def _has_next(self, doc, url: str) -> bool:
        """WooCommerce's own next-page marker, then the shared heuristics."""
        if q(doc, "a.next.page-numbers") is not None:
            return True
        match = _PAGE_IN_PATH_RE.search(url)
        page = int(match.group(1)) if match else 1
        return self.has_next_page(doc, page)

    def parse_detail(self, result: FetchResult) -> ScrapedProduct:
        product = self.generic_detail(result)

        # Woo puts the SKU in a dedicated span that often reads "N/A"; drop that
        # rather than exporting a literal "N/A" in the id column.
        if product.external_id and product.external_id.strip().lower() in ("n/a", "na", "-"):
            product.external_id = None

        # ``.posted_in`` is the product *category* list, which Woo stores in the
        # same place a brand would live. Only trust it as a brand when it matches
        # the leading token of the title.
        if product.brand and product.name:
            if not product.name.lower().startswith(product.brand.lower()):
                product.brand = guess_brand(product.name) or product.brand

        return product