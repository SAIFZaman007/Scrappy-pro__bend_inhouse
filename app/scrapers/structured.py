# backend/app/scrapers/structured.py
"""Structured-data extraction: JSON-LD, microdata, OpenGraph.

Why this exists
---------------
CSS class names are the *least* stable thing on an e-commerce page. A theme
update renames ``.p-item-price`` and a category silently starts returning rows
with a blank price - which is exactly the failure mode this project has been
fighting. Structured data is the opposite: StarTech, TechLand and Ryans all emit
schema.org ``Product`` markup because Google Shopping requires it, and they have
a commercial incentive never to break it.

So the extraction order everywhere in this codebase is:

    JSON-LD  ->  microdata  ->  OpenGraph  ->  site CSS selectors  ->  heuristics

Site selectors are still there, and still valuable - they pick up things
schema.org has no field for, like a "Save 3,000৳" badge. But they are now the
*enrichment* layer, not the foundation. A theme change degrades a run from
excellent to good, instead of from working to zero.

Everything here is defensive. Real-world JSON-LD contains trailing commas,
HTML entities, unescaped newlines inside strings, and ``@graph`` wrappers three
levels deep. A parse failure returns nothing; it never raises.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

from selectolax.parser import HTMLParser

# schema.org availability URLs -> our three stable stock values.
AVAILABILITY_MAP: dict[str, str] = {
    "instock": "In Stock",
    "instoreonly": "In Stock",
    "onlineonly": "In Stock",
    "limitedavailability": "In Stock",
    "outofstock": "Out of Stock",
    "soldout": "Out of Stock",
    "discontinued": "Out of Stock",
    "preorder": "Pre Order",
    "presale": "Pre Order",
    "backorder": "Pre Order",
}

PRODUCT_TYPES = {"product", "individualproduct", "productmodel", "vehicle", "book"}

_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


# --------------------------------------------------------------------------- #
# JSON-LD
# --------------------------------------------------------------------------- #
def _loads_lenient(raw: str) -> Any:
    """Parse JSON that real websites emit, not JSON from a spec."""
    text = _CONTROL_CHARS_RE.sub("", raw.strip())
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Second pass: strip trailing commas, the single most common real-world defect.
    try:
        return json.loads(_TRAILING_COMMA_RE.sub(r"\1", text))
    except json.JSONDecodeError:
        return None


def _flatten(node: Any) -> list[dict]:
    """Walk arrays and ``@graph`` wrappers into a flat list of objects."""
    out: list[dict] = []
    if isinstance(node, list):
        for item in node:
            out.extend(_flatten(item))
    elif isinstance(node, dict):
        out.append(node)
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                out.extend(_flatten(node[key]))
    return out


def extract_jsonld(doc: HTMLParser) -> list[dict]:
    """Every JSON-LD object on the page, flattened."""
    blocks: list[dict] = []
    for script in doc.css('script[type="application/ld+json"]'):
        parsed = _loads_lenient(script.text() or "")
        if parsed is not None:
            blocks.extend(_flatten(parsed))
    return blocks


def _types_of(node: dict) -> set[str]:
    raw = node.get("@type") or node.get("type") or ""
    values = raw if isinstance(raw, list) else [raw]
    return {str(v).split("/")[-1].lower() for v in values if v}


def find_product_node(nodes: list[dict]) -> dict | None:
    """The first object that describes a single product."""
    for node in nodes:
        if _types_of(node) & PRODUCT_TYPES:
            return node
    return None




# --------------------------------------------------------------------------- #
# Field coercion
# --------------------------------------------------------------------------- #
def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _as_text(value: Any) -> str | None:
    value = _first(value)
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("name", "@value", "value", "text"):
            if key in value:
                return str(value[key]).strip() or None
        return None
    text = str(value).strip()
    return text or None


def _as_decimal(value: Any) -> Decimal | None:
    value = _first(value)
    if value is None:
        return None
    if isinstance(value, dict):
        value = value.get("price") or value.get("@value") or value.get("value")
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(value))
    if not cleaned or cleaned.count(".") > 1:
        return None
    try:
        amount = Decimal(cleaned)
    except Exception:  # noqa: BLE001
        return None
    return amount if amount > 0 else None


def _as_int(value: Any) -> int | None:
    value = _first(value)
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def _as_float(value: Any) -> float | None:
    value = _first(value)
    if value is None:
        return None
    match = re.search(r"\d+(?:\.\d+)?", str(value))
    if not match:
        return None
    number = float(match.group(0))
    return number if 0 < number <= 5 else None


def _images_from(value: Any) -> list[str]:
    urls: list[str] = []
    items = value if isinstance(value, list) else [value]
    for item in items:
        if isinstance(item, dict):
            item = item.get("url") or item.get("contentUrl") or item.get("@id")
        if item and isinstance(item, str):
            cleaned = item.strip()
            if cleaned and cleaned not in urls:
                urls.append(cleaned)
    return urls


def normalise_availability(value: Any) -> str:
    text = _as_text(value) or ""
    key = text.split("/")[-1].split("#")[-1].strip().lower().replace(" ", "").replace("_", "")
    return AVAILABILITY_MAP.get(key, "Unknown")


# --------------------------------------------------------------------------- #
# The public shape
# --------------------------------------------------------------------------- #
def product_from_jsonld(node: dict) -> dict[str, Any]:
    """Map a schema.org Product object onto our ``ScrapedProduct`` field names.

    Returns only the keys it is confident about, so callers can use
    ``dict.get`` and let lower-priority layers fill the rest.
    """
    data: dict[str, Any] = {}

    if name := _as_text(node.get("name")):
        data["name"] = name
    if brand := _as_text(node.get("brand") or node.get("manufacturer")):
        data["brand"] = brand
    if sku := _as_text(node.get("sku") or node.get("mpn") or node.get("productID")):
        data["external_id"] = sku
    if description := _as_text(node.get("description")):
        data["description"] = description

    images = _images_from(node.get("image"))
    if images:
        data["images"] = images
        data["image"] = images[0]

    # offers may be a single Offer, a list, or an AggregateOffer.
    offers = node.get("offers")
    offer = _first(offers)
    if isinstance(offer, dict):
        if "offers" in offer:  # AggregateOffer wrapping real offers
            inner = _first(offer["offers"])
            if isinstance(inner, dict):
                offer = {**offer, **inner}
        price = _as_decimal(
            offer.get("price")
            or offer.get("lowPrice")
            or offer.get("priceSpecification")
        )
        if price is not None:
            data["price"] = price
        if currency := _as_text(offer.get("priceCurrency")):
            data["currency"] = currency
        stock = normalise_availability(offer.get("availability"))
        if stock != "Unknown":
            data["stock"] = stock

    rating_node = node.get("aggregateRating")
    if isinstance(rating_node, dict):
        if (rating := _as_float(rating_node.get("ratingValue"))) is not None:
            data["rating"] = rating
        reviews = _as_int(
            rating_node.get("reviewCount") or rating_node.get("ratingCount")
        )
        if reviews is not None:
            data["reviews"] = reviews

    specs = _specs_from_additional_property(node.get("additionalProperty"))
    if specs:
        data["specs"] = specs

    return data


def _specs_from_additional_property(value: Any) -> dict[str, str]:
    specs: dict[str, str] = {}
    items = value if isinstance(value, list) else [value]
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _as_text(item.get("name"))
        val = _as_text(item.get("value"))
        if key and val:
            specs[key] = val
    return specs


# --------------------------------------------------------------------------- #
# Microdata + OpenGraph fallbacks
# --------------------------------------------------------------------------- #
def product_from_microdata(doc: HTMLParser) -> dict[str, Any]:
    """Read ``itemprop`` attributes for sites that use microdata, not JSON-LD."""
    data: dict[str, Any] = {}

    def prop(name: str) -> str | None:
        node = doc.css_first(f'[itemprop="{name}"]')
        if node is None:
            return None
        attrs = node.attributes
        for attr in ("content", "datetime", "href", "src"):
            if attrs.get(attr):
                return str(attrs[attr]).strip()
        text = (node.text() or "").strip()
        return text or None

    if name := prop("name"):
        data["name"] = name
    if brand := prop("brand"):
        data["brand"] = brand
    if sku := prop("sku"):
        data["external_id"] = sku
    if description := prop("description"):
        data["description"] = description
    if (price := _as_decimal(prop("price"))) is not None:
        data["price"] = price
    if currency := prop("priceCurrency"):
        data["currency"] = currency
    if availability := prop("availability"):
        stock = normalise_availability(availability)
        if stock != "Unknown":
            data["stock"] = stock
    if (rating := _as_float(prop("ratingValue"))) is not None:
        data["rating"] = rating
    if (reviews := _as_int(prop("reviewCount") or prop("ratingCount"))) is not None:
        data["reviews"] = reviews
    if image := prop("image"):
        data["image"] = image
        data["images"] = [image]

    return data


def product_from_opengraph(doc: HTMLParser) -> dict[str, Any]:
    """Last structured resort. Almost every storefront emits at least og:title."""
    data: dict[str, Any] = {}

    def meta(*names: str) -> str | None:
        for name in names:
            node = doc.css_first(f'meta[property="{name}"]') or doc.css_first(
                f'meta[name="{name}"]'
            )
            if node is not None:
                value = (node.attributes.get("content") or "").strip()
                if value:
                    return value
        return None

    if title := meta("og:title", "twitter:title"):
        data["name"] = title
    if description := meta("og:description", "description", "twitter:description"):
        data["description"] = description
    if image := meta("og:image", "og:image:secure_url", "twitter:image"):
        data["image"] = image
        data["images"] = [image]
    if (price := _as_decimal(meta("product:price:amount", "og:price:amount"))) is not None:
        data["price"] = price
    if currency := meta("product:price:currency", "og:price:currency"):
        data["currency"] = currency
    if availability := meta("product:availability", "og:availability"):
        stock = normalise_availability(availability)
        if stock != "Unknown":
            data["stock"] = stock
    if brand := meta("product:brand", "og:brand"):
        data["brand"] = brand

    return data


def extract_structured_product(doc: HTMLParser) -> dict[str, Any]:
    """Merge all three structured sources, highest confidence first.

    JSON-LD wins over microdata, which wins over OpenGraph. Nothing here touches
    site-specific CSS - that layer runs afterwards in ``BaseScraper``.
    """
    merged: dict[str, Any] = {}
    for extractor in (
        lambda: product_from_opengraph(doc),
        lambda: product_from_microdata(doc),
        lambda: (
            product_from_jsonld(node)
            if (node := find_product_node(extract_jsonld(doc)))
            else {}
        ),
    ):
        try:
            merged.update({k: v for k, v in extractor().items() if v not in (None, "", [], {})})
        except Exception:  # noqa: BLE001 - one bad source must not lose the others
            continue
    return merged