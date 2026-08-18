"""Computer Mania BD (computermania.com.bd) - WooCommerce, /page/N/ pagination."""
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


class ComputerManiaScraper(BaseScraper):
    key = "computermania"
    name = "Computer Mania BD"
    base_url = "https://computermania.com.bd"

    CARD_SELECTORS = ("ul.products li.product", ".products .product", ".product-grid-item")

    def listing_url(self, url_path: str, page: int) -> tuple[str, dict[str, str] | None]:
        # WooCommerce paginates by path segment rather than a query parameter.
        if page > 1:
            return f"{url_path.rstrip('/')}/page/{page}/", None
        return url_path, None

    def parse_listing(self, result: FetchResult) -> ListingPage:
        doc = self.doc(result)
        cards = []
        for selector in self.CARD_SELECTORS:
            cards = qa(doc, selector)
            if cards:
                break

        products: list[ScrapedProduct] = []
        for card in cards:
            href = first_attr(card, "href", "a.woocommerce-LoopProduct-link", "h2 a", "a")
            name = first_text(card, ".woocommerce-loop-product__title", "h2", ".product-title")
            url = absolutise(href, self.base_url)
            if not url or not name:
                continue
            image = absolutise(
                first_attr(card, "src", "img.attachment-woocommerce_thumbnail", "img")
                or first_attr(card, "data-src", "img"),
                self.base_url,
            )
            # Woo renders a discount as <del>old</del><ins>new</ins>.
            new_price = first_text(card, ".price ins .amount", ".price > .amount", ".price")
            products.append(
                ScrapedProduct(
                    product_url=url,
                    name=clean_text(name),
                    brand=guess_brand(name),
                    price=to_decimal(new_price),
                    old_price=to_decimal(first_text(card, ".price del .amount", "del")),
                    image=image,
                    images=[image] if image else [],
                    badge=first_text(card, ".onsale", ".product-label", ".badge"),
                    stock=normalise_stock(first_text(card, ".stock", ".availability")),
                )
            )

        has_next = bool(doc.css_first("a.next.page-numbers"))
        return ListingPage(products=products, has_next=has_next)

    def parse_detail(self, result: FetchResult) -> ScrapedProduct:
        doc = self.doc(result)
        name = first_text(doc, "h1.product_title", "h1") or ""
        specs = specs_from_table(
            doc, "table.woocommerce-product-attributes tr, .shop_attributes tr", "th", "td"
        )
        if not specs:
            specs = specs_from_bullets(doc, ".woocommerce-product-details__short-description li")

        images: list[str] = []
        for node in qa(doc, ".woocommerce-product-gallery__image img, .flex-control-thumbs img"):
            src = (
                node.attributes.get("data-large_image")
                or node.attributes.get("data-src")
                or node.attributes.get("src")
            )
            abs_src = absolutise(src, self.base_url)
            if abs_src and abs_src not in images:
                images.append(abs_src)

        return ScrapedProduct(
            product_url=result.url,
            name=clean_text(name),
            brand=first_text(doc, ".posted_in a", "[itemprop=brand]") or guess_brand(name),
            external_id=first_text(doc, ".sku", "[itemprop=sku]"),
            price=to_decimal(first_text(doc, "p.price ins .amount", "p.price .amount")),
            old_price=to_decimal(first_text(doc, "p.price del .amount", "del")),
            stock=normalise_stock(first_text(doc, ".stock", ".availability")),
            rating=to_rating(
                first_attr(doc, "style", ".star-rating span")
                or first_text(doc, ".woocommerce-product-rating .rating")
            ),
            reviews=to_int(first_text(doc, ".woocommerce-review-link", ".rating-count")),
            images=images,
            image=images[0] if images else None,
            specs=specs,
            description=first_text(
                doc, "#tab-description", ".woocommerce-Tabs-panel--description"
            ),
        )
