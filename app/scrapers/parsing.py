# backend/app/scrapers/parsing.py
"""Small parsing toolkit shared by every site adapter.

Keeping normalisation here means a price written as "৳ 1,25,000", "Tk. 125000" or
"125,000৳" lands in the database as the same Decimal, regardless of retailer.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import TypedDict

from selectolax.parser import HTMLParser, Node

BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_PRICE_RE = re.compile(r"\d[\d,\s]*(?:\.\d+)?")
_RATING_RE = re.compile(r"(\d(?:\.\d)?)\s*(?:/\s*5|out of 5|star)?", re.I)
_INT_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")
# Deliberately requires a currency marker (৳ / Tk / BDT) next to the digits, not
# just "a big number" - that keeps it from matching phone numbers, SKUs, or star
# ratings while still catching every phrasing these four sites actually use.
_SNIFF_PRICE_RE = re.compile(
    r"(?:৳|Tk\.?|BDT)\s?\d[\d,]{2,}|\d[\d,]{2,}\s?(?:৳|Tk\.?|BDT)", re.IGNORECASE
)

OUT_OF_STOCK_HINTS = ("out of stock", "stock out", "unavailable", "sold out", "নেই")
PREORDER_HINTS = ("pre order", "pre-order", "preorder", "upcoming")


def parse_html(html: str) -> HTMLParser:
    return HTMLParser(html)


def q(node: HTMLParser | Node, selector: str) -> Node | None:
    return node.css_first(selector)


def qa(node: HTMLParser | Node, selector: str) -> list[Node]:
    return node.css(selector)


def first_text(node: HTMLParser | Node | None, *selectors: str) -> str | None:
    """Return the text of the first selector that matches and is non-empty."""
    if node is None:
        return None
    for selector in selectors:
        found = node.css_first(selector)
        if found is not None:
            value = clean_text(found.text())
            if value:
                return value
    return None


def first_attr(node: HTMLParser | Node | None, attr: str, *selectors: str) -> str | None:
    if node is None:
        return None
    for selector in selectors:
        found = node.css_first(selector)
        if found is not None:
            value = (found.attributes.get(attr) or "").strip()
            if value:
                return value
    return None


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\xa0", " ").translate(BENGALI_DIGITS)
    return _WS_RE.sub(" ", value).strip()


def to_decimal(value: str | None) -> Decimal | None:
    """Pull the first currency-looking number out of arbitrary text."""
    if not value:
        return None
    text = clean_text(value)
    match = _PRICE_RE.search(text)
    if not match:
        return None
    raw = match.group(0).replace(",", "").replace(" ", "")
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def to_int(value: str | None) -> int | None:
    if not value:
        return None
    match = _INT_RE.search(clean_text(value))
    return int(match.group(0)) if match else None


def to_rating(value: str | None) -> float | None:
    """Accept "4.5", "4.5 out of 5", or a star-width style like "width: 90%"."""
    if not value:
        return None
    text = clean_text(value)
    pct = re.search(r"(\d{1,3})\s*%", text)
    if pct:
        return round(int(pct.group(1)) / 20, 2)
    match = _RATING_RE.search(text)
    if not match:
        return None
    rating = float(match.group(1))
    return rating if 0 < rating <= 5 else None


def normalise_stock(value: str | None) -> str:
    """Collapse every retailer's phrasing into three stable values."""
    text = clean_text(value).lower()
    if not text:
        return "Unknown"
    if any(h in text for h in OUT_OF_STOCK_HINTS):
        return "Out of Stock"
    if any(h in text for h in PREORDER_HINTS):
        return "Pre Order"
    return "In Stock"


def absolutise(url: str | None, base: str) -> str | None:
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    return base.rstrip("/") + "/" + url.lstrip("/")


def specs_from_table(root: HTMLParser | Node | None, row_sel: str, key_sel: str, val_sel: str) -> dict[str, str]:
    """Turn a spec table into an ordered {label: value} mapping."""
    specs: dict[str, str] = {}
    if root is None:
        return specs
    for row in root.css(row_sel):
        key_node = row.css_first(key_sel)
        val_node = row.css_first(val_sel)
        if not key_node or not val_node:
            continue
        key = clean_text(key_node.text()).rstrip(":")
        val = clean_text(val_node.text())
        if key and val and key.lower() != val.lower():
            specs[key] = val
    return specs


