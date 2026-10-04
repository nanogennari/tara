import pytest

from app import quantity


@pytest.mark.parametrize("raw,expected", [
    ("~75", {"type": "count", "value": 75.0, "estimated": True}),
    ("~2,000", {"type": "count", "value": 2000.0, "estimated": True}),
    ("3 rolls", {"type": "pack", "value": 3.0, "unit": "rolls", "estimated": False}),
    ("90 tabs", {"type": "pack", "value": 90.0, "unit": "tabs"}),
    ("10 mL", {"type": "volume", "value": 10.0, "unit": "mL"}),
    ("30+", {"type": "count", "value": 30.0, "estimated": True}),
    ("1 + 1", {"type": "text", "text": "1 + 1"}),
    ("?", {"type": "text", "text": "?"}),
    ("1 tube, 14.2 g (0.5 oz)", {"type": "text"}),
    (2.0, {"type": "count", "value": 2.0}),
    ("1.5 kg", {"type": "weight", "value": 1.5, "unit": "kg"}),
])
def test_parse(raw, expected):
    q = quantity.parse(raw)
    for k, v in expected.items():
        assert q[k] == v, (raw, q)


def test_format_roundtrip():
    assert quantity.format(quantity.parse("~75")) == "~75"
    assert quantity.format(quantity.parse("3 rolls")) == "3 rolls"
    assert quantity.format(quantity.parse("~2,000")) == "~2000"
    assert quantity.format(quantity.parse("1 + 1")) == "1 + 1"


def test_clean_coerces_bad_units():
    q = quantity.clean({"type": "weight", "value": "2,5", "unit": "kilos"})
    assert q == {"type": "weight", "value": 2.5, "unit": "kg", "text": "", "estimated": False}
    q = quantity.clean({"type": "count", "value": 3, "unit": "kg"})
    assert q["type"] == "weight"
    q = quantity.clean({"type": "pack", "value": 2, "unit": "drawers"})
    assert q["unit"] == "drawers"


def test_singular_unit_display():
    assert quantity.format({"type": "pack", "value": 1, "unit": "bottles"}) == "1 bottle"
    assert quantity.format({"type": "pack", "value": 2, "unit": "bottles"}) == "2 bottles"
    # Parsing the singular display round-trips
    assert quantity.parse("1 bottle")["unit"] == "bottles"
