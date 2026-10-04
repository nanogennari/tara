"""Generic XLSX import: one table per sheet, header auto-detection, embedded images by row.

Two steps so the user can review what will happen:
  analyze(file) -> per-sheet summary (detected name, columns and their mapping, rows, images)
  run(file, folder_id, sheets, mappings) -> creates the tables (mappings: the user's column overrides)
"""
import io
import re
import unicodedata
from datetime import date, datetime

from openpyxl import load_workbook

from .. import quantity
from ..extensions import db
from ..models import CUSTOM_TYPES, InvTable, Item, Photo, default_columns, slugify_key
from . import photos as photo_svc
from .inventory import coerce_custom, get_folder
from .tracking import current_user_id

BUILTIN_ALIASES = {
    "description": {"item", "items", "description", "descricao", "name", "nome", "produto", "product",
                    "article", "artigo", "material", "objeto"},
    "quantity": {"qty", "quantity", "quantidade", "qtd", "qtde", "qt", "count", "amount", "quant"},
    "observation": {"obs", "observation", "observations", "notes", "note", "observacao", "observacoes",
                    "comments", "comment", "comentarios", "remarks"},
    "photos": {"photo", "photos", "foto", "fotos", "image", "images", "imagem", "imagens", "picture"},
}
NAME_KEYS = ("box", "container", "table", "name", "caixa", "tabela")
SUMMARY_KEYS = ("summary", "resumo", "description", "descricao")
_HYPERLINK = re.compile(r'^=HYPERLINK\(\s*"[^"]*"\s*[,;]\s*"([^"]*)"\s*\)\s*$', re.I)
_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


class ImportError_(ValueError):
    pass


def _norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def cell_text(v):
    """Display value of a cell: unwrap HYPERLINK formulas, format dates, trim floats."""
    if v is None:
        return None
    if isinstance(v, str):
        m = _HYPERLINK.match(v.strip())
        if m:
            return m.group(1)
        return v.strip() or None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def _load(data: bytes):
    try:
        return load_workbook(io.BytesIO(data), data_only=False)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises many types
        raise ImportError_("Couldn't read the file. Is it a valid .xlsx workbook?") from exc


def _rows(ws):
    return [[cell_text(c) for c in row] for row in ws.iter_rows(values_only=True)]


def _find_header(rows) -> int | None:
    for i, row in enumerate(rows[:25]):
        filled = [c for c in row if c not in (None, "")]
        if len(filled) >= 2 and all(isinstance(c, str) for c in filled):
            norm = {_norm(c) for c in filled}
            if any(norm & aliases for aliases in BUILTIN_ALIASES.values()):
                return i
    for i, row in enumerate(rows[:25]):
        filled = [c for c in row if c not in (None, "")]
        if len(filled) >= 2 and all(isinstance(c, str) for c in filled):
            return i
    return None


def _title_block(rows, header_idx, sheet_title):
    name, summary, context = None, None, []
    for row in rows[:header_idx]:
        text = " ".join(str(c) for c in row if c not in (None, "")).strip()
        if not text:
            continue
        key, sep, val = text.partition(":")
        k = _norm(key)
        if sep and val.strip():
            if k in NAME_KEYS and not name:
                name = val.strip()
                continue
            if k in SUMMARY_KEYS and not summary:
                summary = val.strip()
                continue
        context.append(text)
    return (name or sheet_title).strip()[:200], summary or "", "\n".join(context)


def _infer_type(values) -> tuple[str, list]:
    vals = [v for v in values if v not in (None, "")]
    if not vals:
        return "text", []
    if all(isinstance(v, bool) for v in vals):
        return "checkbox", []
    dates = [v for v in vals if isinstance(v, str) and _DATE.match(v)]
    if dates and len(dates) >= len(vals) * 0.5:
        return "date", []
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "number", []
    strs = [str(v) for v in vals]
    distinct = list(dict.fromkeys(strs))
    if len(distinct) <= 10 and len(strs) >= 2 * len(distinct) and max(len(s) for s in distinct) <= 40:
        return "select", distinct
    if any(len(s) > 80 or "\n" in s for s in strs):
        return "longtext", []
    return "text", []