def specs_from_bullets(root: HTMLParser | Node | None, item_sel: str) -> dict[str, str]:
    """Fallback for sites that render key features as a bullet list."""
    specs: dict[str, str] = {}
    if root is None:
        return specs
    for idx, item in enumerate(root.css(item_sel), start=1):
        text = clean_text(item.text())
        if not text:
            continue
        if ":" in text:
            key, _, val = text.partition(":")
            key, val = key.strip(), val.strip()
            if key and val:
                specs[key] = val
                continue
        specs[f"Feature {idx}"] = text
    return specs


class SniffedCard(TypedDict):
    url: str
    name: str
    price: Decimal | None
    image: str | None


def sniff_product_cards(
    doc: HTMLParser, base_url: str, min_repeats: int = 4
) -> list[SniffedCard]:
    """Find product cards by shape when a site's own selectors find nothing.

    Every one of the four target sites eventually gets a bespoke adapter with
    hand-picked CSS selectors, but those selectors are only ever as good as the
    markup they were checked against - a redesign, or simply a wrong guess, and a
    category silently returns zero products even though the page loaded fine.

    This is the backstop for that case: it looks for a link sitting near a
    price-looking piece of text, groups those by the tag+class combination of
    whichever nearby ancestor first contained the price, and - if one such
    combination repeats often enough to plausibly be a listing grid - returns the
    products it found that way. No knowledge of the site's actual class names is
    required, which is exactly the point.

    This trades precision for resilience. It will occasionally pick up the wrong
    price within a card (an old/new price pair, say) or miss a rating or badge
    that a bespoke selector would have caught. What it reliably gets right is the
    product name and URL, which is the difference between a category silently
    contributing nothing and one that contributes real, if slightly rough, rows.
    Callers should treat a sniffed hit as "worth reviewing", not "confirmed".
    """
    groups: dict[str, list[tuple[Node, Node, str]]] = {}
    for anchor in doc.css("a[href]"):
        href = (anchor.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        container: Node | None = anchor
        blob = ""
        matched = False
        for _ in range(4):
            container = container.parent
            if container is None:
                break
            blob = clean_text(container.text(deep=True))
            if _SNIFF_PRICE_RE.search(blob):
                matched = True
                break
        if not matched or container is None:
            continue

        classes = (container.attributes.get("class") or "").split()
        if not classes:
            continue
        fingerprint = f"{container.tag}.{'.'.join(sorted(classes))}"
        groups.setdefault(fingerprint, []).append((anchor, container, blob))

    if not groups:
        return []
    _, entries = max(groups.items(), key=lambda kv: len(kv[1]))
    if len(entries) < min_repeats:
        return []

    cards: list[SniffedCard] = []
    seen_urls: set[str] = set()
    for anchor, container, blob in entries:
        url = absolutise(anchor.attributes.get("href"), base_url)
        if not url or url in seen_urls:
            continue

        # Prefer a heading inside the card, then the anchor's own title, then the
        # image's alt text - all of these are far less likely to drag the price
        # or a "Add to cart" button label in with them than the anchor's raw text
        # is, since it's common for one <a> to wrap the whole card's contents.
        name = ""
        heading = container.css_first("h1, h2, h3, h4, h5, h6")
        if heading is not None:
            name = clean_text(heading.text())
        if not name:
            name = clean_text(anchor.attributes.get("title"))
        if not name:
            img = container.css_first("img")
            if img is not None:
                name = clean_text(img.attributes.get("alt"))
        if not name:
            # Last resort: the anchor's own text, with any price-looking substring
            # stripped out so the name doesn't end up with the price glued on.
            name = _SNIFF_PRICE_RE.sub("", clean_text(anchor.text())).strip()
        if not name:
            continue

        image_node = container.css_first("img")
        image = (
            absolutise(
                image_node.attributes.get("data-src") or image_node.attributes.get("src"),
                base_url,
            )
            if image_node is not None
            else None
        )

        price_match = _SNIFF_PRICE_RE.search(blob)
        seen_urls.add(url)
        cards.append(
            SniffedCard(
                url=url,
                name=name,
                price=to_decimal(price_match.group(0)) if price_match else None,
                image=image,
            )
        )
    return cards


def guess_brand(name: str, known: set[str] | None = None) -> str | None:
    """Brand is usually the first token of a product title on these storefronts."""
    text = clean_text(name)
    if not text:
        return None
    if known:
        lowered = text.lower()
        for brand in known:
            if lowered.startswith(brand.lower() + " "):
                return brand
    return text.split(" ")[0].strip(",|-") or None