"""Import / export endpoints."""
import json
import re

from flask import Response, jsonify, request, send_file

from ..extensions import db
from ..services import inventory as inv
from ..services import photos_zip, xlsx_export, xlsx_import
from ..services.audit import audit
from ..services.tree import FolderMap
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
        mappings = json.loads(request.form.get("mappings") or "{}")
        if not isinstance(mappings, dict):
            raise ValueError
    except ValueError:
        return jsonify(error="Invalid column mapping"), 400
    try:
        res = xlsx_import.run(data, folder_id, sheets, mappings)
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


@bp.get("/export/photos.zip")
def export_photos():
    """Photos of a folder (with subfolders), of some tables, or of a sidebar selection (folders + tables).
    ?check=1 only counts them, so the UI can say "no photos here" instead of downloading an error."""
    folder_ids, table_ids = int_list(request.args.get("folders")), int_list(request.args.get("tables"))
    folder_id = request.args.get("folder_id", type=int)
    if folder_id:
        folder_ids.append(folder_id)
    include_archived = request.args.get("include_archived", "1") != "0"
    tables = {t.id: t for fid in folder_ids for t in xlsx_export.tables_for(fid, None, include_archived)}
    if table_ids:
        tables.update({t.id: t for t in xlsx_export.tables_for(None, table_ids, include_archived)})
    if not folder_ids and not table_ids:
        tables = {t.id: t for t in xlsx_export.tables_for(None, None, include_archived)}
    fmap = FolderMap()
    ordered = sorted(tables.values(), key=lambda t: (fmap.path(t.folder_id), t.position, t.name))

    single_folder = folder_ids[0] if len(folder_ids) == 1 and not table_ids else None
    if request.args.get("check"):
        from ..models import Item, Photo
        n = (db.session.query(Photo.id).join(Item, Photo.item_id == Item.id)
             .filter(Item.table_id.in_(list(tables) or [0]), Item.deleted_at.is_(None)).count())
        return jsonify(photos=n, tables=len(tables))
    if single_folder:
        name = inv.get_folder(single_folder).name
    elif len(ordered) == 1:
        name = ordered[0].name
    else:
        name = "inventory"
    f, count = photos_zip.build(ordered, single_folder)
    if not count:
        f.close()
        return jsonify(error="No photos here"), 404
    return send_file(f, mimetype="application/zip", as_attachment=True,
                     download_name=_filename(f"{name} photos", "zip"))


@bp.get("/tables/<int:tid>/export.csv")
def export_csv(tid):
    t = inv.get_table(tid)
    return Response("﻿" + xlsx_export.to_csv(t), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{_filename(t.name, "csv")}"'})
