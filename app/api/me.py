"""The signed-in user's own preferences (table view settings)."""
from flask import jsonify
from flask_login import current_user

from ..extensions import db
from . import body, bp

ROW_H = (26, 160)
FONT = (10, 22)


def _num(v, lo, hi):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return None


def _clean_table_prefs(d: dict) -> dict:
    """Validate a partial per-table update; None values mean 'remove this setting'."""
    out = {}
    for key, val in d.items():
        if val is None:
            out[key] = None
        elif key == "rowH":
            out[key] = _num(val, *ROW_H)
        elif key == "font":
            out[key] = _num(val, *FONT)
        elif key in ("hidden", "order") and isinstance(val, list):
            out[key] = [str(k)[:60] for k in val][:200]
        elif key == "widths" and isinstance(val, dict):
            out[key] = {str(k)[:60]: n for k, v in val.items() if (n := _num(v, 40, 2000))}
        elif key in ("showCreated", "showUpdated"):
            out[key] = bool(val)
    return out


SIZES = ("small", "large")  # phone layout (<= 600 px) vs. everything wider


def _size() -> str:
    from flask import request
    size = request.args.get("size", "large")
    return size if size in SIZES else "large"


@bp.get("/me/prefs")
def get_prefs():
    return jsonify(current_user.prefs or {})


@bp.patch("/me/prefs/tables/<int:tid>")
def patch_table_prefs(tid):
    """Partial update of one table's view for one screen size (?size=small|large)."""
    size = _size()
    prefs = dict(current_user.prefs or {})
    tables = dict(prefs.get("tables") or {})
    per_table = dict(tables.get(str(tid)) or {})
    cur = dict(per_table.get(size) or {})
    for k, v in _clean_table_prefs(body()).items():
        if v is None:
            cur.pop(k, None)
        else:
            cur[k] = v
    if cur:
        per_table[size] = cur
    else:
        per_table.pop(size, None)
    if per_table:
        tables[str(tid)] = per_table
    else:
        tables.pop(str(tid), None)
    prefs["tables"] = tables
    current_user.prefs = prefs
    db.session.commit()
    return jsonify(cur)


@bp.put("/me/prefs/grid")
def put_grid_prefs():
    """Default row height / text size for one screen size; apply_everywhere drops that size's
    per-table row height / text size overrides (column choices are kept)."""
    size = _size()
    d = body()
    prefs = dict(current_user.prefs or {})
    grid = dict(prefs.get("grid") or {})
    grid[size] = {"rowH": _num(d.get("rowH"), *ROW_H) or 36, "font": _num(d.get("font"), *FONT) or 13}
    prefs["grid"] = grid
    if d.get("apply_everywhere"):
        tables = {}
        for tid, per_table in (prefs.get("tables") or {}).items():
            per_table = dict(per_table)
            if size in per_table:
                kept = {k: v for k, v in per_table[size].items() if k not in ("rowH", "font")}
                if kept:
                    per_table[size] = kept
                else:
                    per_table.pop(size)
            if per_table:
                tables[tid] = per_table
        prefs["tables"] = tables
    current_user.prefs = prefs
    db.session.commit()
    return jsonify(prefs)
