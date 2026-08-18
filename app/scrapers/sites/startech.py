"""StarTech (startech.com.bd) - custom PHP storefront, server rendered."""
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


class StarTechScraper(BaseScraper):
    key = "startech"
    name = "StarTech"
    base_url = "https://www.startech.com.bd"

    CARD_SELECTORS = (".p-item", ".product-item", ".short-description")

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
            href = first_attr(card, "href", ".p-item-name a", "h4 a", "a")
            name = first_text(card, ".p-item-name", "h4 a", ".product-name")
            url = absolutise(href, self.base_url)
            if not url or not name:
                continue
            price = to_decimal(first_text(card, ".p-item-price span", ".price-new", ".price"))
            old = to_decimal(first_text(card, ".p-item-price del", ".price-old", "del"))
            image = absolutise(
                first_attr(card, "src", ".p-item-img img", "img")
                or first_attr(card, "data-src", "img"),
                self.base_url,
            )
            badge = first_text(card, ".sticker", ".p-item-sticker", ".label-new")
            products.append(
                ScrapedProduct(
                    product_url=url,
                    name=clean_text(name),
                    brand=guess_brand(name),
                    price=price,
                    old_price=old,
                    image=image,
                    images=[image] if image else [],
                    badge=badge,
                    stock=normalise_stock(first_text(card, ".p-item-stock", ".stock")),
                )
            )

        has_next = bool(doc.css_first("ul.pagination li a[rel=next]")) or bool(
            doc.css_first(".pagination .next:not(.disabled)")
        )
        return ListingPage(products=products, has_next=has_next or len(products) > 0)

    def parse_detail(self, result: FetchResult) -> ScrapedProduct:
        doc = self.doc(result)
        name = first_text(doc, "h1.product-name", "h1") or ""
        specs = specs_from_table(doc, "#specification tr, .data-table tr", "td.text-left, th", "td.text-desc, td:nth-child(2)")
        if not specs:
            specs = specs_from_bullets(doc, ".short-description li, .product-short-description li")

        images: list[str] = []
        for node in qa(doc, ".product-img-holder img, .img-holder img, .additional-images img"):
            src = node.attributes.get("data-src") or node.attributes.get("src")
            abs_src = absolutise(src, self.base_url)
            if abs_src and abs_src not in images:
                images.append(abs_src)

        return ScrapedProduct(
            product_url=result.url,
            name=clean_text(name),
            brand=first_text(doc, ".product-brand a", "[itemprop=brand]") or guess_brand(name),
            external_id=first_text(doc, ".product-code span", "[itemprop=sku]"),
            price=to_decimal(first_text(doc, ".product-price", ".price-new", "[itemprop=price]")),
            old_price=to_decimal(first_text(doc, ".product-regular-price", ".price-old", "del")),
            stock=normalise_stock(first_text(doc, ".product-status span", ".stock", ".status")),
            rating=to_rating(first_text(doc, ".rating-value", "[itemprop=ratingValue]")),
            reviews=to_int(first_text(doc, ".rating-count", "[itemprop=reviewCount]")),
            images=images,
            image=images[0] if images else None,
            specs=specs,
            description=first_text(doc, "#description", ".product-description", ".full-description"),
        )
