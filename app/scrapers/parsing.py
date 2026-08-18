"""Small parsing toolkit shared by every site adapter.

Keeping normalisation here means a price written as "৳ 1,25,000", "Tk. 125000" or
"125,000৳" lands in the database as the same Decimal, regardless of retailer.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from selectolax.parser import HTMLParser, Node

BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_PRICE_RE = re.compile(r"\d[\d,\s]*(?:\.\d+)?")
_RATING_RE = re.compile(r"(\d(?:\.\d)?)\s*(?:/\s*5|out of 5|star)?", re.I)
_INT_RE = re.compile(r"\d+")
_WS_RE = re.compile(r"\s+")

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
