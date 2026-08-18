"""TechLand BD (techlandbd.com) - OpenCart, so the classic .product-layout markup."""
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


class TechLandScraper(BaseScraper):
    key = "techland"
    name = "TechLand BD"
    base_url = "https://www.techlandbd.com"

    CARD_SELECTORS = (".product-layout", ".product-thumb", ".product-grid .product")
    PAGE_LIMIT = "100"  # OpenCart honours &limit=, so fewer round trips per category.

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
            href = first_attr(card, "href", ".caption h4 a", ".name a", "h4 a", "a")
            name = first_text(card, ".caption h4 a", ".name a", "h4")
            url = absolutise(href, self.base_url)
            if not url or not name:
                continue
            image = absolutise(
                first_attr(card, "src", ".image img", "img")
                or first_attr(card, "data-src", "img"),
                self.base_url,
            )
            products.append(
                ScrapedProduct(
                    product_url=url,
                    name=clean_text(name),
                    brand=guess_brand(name),
                    price=to_decimal(first_text(card, ".price-new", ".price span.price-new", ".price")),
                    old_price=to_decimal(first_text(card, ".price-old", "del")),
                    image=image,
                    images=[image] if image else [],
                    badge=first_text(card, ".product-label", ".sale", ".badge"),
                    stock=normalise_stock(first_text(card, ".stock", ".product-stock")),
                    rating=to_rating(first_attr(card, "style", ".rating .fa-stack, .rating span")),
                )
            )

        has_next = bool(doc.css_first(".pagination a:contains('>')")) or bool(
            doc.css_first("ul.pagination li a[rel=next]")
        )
        return ListingPage(products=products, has_next=has_next or len(products) > 0)

    def parse_detail(self, result: FetchResult) -> ScrapedProduct:
        doc = self.doc(result)
        name = first_text(doc, "h1.product-title", "h1", ".product-name") or ""
        specs = specs_from_table(doc, "#tab-specification tr, .table-bordered tr", "td:first-child, th", "td:last-child")
        if not specs:
            specs = specs_from_bullets(doc, "#tab-description li, .short-description li")

        images: list[str] = []
        for node in qa(doc, ".thumbnails img, .product-image img, .image-additional img"):
            src = node.attributes.get("data-src") or node.attributes.get("src")
            abs_src = absolutise(src, self.base_url)
            if abs_src and abs_src not in images:
                images.append(abs_src)

        return ScrapedProduct(
            product_url=result.url,
            name=clean_text(name),
            brand=first_text(doc, ".product-manufacturer a", "[itemprop=brand]") or guess_brand(name),
            external_id=first_text(doc, ".product-model", "[itemprop=sku]"),
            price=to_decimal(first_text(doc, ".product-price .price-new", ".price-new", "[itemprop=price]")),
            old_price=to_decimal(first_text(doc, ".price-old", "del")),
            stock=normalise_stock(first_text(doc, ".product-stock", ".stock span")),
            rating=to_rating(first_text(doc, "[itemprop=ratingValue]", ".rating")),
            reviews=to_int(first_text(doc, "[itemprop=reviewCount]", ".review-count")),
            images=images,
            image=images[0] if images else None,
            specs=specs,
            description=first_text(doc, "#tab-description", ".product-description"),
        )
