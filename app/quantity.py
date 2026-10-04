"""Quantity types, units, parsing and formatting.

A quantity is stored as (type, value, unit, text, estimated):
  count  -> value=75, unit=""            "~75"
  weight -> value=1.2, unit="kg"         "~1.2 kg"
  pack   -> value=3, unit="rolls"        "3 rolls"
  text   -> text="1 + 1" (free form, value ignored)
"""
import re

QTY_TYPES = {
    "count": {"label": "Count", "units": ["", "pcs", "units", "pairs"]},
    "weight": {"label": "Weight", "units": ["g", "kg", "mg", "oz", "lb"]},
    "volume": {"label": "Volume", "units": ["mL", "L", "fl oz", "gal"]},
    "length": {"label": "Length", "units": ["cm", "m", "mm", "in", "ft"]},
    "pack": {
        "label": "Pack / container",
        "units": ["packs", "boxes", "bags", "sets", "rolls", "bottles", "tubes",
                  "sheets", "bundles", "tabs", "caps", "caplets", "kits", "cases"],
    },
    "text": {"label": "Free text", "units": []},
}

_UNIT_TO_TYPE = {}
for _t, _d in QTY_TYPES.items():
    for _u in _d["units"]:
        if _u:
            _UNIT_TO_TYPE[_u.lower()] = _t
# Common singular / alias forms
_ALIASES = {
    "pc": "pcs", "piece": "pcs", "pieces": "pcs", "unit": "units", "pair": "pairs",
    "gram": "g", "grams": "g", "kilo": "kg", "kilos": "kg", "ml": "mL", "l": "L",
    "liter": "L", "liters": "L", "litre": "L", "litres": "L",
    "pack": "packs", "box": "boxes", "bag": "bags", "set": "sets", "roll": "rolls",
    "bottle": "bottles", "tube": "tubes", "sheet": "sheets", "bundle": "bundles",
    "tab": "tabs", "tablet": "tabs", "tablets": "tabs", "cap": "caps", "capsule": "caps",
    "capsules": "caps", "caplet": "caplets", "kit": "kits", "case": "cases",
}

_NUM_RE = re.compile(
    r"^\s*(?P<est>~|≈|approx\.?\s*|ca\.?\s*)?(?P<num>\d{1,3}(?:,\d{3})+|\d+(?:[.,]\d+)?)"
    r"\s*(?P<plus>\+)?\s*(?P<unit>[A-Za-z][A-Za-z .]*?)?\s*(?P<est2>\(est(?:imated)?\.?\)|~|≈)?\s*$",
    re.IGNORECASE,
)


# Display "1 bottle" rather than "1 bottles"
_SINGULAR = {"pcs": "pc", "units": "unit", "pairs": "pair", "packs": "pack", "boxes": "box", "bags": "bag",
             "sets": "set", "rolls": "roll", "bottles": "bottle", "tubes": "tube", "sheets": "sheet",
             "bundles": "bundle", "tabs": "tab", "caps": "cap", "caplets": "caplet", "kits": "kit", "cases": "case"}


def normalize_unit(unit: str | None) -> str:
    if not unit:
        return ""
    u = unit.strip()
    low = u.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    for known in _UNIT_TO_TYPE:
        if known == low:
            # return canonical casing
            for t in QTY_TYPES.values():
                for cu in t["units"]:
                    if cu.lower() == low:
                        return cu
    return u


def type_for_unit(unit: str) -> str | None:
    if not unit:
        return "count"
    return _UNIT_TO_TYPE.get(unit.lower())


def empty() -> dict:
    return {"type": "count", "value": None, "unit": "", "text": "", "estimated": False}


def parse(raw) -> dict:
    """Parse a free-form quantity ("~75", "3 rolls", "1 + 1", 2.0) into a dict."""
    q = empty()
    if raw is None:
        return q
    if isinstance(raw, bool):
        raw = int(raw)
    if isinstance(raw, (int, float)):
        q["value"] = float(raw)
        return q
    s = str(raw).strip()
    if not s:
        return q
    m = _NUM_RE.match(s)
    if m:
        num = m.group("num")
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", num):
            num = num.replace(",", "")
        else:
            num = num.replace(",", ".")
        unit = normalize_unit(m.group("unit"))
        qtype = type_for_unit(unit)
        if qtype is not None:
            q["value"] = float(num)
            q["unit"] = unit
            q["type"] = qtype
            q["estimated"] = bool(m.group("est") or m.group("est2") or m.group("plus"))
            return q
    q["type"] = "text"
    q["text"] = s
    q["estimated"] = s.startswith("~")
    return q


def _fmt_num(v: float) -> str:
    if v is None:
        return ""
    if float(v).is_integer():
        v = int(v)
        return f"{v:,}" if abs(v) >= 10000 else str(v)
    return f"{v:g}"


def format(q: dict) -> str:
    """Human display of a quantity dict."""
    if not q:
        return ""
    if q.get("type") == "text":
        return q.get("text") or ""
    v = q.get("value")
    if v is None:
        return ""
    s = _fmt_num(v)
    unit = q.get("unit") or ""
    if v == 1 and unit in _SINGULAR:
        unit = _SINGULAR[unit]
    if unit:
        s = f"{s} {unit}"
    if q.get("estimated"):
        s = "~" + s
    return s


def clean(data: dict | None) -> dict:
    """Validate and coerce an incoming quantity dict (from API or AI)."""
    q = empty()
    if not isinstance(data, dict):
        return parse(data)
    qtype = data.get("type") or "count"
    if qtype not in QTY_TYPES:
        qtype = "text" if data.get("text") else "count"
    q["type"] = qtype
    q["estimated"] = bool(data.get("estimated"))
    if qtype == "text":
        q["text"] = str(data.get("text") or data.get("value") or "").strip()[:200]
        return q
    val = data.get("value")
    if val in (None, ""):
        q["value"] = None
    else:
        try:
            q["value"] = float(str(val).replace(",", "."))
        except ValueError:
            return {**empty(), "type": "text", "text": str(val)[:200], "estimated": q["estimated"]}
    unit = normalize_unit(data.get("unit"))
    allowed = QTY_TYPES[qtype]["units"]
    # Units outside the known list are allowed for pack (e.g. "drawers"); for others coerce.
    if qtype != "pack" and allowed and unit not in allowed:
        guessed = type_for_unit(unit) if unit else None
        if guessed and guessed != "count":
            q["type"] = guessed
        else:
            unit = allowed[0]
    q["unit"] = unit[:30]
    return q
