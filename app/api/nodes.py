"""Folders, tables, columns, bulk node actions and trash."""
from flask import jsonify, request
from flask_login import current_user

from ..extensions import db
from ..services import inventory as inv
from ..services.audit import audit
from ..services.tree import FolderMap, table_state, tree_payload
from . import admin_required, body, bp, editor_required, int_list


@bp.get("/tree")
def tree():
    include = request.args.get("include_archived", "1") != "0"
    return jsonify(tree_payload(include_archived=include))


# ---------------------------------------------------------------- folders

@bp.post("/folders")
@editor_required
def create_folder():
    d = body()
    f = inv.create_folder(d.get("name"), d.get("parent_id"))
    return jsonify(f.to_dict()), 201


@bp.patch("/folders/<int:fid>")
@editor_required
def update_folder(fid):
    d = body()
    f = inv.update_folder(inv.get_folder(fid), d)
    if "active" in d:
        audit("archive" if not f.active else "reactivate", "folder", f.id)
    return jsonify(f.to_dict())


@bp.get("/folders/<int:fid>")
def folder_view(fid):
    """Folder overview: tables directly inside plus sub-folders, with last-updated info."""
    f = inv.get_folder(fid)
    payload = tree_payload(include_archived=True)
    fmap = FolderMap()
    return jsonify({
        "folder": {**f.to_dict(), "path": fmap.path(f.id), "effective_active": fmap.effective_active(f.id)},
        "folders": [x for x in payload["folders"] if x["parent_id"] == fid],
        "tables": [x for x in payload["tables"] if x["folder_id"] == fid],
    })


@bp.get("/folders/<int:fid>/items")
def folder_items(fid):
    """Every live item in this folder and its subfolders, with each item's path (read-only view)."""
    from sqlalchemy.orm import selectinload

    from ..models import InvTable, Item
    from .. import quantity
    f = inv.get_folder(fid)
    include_archived = request.args.get("include_archived") in ("1", "true")
    fmap = FolderMap()
    folder_ids = [fid] + [d for d in fmap.descendants(fid) if not fmap.nodes[d].deleted]
    tables = {t.id: t for t in db.session.query(InvTable).filter(
        InvTable.folder_id.in_(folder_ids), InvTable.deleted_at.is_(None))}
    active = {tid: t.active and fmap.effective_active(t.folder_id) for tid, t in tables.items()}
    if not include_archived:
        tables = {tid: t for tid, t in tables.items() if active[tid]}
    items = (db.session.query(Item).options(selectinload(Item.photos), selectinload(Item.creator))
             .filter(Item.table_id.in_(list(tables) or [0]), Item.deleted_at.is_(None))
             .order_by(Item.table_id, Item.position).all())
    out = []
    for it in items:
        t = tables[it.table_id]
        labels = {c["key"]: c.get("label") or c["key"] for c in t.columns}
        fields = " · ".join(f"{labels.get(k, k)}: {'yes' if v is True else v}"
                            for k, v in (it.custom or {}).items() if v not in (None, "", False))
        q = it.quantity
        out.append({
            "id": it.id, "table_id": t.id, "table_name": t.name,
            # relative to the folder being viewed: "Box 2" or "Shelf / Box 2"
            "path": " / ".join(fmap.path(t.folder_id)[len(fmap.path(f.id)):] + [t.name]),
            "description": it.description or "", "quantity": q, "quantity_display": quantity.format(q),
            "observation": it.observation or "", "fields": fields, "archived": not active[t.id],
            "photos": [p.to_dict() for p in it.photos],
            "created_at": it.to_dict()["created_at"], "created_by": it.creator.name if it.creator else None,
        })
    return jsonify({"folder": {**f.to_dict(), "path": fmap.path(f.id)}, "items": out,
                    "tables": len(tables), "archived_hidden": not include_archived and any(not a for a in active.values())})


# ---------------------------------------------------------------- tables

@bp.post("/tables")
@editor_required
def create_table():
    d = body()
    t = inv.create_table(d.get("name"), d.get("folder_id"), d.get("summary", ""), d.get("context", ""))
    return jsonify(t.to_dict()), 201


@bp.get("/tables/<int:tid>")
def get_table(tid):
    t = inv.get_table(tid)
    st = table_state(t)
    items = [i.to_dict() for i in t.live_items()]
    return jsonify({**t.to_dict(), **st, "items": items, "item_count": len(items),
                    "writable": st["effective_active"] and current_user.can_edit})


@bp.patch("/tables/<int:tid>")
@editor_required
def update_table(tid):
    d = body()
    t = inv.update_table(inv.get_table(tid), d)
    if "active" in d:
        audit("archive" if not t.active else "reactivate", "table", t.id)
    return jsonify({**t.to_dict(), **table_state(t)})


@bp.post("/tables/<int:tid>/columns")
@editor_required
def add_column(tid):
    return jsonify(inv.add_column(inv.get_table(tid), body())), 201


@bp.patch("/tables/<int:tid>/columns/<key>")
@editor_required
def update_column(tid, key):
    return jsonify(inv.update_column(inv.get_table(tid), key, body()))


@bp.delete("/tables/<int:tid>/columns/<key>")
@editor_required
def delete_column(tid, key):
    inv.delete_column(inv.get_table(tid), key)
    return jsonify(ok=True)


@bp.put("/tables/<int:tid>/columns/order")
@editor_required
def reorder_columns(tid):
    inv.reorder_columns(inv.get_table(tid), body().get("keys") or [])
    return jsonify(ok=True)


@bp.put("/tables/<int:tid>/items/order")
@editor_required
def reorder_items(tid):
    inv.reorder_items(inv.get_table(tid), int_list(body().get("ids")))
    return jsonify(ok=True)


# ---------------------------------------------------------------- bulk nodes

@bp.post("/nodes/impact")
def nodes_impact():
    d = body()
    return jsonify(inv.impact(int_list(d.get("folders")), int_list(d.get("tables"))))


@bp.post("/nodes/bulk")
@editor_required
def nodes_bulk():
    d = body()
    folders, tables, action = int_list(d.get("folders")), int_list(d.get("tables")), d.get("action")
    if action == "delete":
        res = inv.delete_nodes(folders, tables)
        audit("delete", "nodes", None, f"folders={folders} tables={tables} batch={res['batch']}")
    elif action in ("archive", "reactivate"):
        res = inv.set_active(folders, tables, action == "reactivate")
        audit(action, "nodes", None, f"folders={folders} tables={tables}")
    elif action == "move":
        res = inv.move_nodes(folders, tables, d.get("target_folder_id"))
    else:
        return jsonify(error=f"Unknown action: {action}"), 400
    return jsonify(res)


# ---------------------------------------------------------------- trash

@bp.get("/trash")
def trash():
    return jsonify(inv.list_trash())


@bp.post("/trash/<batch>/restore")
@editor_required
def trash_restore(batch):
    res = inv.restore_batch(batch)
    audit("restore", "batch", None, batch)
    return jsonify(res)


@bp.delete("/trash/<batch>")
@admin_required
def trash_purge_batch(batch):
    res = inv.purge(batch=batch)
    audit("purge", "batch", None, batch)
    return jsonify(res)


@bp.delete("/trash")
@admin_required
def trash_empty():
    res = inv.purge()
    audit("purge", "trash", None, "all")
    return jsonify(res)
