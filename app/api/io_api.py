"""Import / export endpoints."""
import re

from flask import Response, jsonify, request

from ..extensions import db
from ..services import inventory as inv
from ..services import xlsx_export, xlsx_import
from ..services.audit import audit
from . import bp, editor_required, int_list

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _upload_bytes() -> bytes:
    f = request.files.get("file")
    if not f or not f.filename:
        raise inv.InventoryError("Choose an .xlsx file")
    if not f.filename.lower().endswith((".xlsx", ".xlsm")):
        raise inv.InventoryError("Only .xlsx files are supported")
    return f.read()


@bp.post("/import/xlsx/analyze")
@editor_required
def import_analyze():
    try:
        return jsonify(xlsx_import.analyze(_upload_bytes()))
    except xlsx_import.ImportError_ as e:
        return jsonify(error=str(e)), 400


@bp.post("/import/xlsx")
@editor_required
def import_run():
    data = _upload_bytes()
    folder_id = request.form.get("folder_id")
    folder_id = int(folder_id) if folder_id not in (None, "", "0", "null") else None
    sheets = request.form.getlist("sheets") or None
    try:
        res = xlsx_import.run(data, folder_id, sheets)
    except xlsx_import.ImportError_ as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400
    audit("import", "xlsx", None, f"{request.files['file'].filename}: {len(res['tables'])} tables")
    return jsonify(res), 201


def _filename(name: str, ext: str) -> str:
    safe = re.sub(r"[^\w\- ]+", "", name).strip().replace(" ", "_") or "inventory"
    return f"{safe[:80]}.{ext}"


@bp.get("/export/xlsx")
def export_xlsx():
    folder_id = request.args.get("folder_id", type=int)
    table_ids = int_list(request.args.get("tables"))
    include_archived = request.args.get("include_archived", "1") != "0"
    tables = xlsx_export.tables_for(folder_id, table_ids, include_archived)
    if not tables:
        return jsonify(error="Nothing to export"), 404
    if len(tables) == 1:
        name = tables[0].name
    elif folder_id:
        name = inv.get_folder(folder_id).name
    else:
        name = "inventory"
    data = xlsx_export.to_xlsx(tables)
    return Response(data, mimetype=XLSX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{_filename(name, "xlsx")}"'})


@bp.get("/tables/<int:tid>/export.csv")
def export_csv(tid):
    t = inv.get_table(tid)
    return Response("﻿" + xlsx_export.to_csv(t), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(t.name, "csv")}"'})
