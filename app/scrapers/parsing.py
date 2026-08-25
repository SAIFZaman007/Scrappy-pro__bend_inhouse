# backend/app/scrapers/parsing.py
"""Parsing toolkit shared by every site adapter.

Fixes carried in this rewrite
-----------------------------
1. **``:contains()`` now cannot reach selectolax.** The previous ``techland.py``
   evaluated ``.pagination a:contains('>')``. selectolax is a C extension over
   Lexbor and ``:contains()`` is a jQuery invention, not CSS - passing it does
   not raise, it **segfaults the process**. That killed the arq worker outright
   on every TechLand listing page, which is why that site produced nothing and
   left no Python traceback behind. ``q``/``qa`` now sanitise selectors and log
   loudly instead.

2. **``to_rating`` no longer invents ratings.** The old pattern made the
   "out of 5"/"star" suffix optional, so it matched the first bare digit in any
   string - ``"Product Code: 12345"`` became a 1.0-star rating. It now requires
   a rating-shaped anchor.

3. **``normalise_stock`` no longer defaults to "In Stock".** Any non-empty text
   used to mean in stock, so a selector that accidentally matched a nav label
   marked the whole catalogue available. Unrecognised text is now ``Unknown``,
   which is honest and visible in the export.

4. **``to_decimal`` prefers currency-anchored numbers.** ``"Save: 600৳ 4,300৳"``
   used to yield 600. It now looks for a ৳/Tk/BDT-adjacent figure first and only
   falls back to a bare number when there is no currency marker at all.

Normalisation lives here so a price written "৳ 1,25,000", "Tk. 125000" or
"125,000৳" lands in Postgres as the same Decimal regardless of retailer.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import TypedDict

from selectolax.parser import HTMLParser, Node

from app.core.logging import get_logger

log = get_logger(__name__)

BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_WS_RE = re.compile(r"\s+")
_INT_RE = re.compile(r"\d[\d,]*")

# Currency-anchored first: a number that sits next to ৳ / Tk / BDT is a price.
_CURRENCY_PRICE_RE = re.compile(
    r"(?:৳|Tk\.?|BDT|৳\s*)\s*(\d[\d,\s]*(?:\.\d{1,2})?)"
    r"|(\d[\d,\s]*(?:\.\d{1,2})?)\s*(?:৳|Tk\.?|BDT)",
    re.IGNORECASE,
)
# Fallback for elements that contain only the figure, with the symbol elsewhere.
_BARE_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d{1,2})?")

# Ratings must look like a rating: "4.5", "4.5/5", "4.5 out of 5", "4 stars".
_RATING_RE = re.compile(
    r"(\d(?:\.\d+)?)\s*(?:/\s*5|out\s+of\s+5|stars?\b)", re.IGNORECASE
)
_RATING_BARE_RE = re.compile(r"^\s*([0-5](?:\.\d+)?)\s*$")

_SNIFF_PRICE_RE = re.compile(
    r"(?:৳|Tk\.?|BDT)\s?\d[\d,]{2,}|\d[\d,]{2,}\s?(?:৳|Tk\.?|BDT)", re.IGNORECASE
)

OUT_OF_STOCK_HINTS = (
    "out of stock",
    "stock out",
    "unavailable",
    "sold out",
    "not available",
    "stock nei",
    "নেই",
    "স্টক আউট",
)
PREORDER_HINTS = ("pre order", "pre-order", "preorder", "up coming", "upcoming", "coming soon")
IN_STOCK_HINTS = ("in stock", "available", "স্টকে আছে", "আছে")

# Pseudo-classes selectolax/Lexbor does not implement. ``:contains`` in
# particular does not raise - it segfaults - so this list is a hard guard, not a
# nicety. Extend it if a new crash is ever traced to a selector.
UNSUPPORTED_PSEUDO = (":contains(", ":has(", ":icontains(", ":matches(", ":visible")

# Common BD-market brands, checked before falling back to "first word of title".
KNOWN_BRANDS: frozenset[str] = frozenset(
    {
        "asus", "msi", "gigabyte", "asrock", "acer", "dell", "hp", "lenovo", "apple",
        "samsung", "lg", "sony", "intel", "amd", "nvidia", "corsair", "kingston",
        "adata", "team", "g.skill", "gskill", "crucial", "western digital", "wd",
        "seagate", "toshiba", "transcend", "sandisk", "lexar", "pny", "colorful",
        "zotac", "sapphire", "powercolor", "inno3d", "palit", "galax", "xfx",
        "cooler master", "deepcool", "thermaltake", "nzxt", "antec", "be quiet",
        "lian li", "phanteks", "xigmatek", "gamdias", "value-top", "maxgreen",
        "logitech", "razer", "steelseries", "hyperx", "a4tech", "havit", "fantech",
        "rapoo", "redragon", "keychron", "royal kludge", "aula", "ajazz", "dareu",
        "tp-link", "tenda", "d-link", "mikrotik", "netgear", "ubiquiti", "cudy",
        "mercusys", "totolink", "zyxel", "ruijie", "cisco", "huawei", "hikvision",
        "dahua", "jovision", "uniview", "ezviz", "imou", "zkteco", "tiandy",
        "xiaomi", "redmi", "realme", "oppo", "vivo", "oneplus", "honor", "tecno",
        "infinix", "symphony", "walton", "nokia", "motorola", "google", "canon",
        "nikon", "fujifilm", "panasonic", "epson", "brother", "pantum", "ricoh",
        "sharp", "benq", "viewsonic", "optoma", "philips", "aoc", "koorui",
        "jbl", "bose", "edifier", "microlab", "f&d", "anker", "baseus", "ugreen",
        "oraimo", "joyroom", "hoco", "awei", "qcy", "soundpeats", "earfun",
        "amazfit", "haylou", "kieslect", "boat", "dji", "gopro", "insta360",
        "sjcam", "wacom", "huion", "xp-pen", "veikk", "microsoft", "adobe",
        "gree", "midea", "haier", "hisense", "tcl", "singer", "beko", "whirlpool",
    }
)


# --------------------------------------------------------------------------- #
# Document helpers
# --------------------------------------------------------------------------- #
def parse_html(html: str) -> HTMLParser:
    return HTMLParser(html or "")


def _is_safe_selector(selector: str) -> bool:
    lowered = selector.lower()
    for bad in UNSUPPORTED_PSEUDO:
        if bad in lowered:
            log.error(
                "css.unsupported_selector",
                selector=selector,
                pseudo=bad,
                detail=(
                    "selectolax/Lexbor does not implement this pseudo-class and "
                    "evaluating it can crash the worker process. Selector skipped."
                ),
            )
            return False
    return True


def q(node: HTMLParser | Node | None, selector: str) -> Node | None:
    """``css_first`` with a guard against process-killing selectors."""
    if node is None or not _is_safe_selector(selector):
        return None
    try:
        return node.css_first(selector)
    except Exception as exc:  # noqa: BLE001 - malformed selector must not kill a job
        log.warning("css.query_failed", selector=selector, error=str(exc))
        return None


def qa(node: HTMLParser | Node | None, selector: str) -> list[Node]:
    """``css`` with the same guard."""
    if node is None or not _is_safe_selector(selector):
        return []
    try:
        return node.css(selector)
    except Exception as exc:  # noqa: BLE001
        log.warning("css.query_failed", selector=selector, error=str(exc))
        return []


def first_text(node: HTMLParser | Node | None, *selectors: str) -> str | None:
    """Text of the first selector that matches and is non-empty."""
    if node is None:
        return None
    for selector in selectors:
        found = q(node, selector)
        if found is not None:
            value = clean_text(found.text())
            if value:
                return value
    return None





def first_attr(node: HTMLParser | Node | None, attr: str, *selectors: str) -> str | None:
    if node is None:
        return None
    for selector in selectors:
        found = q(node, selector)
        if found is not None:
            value = (found.attributes.get(attr) or "").strip()
            if value:
                return value
    return None


# Attributes lazy-loading themes hide the real image behind, in priority order.
IMAGE_ATTRS = (
    "data-large_image",
    "data-zoom-image",
    "data-original",
    "data-lazy-src",
    "data-src",
    "data-srcset",
    "srcset",
    "src",
)


def image_url(node: Node | None, base: str) -> str | None:
    """Pull the best available image URL off an ``<img>``, lazy-loading aware.

    The old code checked ``src`` first and only looked at ``data-src`` if ``src``
    was missing entirely - but lazy-loading themes always set ``src`` to a 1px
    placeholder, so it never fell through. Priority is now reversed.
    """
    if node is None:
        return None
    attrs = node.attributes
    for attr in IMAGE_ATTRS:
        raw = (attrs.get(attr) or "").strip()
        if not raw:
            continue
        if attr in ("srcset", "data-srcset"):
            # "url 1x, url2 2x" - take the last (largest) candidate.
            candidates = [c.strip().split(" ")[0] for c in raw.split(",") if c.strip()]
            raw = candidates[-1] if candidates else ""
        if not raw or raw.startswith("data:"):
            continue
        if "placeholder" in raw.lower() or "blank.gif" in raw.lower():
            continue
        return absolutise(raw, base)
    return None


def collect_images(root: HTMLParser | Node | None, selector: str, base: str) -> list[str]:
    images: list[str] = []
    for node in qa(root, selector):
        url = image_url(node, base)
        if url and url not in images:
            images.append(url)
    return images


# --------------------------------------------------------------------------- #
# Value coercion
# --------------------------------------------------------------------------- #
def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\xa0", " ").translate(BENGALI_DIGITS)
    return _WS_RE.sub(" ", value).strip()


def _to_decimal_raw(raw: str) -> Decimal | None:
    cleaned = raw.replace(",", "").replace(" ", "")
    if not cleaned:
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    return amount if amount > 0 else None


def to_decimal(value: str | None) -> Decimal | None:
    """Pull a price out of arbitrary text, preferring currency-anchored figures."""
    if not value:
        return None
    text = clean_text(value)
    if not text:
        return None

    match = _CURRENCY_PRICE_RE.search(text)
    if match:
        raw = match.group(1) or match.group(2) or ""
        amount = _to_decimal_raw(raw)
        if amount is not None:
            return amount

    bare = _BARE_NUMBER_RE.search(text)
    return _to_decimal_raw(bare.group(0)) if bare else None


def all_prices(value: str | None) -> list[Decimal]:
    """Every currency-anchored figure in a blob, in document order.

    Used where one element holds both the new and the old price, which is how
    StarTech renders a discounted item.
    """
    if not value:
        return []
    text = clean_text(value)
    found: list[Decimal] = []
    for match in _CURRENCY_PRICE_RE.finditer(text):
        raw = match.group(1) or match.group(2) or ""
        amount = _to_decimal_raw(raw)
        if amount is not None:
            found.append(amount)
    return found


def to_int(value: str | None) -> int | None:
    if not value:
        return None
    match = _INT_RE.search(clean_text(value))
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def to_rating(value: str | None) -> float | None:
    """Accept "4.5/5", "4.5 out of 5", "4 stars", a bare "4.5", or "width: 90%".

    Deliberately strict: an unanchored digit is not a rating. The previous
    version treated any leading number as one, which quietly populated the
    rating column with product codes.
    """
    if not value:
        return None
    text = clean_text(value)
    if not text:
        return None

    pct = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", text)
    if pct:
        rating = round(float(pct.group(1)) / 20, 2)
        return rating if 0 < rating <= 5 else None

    match = _RATING_RE.search(text) or _RATING_BARE_RE.match(text)
    if not match:
        return None
    rating = float(match.group(1))
    return rating if 0 < rating <= 5 else None


def normalise_stock(value: str | None) -> str:
    """Collapse every retailer's phrasing into four stable values.

    Returns Unknown rather than guessing. A blank stock cell in the export is a
    true statement; a wrong "In Stock" is not.
    """
    text = clean_text(value).lower()
    if not text:
        return "Unknown"
    if any(hint in text for hint in OUT_OF_STOCK_HINTS):
        return "Out of Stock"
    if any(hint in text for hint in PREORDER_HINTS):
        return "Pre Order"
    if any(hint in text for hint in IN_STOCK_HINTS):
        return "In Stock"
    return "Unknown"


def absolutise(url: str | None, base: str) -> str | None:
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith(("data:", "javascript:", "mailto:", "tel:", "#")):
        return None
    return base.rstrip("/") + "/" + url.lstrip("/")


# --------------------------------------------------------------------------- #
# Spec tables
# --------------------------------------------------------------------------- #
def specs_from_table(
    root: HTMLParser | Node | None, row_sel: str, key_sel: str, val_sel: str
) -> dict[str, str]:
    """Turn a spec table into an ordered {label: value} mapping."""
    specs: dict[str, str] = {}
    for row in qa(root, row_sel):
        key_node = q(row, key_sel)
        val_node = q(row, val_sel)
        if not key_node or not val_node:
            continue
        key = clean_text(key_node.text()).rstrip(":").strip()
        val = clean_text(val_node.text())
        if key and val and key.lower() != val.lower():
            specs[key] = val
    return specs


def specs_from_any_table(root: HTMLParser | Node | None) -> dict[str, str]:
    """Generic two-column table reader for when no known selector matches.

    Walks every ``<tr>`` on the page and keeps rows that look like a label/value
    pair: exactly two cells, a short-ish left cell, a non-empty right cell.
    Site-agnostic, so it survives a theme rewrite.
    """
    specs: dict[str, str] = {}
    for row in qa(root, "tr"):
        cells = qa(row, "th, td")
        if len(cells) != 2:
            continue
        key = clean_text(cells[0].text()).rstrip(":").strip()
        val = clean_text(cells[1].text())
        if not key or not val or len(key) > 80 or key.lower() == val.lower():
            continue
        specs.setdefault(key, val)
    return specs


def specs_from_bullets(root: HTMLParser | Node | None, item_sel: str) -> dict[str, str]:
    """Fallback for sites that render key features as a bullet list."""
    specs: dict[str, str] = {}
    for idx, item in enumerate(qa(root, item_sel), start=1):
        text = clean_text(item.text())
        if not text:
            continue
        if ":" in text:
            key, _, val = text.partition(":")
            key, val = key.strip(), val.strip()
            if key and val and len(key) <= 60:
                specs[key] = val
                continue
        specs[f"Feature {idx}"] = text
    return specs


# --------------------------------------------------------------------------- #
# Structural sniffing (backstop when a site's selectors all miss)
# --------------------------------------------------------------------------- #
class SniffedCard(TypedDict):
    url: str
    name: str
    price: Decimal | None
    image: str | None


def sniff_product_cards(
    doc: HTMLParser, base_url: str, min_repeats: int = 4
) -> list[SniffedCard]:
    """Find product cards by shape when a site's own selectors find nothing.

    Looks for a link sitting near price-looking text, groups those by the
    tag+class fingerprint of the nearest ancestor that contained the price, and
    - if one fingerprint repeats often enough to plausibly be a listing grid -
    returns what it found. No knowledge of the site's class names required.

    Trades precision for resilience: it may pick the wrong price within a card,
    or miss a badge a bespoke selector would have caught. What it reliably gets
    right is name and URL. Treat a sniffed hit as "worth reviewing", not
    "confirmed" - the CLI reports it as SNIFF for exactly that reason.
    """
    groups: dict[str, list[tuple[Node, Node, str]]] = {}
    for anchor in qa(doc, "a[href]"):
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

        # Prefer a heading inside the card, then the anchor's title, then the
        # image alt text. All three are far less likely than the anchor's raw
        # text to drag the price or an "Add to cart" label in with them.
        name = ""
        heading = q(container, "h1, h2, h3, h4, h5, h6")
        if heading is not None:
            name = clean_text(heading.text())
        if not name:
            name = clean_text(anchor.attributes.get("title"))
        if not name:
            img = q(container, "img")
            if img is not None:
                name = clean_text(img.attributes.get("alt"))
        if not name:
            name = _SNIFF_PRICE_RE.sub("", clean_text(anchor.text())).strip()
        if not name:
            continue

        prices = all_prices(blob)
        seen_urls.add(url)
        cards.append(
            SniffedCard(
                url=url,
                name=name,
                price=prices[0] if prices else None,
                image=image_url(q(container, "img"), base_url),
            )
        )
    return cards


def guess_brand(name: str | None, known: set[str] | None = None) -> str | None:
    """Best-effort brand from a product title.

    Checks a curated multi-word brand list first (so "Cooler Master MasterBox"
    is not filed under "Cooler"), then falls back to the leading token, which is
    the convention all four storefronts follow.
    """
    text = clean_text(name)
    if not text:
        return None

    lowered = text.lower()
    pool = {b.lower() for b in known} if known else set(KNOWN_BRANDS)
    # Longest match first so "western digital" beats "western".
    for brand in sorted(pool, key=len, reverse=True):
        if lowered.startswith(brand + " ") or lowered == brand:
            return text[: len(brand)].strip()

    first = text.split(" ")[0].strip(",|-()").strip()
    return first or None