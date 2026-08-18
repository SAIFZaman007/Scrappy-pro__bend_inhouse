"""Normalisation is where scraped data most often goes wrong, so it gets tests."""
from decimal import Decimal

from app.scrapers.parsing import (
    guess_brand,
    normalise_stock,
    to_decimal,
    to_int,
    to_rating,
)


def test_price_handles_currency_symbols_and_separators():
    assert to_decimal("৳ 1,25,000") == Decimal("125000")
    assert to_decimal("Tk. 45,500") == Decimal("45500")
    assert to_decimal("125000৳") == Decimal("125000")
    assert to_decimal("Price: 9,999.50") == Decimal("9999.50")


def test_price_rejects_junk():
    assert to_decimal("") is None
    assert to_decimal("Call for price") is None
    assert to_decimal("0") is None


def test_bengali_digits_are_converted():
    assert to_int("১২৩ reviews") == 123


def test_rating_from_text_and_star_width():
    assert to_rating("4.5 out of 5") == 4.5
    assert to_rating("width: 90%") == 4.5
    assert to_rating("nonsense") is None


def test_stock_normalisation_collapses_phrasings():
    assert normalise_stock("Out Of Stock") == "Out of Stock"
    assert normalise_stock("Stock Out") == "Out of Stock"
    assert normalise_stock("Pre-Order") == "Pre Order"
    assert normalise_stock("In Stock") == "In Stock"
    assert normalise_stock(None) == "Unknown"


def test_brand_guess_prefers_known_list():
    known = {"ASUS ROG", "MSI"}
    assert guess_brand("ASUS ROG Strix G16 Gaming Laptop", known) == "ASUS ROG"
    assert guess_brand("Logitech G502 Mouse") == "Logitech"
