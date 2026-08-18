"""The export schema is a contract with downstream consumers. Lock it down."""
from app.services.export import COLUMNS, _format_specs, _join


def test_column_order_is_exactly_the_spec():
    assert COLUMNS == [
        "id", "name", "brand", "category", "sub", "price", "oldPrice", "stock",
        "rating", "reviews", "badge", "image", "images", "specs", "desc",
    ]


def test_specs_flatten_to_label_value_pairs():
    assert _format_specs({"CPU": "Intel i5", "RAM": "16GB"}) == "CPU: Intel i5 | RAM: 16GB"
    assert _format_specs({}) == ""
    assert _format_specs(None) == ""


def test_join_drops_blanks():
    assert _join(["a", "", "b"]) == "a | b"
