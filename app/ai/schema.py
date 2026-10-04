"""Per-table JSON schema for AI item proposals, plus validation/normalisation."""
from .. import quantity
from ..services.inventory import coerce_custom

_JSON_TYPES = {
    "text": "string", "longtext": "string", "url": "string", "date": "string",
    "select": "string", "number": "number", "checkbox": "boolean",
}


def _nullable(t: str) -> dict:
    return {"type": [t, "null"]}


def build_schema(table) -> dict:
    """Strict JSON schema (all props required, no extras) — works for OpenAI strict mode,
    Claude structured outputs, Gemini response_json_schema and Ollama `format`."""
    custom_props = {}
    for c in table.custom_columns():
        prop = _nullable(_JSON_TYPES.get(c["type"], "string"))
        desc = c.get("label") or c["key"]
        if c["type"] == "date":
            desc += " (YYYY-MM-DD, or YYYY-MM if only month/year is visible)"
        if c["type"] == "select" and c.get("options"):
            desc += f" (one of: {', '.join(c['options'])})"
        prop["description"] = desc
        custom_props[c["key"]] = prop

    item = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "What the item is"},
            "quantity": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(quantity.QTY_TYPES)},
                    "value": _nullable("number"),
                    "unit": {"type": "string"},
                    "text": {"type": "string"},
                    "estimated": {"type": "boolean"},
                },
                "required": ["type", "value", "unit", "text", "estimated"],
                "additionalProperties": False,
            },
            "observation": {"type": "string"},
            "custom": {
                "type": "object",
                "properties": custom_props,
                "required": list(custom_props),
                "additionalProperties": False,
            },
            "photo_indexes": {"type": "array", "items": {"type": "integer"}},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["description", "quantity", "observation", "custom", "photo_indexes", "confidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": item},
            "notes": {"type": "string", "description": "Anything the user should double-check"},
        },
        "required": ["items", "notes"],
        "additionalProperties": False,
    }


class ProposalError(ValueError):
    pass


def normalise(raw, table, n_photos: int) -> dict:
    """Validate the model output and coerce it into clean item dicts."""
    # Local models sometimes drift from the schema: accept a bare list or another list-valued key.
    if isinstance(raw, list):
        raw = {"items": raw, "notes": ""}
    elif isinstance(raw, dict) and "items" not in raw and any(k in raw for k in ("description", "name", "item")):
        raw = {"items": [raw], "notes": ""}  # a single item object
    if isinstance(raw, dict) and not isinstance(raw.get("items"), list):
        lists = [v for v in raw.values() if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)]
        if len(lists) == 1:
            raw = {**raw, "items": lists[0]}
    if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
        raise ProposalError("The AI response did not contain an item list")
    types = {c["key"]: c["type"] for c in table.custom_columns()}
    options = {c["key"]: c.get("options") or [] for c in table.custom_columns()}
    items = []
    for r in raw["items"]:
        if not isinstance(r, dict):
            continue
        desc = str(r.get("description") or r.get("name") or r.get("item") or "").strip()
        if not desc:
            continue
        custom = {}
        for k, v in (r.get("custom") if isinstance(r.get("custom"), dict) else {}).items():
            if k not in types or v in (None, ""):
                continue
            v = coerce_custom(v, types[k])
            if types[k] == "select" and options[k] and v not in options[k]:
                continue
            if v is not None:
                custom[k] = v
        raw_idx = r.get("photo_indexes") or []
        if isinstance(raw_idx, (int, float)):
            raw_idx = [raw_idx]
        idx = sorted({int(i) for i in raw_idx if isinstance(i, (int, float)) and 0 <= int(i) < n_photos})
        if not idx and n_photos == 1:
            idx = [0]
        conf = r.get("confidence") if r.get("confidence") in ("high", "medium", "low") else "medium"
        items.append({
            "description": desc[:2000],
            "quantity": quantity.clean(r.get("quantity")),
            "observation": str(r.get("observation") or "").strip()[:5000],
            "custom": custom,
            "photo_indexes": idx,
            "confidence": conf,
        })
    return {"items": items, "notes": str(raw.get("notes") or "").strip()[:2000]}
