"""Ryans Computers (ryans.com) - Laravel storefront, /category/<slug> listings."""
from __future__ import annotations

from app.scrapers.base import BaseScraper, ListingPage, ScrapedProduct
from app.scrapers.http import FetchResult
from app.scrapers.parsing import (
    absolutise,
    clean_text,
    first_attr,
    first_text,
    guess_brand,
    normalise_stock,
    qa,
    specs_from_bullets,
    specs_from_table,
    to_decimal,
    to_int,
    to_rating,
)


class RyansScraper(BaseScraper):
    key = "ryans"
    name = "Ryans Computers"
    base_url = "https://www.ryans.com"

    CARD_SELECTORS = (".category-single-product", ".card.h-100", ".product-card", ".cus-col-2")

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
            href = first_attr(card, "href", "p.card-text a", ".card-body a", "a")
            name = first_text(card, "p.card-text a", ".card-text", "h5", ".product-title")
            url = absolutise(href, self.base_url)
            if not url or not name:
                continue
            image = absolutise(
                first_attr(card, "src", ".image-box img", "img")
                or first_attr(card, "data-src", "img"),
                self.base_url,
            )
            products.append(
                ScrapedProduct(
                    product_url=url,
                    name=clean_text(name),
                    brand=guess_brand(name),
                    price=to_decimal(first_text(card, ".pr-text", ".new-sp-text", ".price")),
                    old_price=to_decimal(first_text(card, ".old-sp-text", "del", ".regular-price")),
                    image=image,
                    images=[image] if image else [],
                    badge=first_text(card, ".stock-status", ".label", ".badge"),
                    stock=normalise_stock(first_text(card, ".stock-status", ".stock")),
                )
            )

        has_next = bool(doc.css_first(".pagination a[rel=next]")) or bool(
            doc.css_first(".pagination .page-item:not(.disabled) a[aria-label=Next]")
        )
        return ListingPage(products=products, has_next=has_next or len(products) > 0)

    def parse_detail(self, result: FetchResult) -> ScrapedProduct:
        doc = self.doc(result)
        name = first_text(doc, "h1.title-detail", "h1", ".product-title") or ""
        specs = specs_from_table(doc, ".table-responsive tr, #specification tr", "td:first-child, th", "td:last-child")
        if not specs:
            specs = specs_from_bullets(doc, ".short-description li, .product-feature li")

        images: list[str] = []
        for node in qa(doc, ".product-image img, .slider-nav img, .gallery img"):
            src = node.attributes.get("data-src") or node.attributes.get("src")
            abs_src = absolutise(src, self.base_url)
            if abs_src and abs_src not in images:
                images.append(abs_src)

        return ScrapedProduct(
            product_url=result.url,
            name=clean_text(name),
            brand=first_text(doc, ".brand-name", "[itemprop=brand]") or guess_brand(name),
            external_id=first_text(doc, ".product-code", "[itemprop=sku]"),
            price=to_decimal(first_text(doc, ".new-sp-text", ".price-new", "[itemprop=price]")),
            old_price=to_decimal(first_text(doc, ".old-sp-text", "del")),
            stock=normalise_stock(first_text(doc, ".stock-status", ".status")),
            rating=to_rating(first_text(doc, "[itemprop=ratingValue]", ".rating")),
            reviews=to_int(first_text(doc, "[itemprop=reviewCount]", ".review-count")),
            images=images,
            image=images[0] if images else None,
            specs=specs,
            description=first_text(doc, "#description", ".product-details-text", ".description"),
        )