def _sheet_plan(ws) -> dict:
    rows = _rows(ws)
    raw_first = [str(r[0].value) for r in ws.iter_rows(max_col=1) if r and isinstance(r[0].value, str)]
    hyperlinks = sum(1 for v in raw_first if _HYPERLINK.match(v.strip()))
    header_idx = _find_header(rows)
    plan = {"sheet": ws.title, "images": len(getattr(ws, "_images", [])), "hyperlinks": hyperlinks}
    if header_idx is None:
        return {**plan, "ok": False, "reason": "No header row found", "include": False, "rows": 0}

    header = rows[header_idx]
    data_rows = [r for r in rows[header_idx + 1:] if any(c not in (None, "") for c in r)]
    name, summary, context = _title_block(rows, header_idx, ws.title)

    columns, used_builtins = [], set()
    for ci, h in enumerate(header):
        if h in (None, ""):
            continue
        nh = _norm(h)
        target = next((b for b, aliases in BUILTIN_ALIASES.items() if nh in aliases and b not in used_builtins), None)
        values = [r[ci] if ci < len(r) else None for r in data_rows]
        # Type is inferred for every column so the user can remap a built-in one to a custom column
        ctype, options = _infer_type(values)
        sample = next((str(v) for v in values if v not in (None, "")), "")[:60]
        if target:
            used_builtins.add(target)
        columns.append({"index": ci, "header": str(h), "maps_to": target or "custom", "type": ctype,
                        "options": options, "sample": sample})

    # A link index (like a table of contents) is not inventory
    is_index = hyperlinks >= 2 and hyperlinks >= len(data_rows) * 0.5
    ok = "description" in used_builtins or any(c["maps_to"] == "custom" for c in columns)
    reason = "Looks like an index of links to other sheets" if is_index else (None if ok else "No usable columns")
    return {**plan, "ok": ok and not is_index, "reason": reason, "include": ok and not is_index,
            "name": name, "summary": summary, "context": context, "header_row": header_idx + 1,
            "rows": len(data_rows), "columns": columns}


def analyze(data: bytes) -> list[dict]:
    wb = _load(data)
    return [_sheet_plan(ws) for ws in wb.worksheets]


MAP_TARGETS = (*BUILTIN_ALIASES, "custom", "skip")


def _apply_mapping(plan: dict, rows, mapping: list[dict]):
    """Override the detected column mapping with the user's choices (by column index)."""
    by_index = {c["index"]: c for c in plan["columns"]}
    for m in mapping or []:
        c = by_index.get(m.get("index")) if isinstance(m, dict) else None
        if c is None:
            continue
        target = m.get("maps_to")
        if target not in MAP_TARGETS:
            raise ImportError_(f"Sheet “{plan['sheet']}”: unknown target for column “{c['header']}”")
        c["maps_to"] = target
        ctype = m.get("type") or c["type"]
        if target == "custom" and ctype != c["type"]:
            if ctype not in CUSTOM_TYPES:
                raise ImportError_(f"Sheet “{plan['sheet']}”: unknown type for column “{c['header']}”")
            c["type"] = ctype
            if ctype == "select":
                vals = [r[c["index"]] for r in rows[plan["header_row"]:] if c["index"] < len(r)]
                c["options"] = list(dict.fromkeys(str(v)[:80] for v in vals if v not in (None, "")))[:100]
    seen = set()
    for c in plan["columns"]:
        if c["maps_to"] in BUILTIN_ALIASES:
            if c["maps_to"] in seen:
                raise ImportError_(f"Sheet “{plan['sheet']}”: more than one column is mapped to {c['maps_to']}")
            seen.add(c["maps_to"])


