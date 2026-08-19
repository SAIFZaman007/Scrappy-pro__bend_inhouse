"""StarTech (startech.com.bd) - OpenCart-derived storefront, fully server rendered.

Verified against the live site: every listing page ships complete product markup
in the initial HTML response. No JavaScript, no XHR, no browser needed. When this
adapter returned nothing it was never a rendering problem - it was the transport
handing the parser Brotli bytes (see ``http.py``).

Two throughput notes:

* The grid honours ``?limit=`` with the same options the "Show:" control offers
  (20/24/48/75/90). Requesting 90 cuts a 122-product category from 7 requests to
  2. That is 5x less load on the origin for identical data - the polite option
  and the fast one are the same option here.
* The footer prints "Showing 1 to 20 of 122 (7 Pages)", which ``BaseScraper``
  reads for an exact stop condition instead of walking until a duplicate shows up.

Card markup, confirmed live::

    <div class="p-item">
      <div class="p-item-img"><a href="/amd-ryzen-3-2200g-processor"><img .../></a></div>
      <div class="p-item-details">
        <h4 class="p-item-name"><a href="...">AMD Ryzen 3 2200G ...</a></h4>
        <div class="short-description"><ul><li>Speed: 3.5GHz...</li></ul></div>
        <div class="p-item-price">
          <span class="price-new">4,300৳</span><span class="price-old">4,900৳</span>
        </div>
      </div>
    </div>

Product URLs are flat root slugs (``/amd-ryzen-3-2200g-processor``), not nested
under the category path.
"""

from __future__ import annotations

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
    specs_from_bullets,
)


class StarTechScraper(BaseScraper):
    key = "startech"
    name = "StarTech"
    base_url = "https://www.startech.com.bd"

    # Ordered by specificity. The first selector that matches wins, so the
    # generic ones only ever run after a theme change breaks the specific one.
    CARD_SELECTORS = (
        ".p-item",
        ".product-item",
        ".product-thumb",
        ".main-content .col-md-3",
    )
    PAGE_LIMIT = "90"  # matches the site's own maximum "Show:" option

    IMAGE_SELECTOR = (
        ".product-img-holder img, .main-img img, .img-holder img, "
        ".additional-images img, .product-image img"
    )
    SPEC_TABLE = (
        "#specification tr, .data-table tr, .product-specification tr, table.table tr",
        "td.text-left, td:first-child, th",
        "td.text-desc, td:last-child",
    )
    SPEC_BULLETS = ".short-description li, .product-short-description li"

    DETAIL_SELECTORS = {
        "name": ("h1.product-name", ".product-short-info h1", "h1"),
        "price": (
            ".product-price",
            ".price-new",
            "td.product-info-data .price-new",
            "[itemprop=price]",
        ),
        "old_price": (".product-regular-price", ".price-old", "del"),
        "price_block": (".product-price-block", ".product-price", "#product-price"),
        "stock": (".product-status span", ".product-status", ".stock", ".status"),
        "brand": (".product-brand a", ".product-brand span", "[itemprop=brand]"),
        "sku": (".product-code span", ".product-code", "[itemprop=sku]"),
        "rating": (".rating-value", "[itemprop=ratingValue]"),
        "rating_style": (".star-rating span", ".rating-stars span"),
        "reviews": (".rating-count", "[itemprop=reviewCount]", ".review-count"),
        "description": ("#description", ".product-description", ".full-description"),
        "badge": (".product-label", ".sticker", ".discount-tag"),
    }

    def listing_url(self, url_path: str, page: int) -> tuple[str, dict[str, str] | None]:
        params: dict[str, str] = {"limit": self.PAGE_LIMIT}
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
            href = first_attr(card, "href", ".p-item-name a", "h4 a", ".p-item-img a", "a")
            name = first_text(card, ".p-item-name", "h4 a", "h4", ".product-name")
            url = absolutise(href, self.base_url)
            if not url or not name:
                continue

            new_price, old_price = self._prices(card)
            image = image_url(q(card, ".p-item-img img, img"), self.base_url)

            # The listing already carries the four-bullet feature list. Capturing
            # it here means a run in `missing_only` detail mode still exports a
            # populated specs column without a second request per product.
            specs = specs_from_bullets(card, ".short-description li, .p-item-desc li")

            products.append(
                ScrapedProduct(
                    product_url=url,
                    name=clean_text(name),
                    brand=guess_brand(name),
                    price=new_price,
                    old_price=old_price,
                    image=image,
                    images=[image] if image else [],
                    badge=first_text(
                        card, ".sticker", ".p-item-sticker", ".label-new", ".discount-tag"
                    ),
                    stock=normalise_stock(first_text(card, ".p-item-stock", ".stock")),
                    specs=specs,
                )
            )

        return ListingPage(
            products=products,
            has_next=self.has_next_page(doc, self._page_of(result.url)),
            total_pages=self.total_pages(doc),
        )

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _prices(card) -> tuple[object | None, object | None]:
        """Return ``(new_price, old_price)`` for one card.

        Tries the explicit new/old spans first. If the theme has collapsed both
        figures into a single element - which some StarTech category templates do
        - falls back to reading every currency figure in the price block: the
        lowest is what you pay, the highest is what it was.
        """
        new_price = None
        old_price = None

        explicit_new = first_text(card, ".p-item-price .price-new", ".price-new", ".p-item-price span")
        explicit_old = first_text(card, ".p-item-price .price-old", ".price-old", "del")

        from app.scrapers.parsing import to_decimal

        if explicit_new:
            new_price = to_decimal(explicit_new)
        if explicit_old:
            old_price = to_decimal(explicit_old)

        if new_price is None or old_price is None:
            blob = first_text(card, ".p-item-price", ".price") or ""
            figures = all_prices(blob)
            if figures:
                if new_price is None:
                    new_price = min(figures)
                if old_price is None and len(figures) > 1:
                    highest = max(figures)
                    old_price = highest if highest != new_price else None

        return new_price, old_price

    @staticmethod
    def _page_of(url: str) -> int:
        from urllib.parse import parse_qs, urlparse

        try:
            return int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
        except (TypeError, ValueError):
            return 1

    def parse_detail(self, result: FetchResult) -> ScrapedProduct:
        """Structured data first, then StarTech's own product-info list.

        StarTech renders code/status/brand as an unordered list of
        ``Label: value`` rows rather than a table, so those need reading by hand
        after the generic pass.
        """
        product = self.generic_detail(result)
        doc = self.doc(result)

        info = self._info_list(doc)
        if not product.external_id:
            product.external_id = info.get("product code") or info.get("model")
        if product.stock == "Unknown":
            product.stock = normalise_stock(info.get("status") or info.get("availability"))
        if not product.brand:
            product.brand = info.get("brand") or guess_brand(product.name)

        # Fold the short-description bullets in as features - they are the only
        # spec data present for accessories and gadgets.
        if bullets := specs_from_bullets(doc, self.SPEC_BULLETS):
            product.specs = {**bullets, **product.specs}

        return product

    @staticmethod
    def _info_list(doc) -> dict[str, str]:
        """Read the ``Label: value`` list StarTech shows beside the gallery."""
        info: dict[str, str] = {}
        for node in qa(doc, ".product-info-list li, .product-info li, ul.product-info-list li"):
            text = clean_text(node.text())
            if ":" not in text:
                continue
            key, _, value = text.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key and value:
                info.setdefault(key, value)
        return info