def run(data: bytes, folder_id: int | None, sheets: list[str] | None = None,
        mappings: dict[str, list[dict]] | None = None) -> dict:
    if folder_id is not None:
        get_folder(folder_id)
    wb = _load(data)
    uid = current_user_id()
    created, total_items, total_photos = [], 0, 0
    pos = (db.session.query(db.func.max(InvTable.position))
           .filter(InvTable.folder_id.is_(None) if folder_id is None else InvTable.folder_id == folder_id)
           .scalar() or 0) + 1

    for ws in wb.worksheets:
        if sheets is not None and ws.title not in sheets:
            continue
        plan = _sheet_plan(ws)
        if not plan["ok"] and sheets is None:
            continue
        if plan.get("header_row") is None:
            continue
        rows = _rows(ws)
        header_idx = plan["header_row"] - 1
        if mappings and ws.title in mappings:
            _apply_mapping(plan, rows, mappings[ws.title])

        cols = default_columns()
        builtin_label = {c["maps_to"]: c["header"] for c in plan["columns"] if c["maps_to"] in BUILTIN_ALIASES}
        for c in cols:
            if c["key"] in builtin_label:
                c["label"] = builtin_label[c["key"]][:60]
        custom_map: dict[int, dict] = {}
        keys = {c["key"] for c in cols}
        # Custom columns go before Obs, matching how inventories are usually laid out
        insert_at = 2
        for c in plan["columns"]:
            if c["maps_to"] != "custom":
                continue
            key = slugify_key(c["header"], keys)
            keys.add(key)
            col = {"key": key, "label": c["header"][:60], "type": c["type"], "width": 150, "hidden": False}
            if c["type"] == "select":
                col["options"] = c["options"]
            cols.insert(insert_at, col)
            insert_at += 1
            custom_map[c["index"]] = col
        idx_of = {c["maps_to"]: c["index"] for c in plan["columns"] if c["maps_to"] in BUILTIN_ALIASES}

        t = InvTable(name=plan["name"], folder_id=folder_id, summary=plan["summary"], context=plan["context"],
                     columns=cols, position=pos, updated_by=uid, content_updated_by=uid)
        pos += 1
        db.session.add(t)
        db.session.flush()

        row_to_item: dict[int, Item] = {}
        ipos = 1
        for ri in range(header_idx + 1, len(rows)):
            r = rows[ri]
            if not any(c not in (None, "") for c in r):
                continue

            def val(key):
                i = idx_of.get(key)
                return r[i] if i is not None and i < len(r) else None

            custom = {}
            for ci, col in custom_map.items():
                v = coerce_custom(r[ci] if ci < len(r) else None, col["type"])
                if v not in (None, ""):
                    custom[col["key"]] = v
            desc = val("description")
            it = Item(table_id=t.id, position=ipos, description=str(desc or "").strip(),
                      observation=str(val("observation") or "").strip(), custom=custom, updated_by=uid)
            it.quantity = quantity.parse(val("quantity"))
            db.session.add(it)
            row_to_item[ri] = it
            ipos += 1
        db.session.flush()

        for img in getattr(ws, "_images", []):
            try:
                anchor_row = img.anchor._from.row
            except AttributeError:
                continue
            it = row_to_item.get(anchor_row)
            if it is None:
                continue
            try:
                raw = img._data()
                p = photo_svc.store_bytes(raw, f"{ws.title}-r{anchor_row + 1}.jpg", uid, it.id,
                                          position=len([x for x in it.photos]))
                it.photos.append(p)
                total_photos += 1
            except photo_svc.PhotoError:
                continue

        total_items += len(row_to_item)
        created.append({"id": t.id, "name": t.name, "items": len(row_to_item)})
    db.session.commit()
    if not created:
        raise ImportError_("No sheets were imported")
    return {"tables": created, "items": total_items, "photos": total_photos}
