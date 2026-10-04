"""Business logic for folders, tables, items: CRUD, columns, soft delete, trash, archive."""
import re
import uuid
from datetime import timedelta

from sqlalchemy import func

from ..extensions import db
from ..models import (BUILTIN_COLUMNS, CUSTOM_TYPES, Folder, InvTable, Item, Photo,
                      default_columns, slugify_key, utcnow)
from . import photos as photo_svc
from .tracking import current_user_id
from .tree import FolderMap


_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


class InventoryError(ValueError):
    status = 400


class Forbidden(InventoryError):
    status = 403


class NotFound(InventoryError):
    status = 404


# ---------------------------------------------------------------- lookups

def get_folder(fid: int, allow_deleted=False) -> Folder:
    f = db.session.get(Folder, fid)
    if f is None or (f.deleted_at is not None and not allow_deleted):
        raise NotFound("Folder not found")
    return f


def get_table(tid: int, allow_deleted=False) -> InvTable:
    t = db.session.get(InvTable, tid)
    if t is None or (t.deleted_at is not None and not allow_deleted):
        raise NotFound("Table not found")
    return t


def get_item(iid: int) -> Item:
    it = db.session.get(Item, iid)
    if it is None or it.deleted_at is not None:
        raise NotFound("Item not found")
    return it


def ensure_table_writable(t: InvTable, fmap: FolderMap | None = None):
    fmap = fmap or FolderMap()
    if t.deleted_at is not None or fmap.effective_deleted(t.folder_id):
        raise NotFound("Table is in the trash")
    if not (t.active and fmap.effective_active(t.folder_id)):
        raise Forbidden("This table is archived (read-only). Reactivate it to edit.")


def _clean_name(name, what="Name") -> str:
    name = (name or "").strip()
    if not name:
        raise InventoryError(f"{what} is required")
    return name[:200]


def _next_position(model, **filters) -> int:
    q = db.session.query(func.max(model.position))
    for k, v in filters.items():
        col = getattr(model, k)
        q = q.filter(col.is_(None) if v is None else col == v)
    return (q.scalar() or 0) + 1


# ---------------------------------------------------------------- folders

def create_folder(name: str, parent_id: int | None = None) -> Folder:
    if parent_id is not None:
        get_folder(parent_id)
    f = Folder(name=_clean_name(name), parent_id=parent_id,
               position=_next_position(Folder, parent_id=parent_id))
    db.session.add(f)
    db.session.commit()
    return f


def update_folder(f: Folder, data: dict) -> Folder:
    if "name" in data:
        f.name = _clean_name(data["name"])
    if "parent_id" in data:
        _move_folder(f, data["parent_id"])
    if "active" in data:
        f.active = bool(data["active"])
    if "position" in data:
        f.position = int(data["position"])
    db.session.commit()
    return f


def _move_folder(f: Folder, parent_id):
    parent_id = int(parent_id) if parent_id not in (None, "", 0) else None
    if parent_id is not None:
        get_folder(parent_id)
        if parent_id == f.id or FolderMap().is_descendant(parent_id, f.id):
            raise InventoryError("Can't move a folder into itself")
    f.parent_id = parent_id
    f.position = _next_position(Folder, parent_id=parent_id)


# ---------------------------------------------------------------- tables

def create_table(name: str, folder_id: int | None = None, summary="", context="",
                 columns: list | None = None) -> InvTable:
    if folder_id is not None:
        get_folder(folder_id)
    t = InvTable(name=_clean_name(name), folder_id=folder_id, summary=summary or "",
                 context=context or "", columns=columns or default_columns(),
                 position=_next_position(InvTable, folder_id=folder_id),
                 updated_by=current_user_id(), content_updated_by=current_user_id())
    db.session.add(t)
    db.session.commit()
    return t


def update_table(t: InvTable, data: dict) -> InvTable:
    fmap = FolderMap()
    only_state = set(data) <= {"active", "position", "folder_id"}
    if not only_state:
        ensure_table_writable(t, fmap)
    for field in ("summary", "context"):
        if field in data:
            setattr(t, field, (data[field] or "").strip())
    if "name" in data:
        t.name = _clean_name(data["name"])
    if "folder_id" in data:
        fid = data["folder_id"]
        fid = int(fid) if fid not in (None, "", 0) else None
        if fid is not None:
            get_folder(fid)
        t.folder_id = fid
        t.position = _next_position(InvTable, folder_id=fid)
    if "active" in data:
        t.active = bool(data["active"])
    if "position" in data:
        t.position = int(data["position"])
    t.updated_by = current_user_id()
    db.session.commit()
    return t


# ---------------------------------------------------------------- columns

def _clean_column(col: dict, existing_keys: set[str]) -> dict:
    ctype = col.get("type") or "text"
    if ctype not in CUSTOM_TYPES:
        raise InventoryError(f"Unknown column type: {ctype}")
    label = _clean_name(col.get("label"), "Column label")[:60]
    key = col.get("key") or slugify_key(label, existing_keys)
    out = {"key": key, "label": label, "type": ctype, "width": int(col.get("width") or 150),
           "hidden": bool(col.get("hidden"))}
    if ctype == "select":
        opts = [str(o).strip()[:80] for o in (col.get("options") or []) if str(o).strip()]
        out["options"] = list(dict.fromkeys(opts))
    return out


def add_column(t: InvTable, col: dict) -> dict:
    ensure_table_writable(t)
    new = _clean_column({k: v for k, v in col.items() if k != "key"}, t.column_keys())
    t.columns.append(new)
    db.session.commit()
    return new


def update_column(t: InvTable, key: str, data: dict) -> dict:
    ensure_table_writable(t)
    for i, c in enumerate(t.columns):
        if c["key"] != key:
            continue
        if c.get("type") == "builtin":
            c = dict(c)
            if "label" in data:
                c["label"] = _clean_name(data["label"], "Column label")[:60]
            for f in ("width", "hidden"):
                if f in data:
                    c[f] = data[f]
            if key == "description":
                c["hidden"] = False
        else:
            merged = {**c, **{k: v for k, v in data.items() if k != "key"}, "key": key}
            c = _clean_column(merged, t.column_keys())
            if c["type"] != t.columns[i]["type"]:
                _coerce_values(t, key, c["type"])
        t.columns[i] = c
        db.session.commit()
        return c
    raise NotFound("Column not found")


def reorder_columns(t: InvTable, keys: list[str]):
    ensure_table_writable(t)
    by_key = {c["key"]: c for c in t.columns}
    if set(keys) != set(by_key):
        raise InventoryError("Column list mismatch")
    t.columns = [by_key[k] for k in keys]
    db.session.commit()


def delete_column(t: InvTable, key: str):
    ensure_table_writable(t)
    if key in BUILTIN_COLUMNS:
        raise InventoryError("Built-in columns can be hidden but not deleted")
    if key not in t.column_keys():
        raise NotFound("Column not found")
    t.columns = [c for c in t.columns if c["key"] != key]
    for it in db.session.query(Item).filter(Item.table_id == t.id):
        if key in (it.custom or {}):
            del it.custom[key]
    db.session.commit()


def _coerce_values(t: InvTable, key: str, ctype: str):
    for it in db.session.query(Item).filter(Item.table_id == t.id):
        if key in (it.custom or {}):
            it.custom[key] = coerce_custom(it.custom[key], ctype)


def coerce_custom(val, ctype: str):
    if val in (None, ""):
        return None
    if ctype == "number":
        try:
            f = float(str(val).replace(",", "."))
            return int(f) if f.is_integer() else f
        except ValueError:
            return None
    if ctype == "checkbox":
        return str(val).lower() in ("1", "true", "yes", "y", "on", "x", "sim")
    if ctype == "date":
        if hasattr(val, "isoformat"):
            return val.isoformat()[:10]
        s = str(val).strip()[:10]
        return s if _DATE_RE.match(s) else None
    return str(val)[:5000]


# ---------------------------------------------------------------- items

ITEM_FIELDS = ("description", "observation", "quantity", "custom")


def _apply_item_data(it: Item, t: InvTable, data: dict):
    if "description" in data:
        it.description = (data["description"] or "").strip()[:2000]
    if "observation" in data:
        it.observation = (data["observation"] or "").strip()[:5000]
    if "quantity" in data:
        it.quantity = data["quantity"]
    if "custom" in data and isinstance(data["custom"], dict):
        types = {c["key"]: c["type"] for c in t.custom_columns()}
        custom = dict(it.custom or {})
        for k, v in data["custom"].items():
            if k in types:
                custom[k] = coerce_custom(v, types[k])
        it.custom = custom
    if data.get("ai_generated") is False:
        it.ai_generated = False


def create_item(t: InvTable, data: dict, ai_generated=False, after_id: int | None = None) -> Item:
    ensure_table_writable(t)
    if after_id:
        after = get_item(after_id)
        pos = after.position + 1
        db.session.query(Item).filter(Item.table_id == t.id, Item.position >= pos).update(
            {Item.position: Item.position + 1}, synchronize_session=False)
    else:
        pos = _next_position(Item, table_id=t.id)
    it = Item(table_id=t.id, position=pos, ai_generated=ai_generated, updated_by=current_user_id(),
              custom={})
    _apply_item_data(it, t, data)
    db.session.add(it)
    db.session.commit()
    return it


def update_item(it: Item, data: dict) -> Item:
    t = it.table
    ensure_table_writable(t)
    _apply_item_data(it, t, data)
    if any(k in data for k in ITEM_FIELDS):
        it.ai_generated = False if not data.get("keep_ai_flag") else it.ai_generated
    it.updated_by = current_user_id()
    db.session.commit()
    return it


def reorder_items(t: InvTable, ids: list[int]):
    ensure_table_writable(t)
    items = {i.id: i for i in db.session.query(Item).filter(Item.table_id == t.id, Item.deleted_at.is_(None))}
    pos = 1
    for iid in ids:
        if iid in items:
            items.pop(iid).position = pos
            pos += 1
    for it in sorted(items.values(), key=lambda i: i.position):
        it.position = pos
        pos += 1
    db.session.commit()


def bulk_items(ids: list[int], action: str, data: dict | None = None) -> dict:
    data = data or {}
    items = db.session.query(Item).filter(Item.id.in_(ids), Item.deleted_at.is_(None)).all()
    if not items:
        raise NotFound("No items selected")
    fmap = FolderMap()
    for t in {i.table for i in items}:
        ensure_table_writable(t, fmap)
    uid, now = current_user_id(), utcnow()
    if action == "delete":
        batch = str(uuid.uuid4())
        for it in items:
            it.deleted_at, it.deleted_by, it.delete_batch = now, uid, batch
        db.session.commit()
        return {"deleted": len(items), "batch": batch}
    if action == "move":
        target = get_table(int(data.get("table_id") or 0))
        ensure_table_writable(target, fmap)
        pos = _next_position(Item, table_id=target.id)
        keys = target.column_keys()
        for src in {i.table for i in items} - {target}:
            src.content_updated_at, src.content_updated_by = now, uid
        for it in sorted(items, key=lambda i: (i.table_id, i.position)):
            it.table_id, it.position = target.id, pos
            it.custom = {k: v for k, v in (it.custom or {}).items() if k in keys}
            it.updated_by = uid
            pos += 1
        db.session.commit()
        return {"moved": len(items), "table_id": target.id}
    if action in ("set_estimated", "clear_estimated"):
        for it in items:
            it.qty_estimated = action == "set_estimated"
            it.updated_by = uid
        db.session.commit()
        return {"updated": len(items)}
    if action == "clear_ai_flag":
        for it in items:
            it.ai_generated = False
        db.session.commit()
        return {"updated": len(items)}
    raise InventoryError(f"Unknown action: {action}")


# ---------------------------------------------------------------- photos

def add_photos(it: Item, files) -> list[Photo]:
    ensure_table_writable(it.table)
    pos = _next_position(Photo, item_id=it.id)
    out = []
    for f in files:
        data = f.read()
        if not data:
            continue
        p = photo_svc.store_bytes(data, getattr(f, "filename", None), current_user_id(), it.id, pos)
        pos += 1
        out.append(p)
    it.updated_by = current_user_id()
    db.session.commit()
    return out


def delete_photo(p: Photo):
    if p.item is not None:
        ensure_table_writable(p.item.table)
    sha = p.sha256
    db.session.delete(p)
    db.session.commit()
    photo_svc.delete_files_if_orphan(sha)


def make_main_photo(p: Photo):
    if p.item is None:
        raise InventoryError("Photo isn't attached to an item")
    ensure_table_writable(p.item.table)
    others = [x for x in p.item.photos if x.id != p.id]
    p.position = 0
    for i, x in enumerate(others, start=1):
        x.position = i
    db.session.commit()


# ---------------------------------------------------------------- nodes: impact, delete, archive, move

def _expand(folder_ids: list[int], table_ids: list[int], fmap: FolderMap):
    """Live folders/tables selected plus everything below the folders."""
    folders: set[int] = set()
    for fid in folder_ids:
        f = db.session.get(Folder, fid)
        if f is None or f.deleted_at is not None:
            continue
        folders.add(fid)
        folders.update(d for d in fmap.descendants(fid) if not fmap.nodes[d].deleted)
    tables = set()
    if folders:
        tables.update(t for (t,) in db.session.query(InvTable.id).filter(
            InvTable.folder_id.in_(folders), InvTable.deleted_at.is_(None)))
    tables.update(t for (t,) in db.session.query(InvTable.id).filter(
        InvTable.id.in_(table_ids or [0]), InvTable.deleted_at.is_(None)))
    return folders, tables


def impact(folder_ids: list[int], table_ids: list[int]) -> dict:
    fmap = FolderMap()
    folders, tables = _expand(folder_ids, table_ids, fmap)
    n_items = n_photos = 0
    if tables:
        n_items = db.session.query(Item.id).filter(Item.table_id.in_(tables), Item.deleted_at.is_(None)).count()
        n_photos = (db.session.query(Photo.id).join(Item, Photo.item_id == Item.id)
                    .filter(Item.table_id.in_(tables), Item.deleted_at.is_(None)).count())
    return {"folders": len(folders), "tables": len(tables), "items": n_items, "photos": n_photos}


def delete_nodes(folder_ids: list[int], table_ids: list[int]) -> dict:
    fmap = FolderMap()
    summary = impact(folder_ids, table_ids)
    folders, tables = _expand(folder_ids, table_ids, fmap)
    if not folders and not tables:
        raise NotFound("Nothing to delete")
    batch, now, uid = str(uuid.uuid4()), utcnow(), current_user_id()
    for fid in folders:
        f = db.session.get(Folder, fid)
        f.deleted_at, f.deleted_by, f.delete_batch = now, uid, batch
    for tid in tables:
        t = db.session.get(InvTable, tid)
        t.deleted_at, t.deleted_by, t.delete_batch = now, uid, batch
    db.session.commit()
    return {**summary, "batch": batch}


def set_active(folder_ids: list[int], table_ids: list[int], active: bool) -> dict:
    n = 0
    for fid in folder_ids:
        f = get_folder(fid)
        f.active = active
        n += 1
    for tid in table_ids:
        t = get_table(tid)
        t.active = active
        n += 1
    db.session.commit()
    return {"updated": n}


def move_nodes(folder_ids: list[int], table_ids: list[int], target_folder_id) -> dict:
    for fid in folder_ids:
        _move_folder(get_folder(fid), target_folder_id)
    target = int(target_folder_id) if target_folder_id not in (None, "", 0) else None
    if target is not None:
        get_folder(target)
    for tid in table_ids:
        t = get_table(tid)
        t.folder_id = target
        t.position = _next_position(InvTable, folder_id=target)
    db.session.commit()
    return {"moved": len(folder_ids) + len(table_ids)}


# ---------------------------------------------------------------- trash

def list_trash() -> list[dict]:
    """Deleted things grouped by delete batch, showing only the top-level entries of each."""
    from ..models import User
    users = dict(db.session.query(User.id, func.coalesce(User.display_name, User.username)).all())
    fmap = FolderMap()
    batches: dict[str, dict] = {}

    def entry(batch, when, who):
        b = batches.setdefault(batch, {"batch": batch, "deleted_at": None, "deleted_by": None,
                                       "folders": [], "tables": [], "items": [],
                                       "counts": {"folders": 0, "tables": 0, "items": 0}})
        b["deleted_at"] = _iso(when)
        b["deleted_by"] = users.get(who)
        return b

    deleted_folders = db.session.query(Folder).filter(Folder.deleted_at.isnot(None)).all()
    batch_folder_ids: dict[str, set[int]] = {}
    for f in deleted_folders:
        batch_folder_ids.setdefault(f.delete_batch, set()).add(f.id)
    for f in deleted_folders:
        b = entry(f.delete_batch, f.deleted_at, f.deleted_by)
        b["counts"]["folders"] += 1
        if f.parent_id not in batch_folder_ids[f.delete_batch]:
            b["folders"].append({"id": f.id, "name": f.name, "path": fmap.path(f.parent_id)})

    for t in db.session.query(InvTable).filter(InvTable.deleted_at.isnot(None)):
        b = entry(t.delete_batch, t.deleted_at, t.deleted_by)
        b["counts"]["tables"] += 1
        b["counts"]["items"] += t.live_item_count()
        if t.folder_id not in batch_folder_ids.get(t.delete_batch, set()):
            b["tables"].append({"id": t.id, "name": t.name, "path": fmap.path(t.folder_id)})

    for it in db.session.query(Item).filter(Item.deleted_at.isnot(None)).order_by(Item.deleted_at.desc()):
        b = entry(it.delete_batch, it.deleted_at, it.deleted_by)
        b["counts"]["items"] += 1
        t = it.table
        b["items"].append({"id": it.id, "description": it.description, "table_id": t.id,
                           "table_name": t.name, "path": fmap.path(t.folder_id) + [t.name]})
    return sorted(batches.values(), key=lambda b: b["deleted_at"] or "", reverse=True)


def restore_batch(batch: str) -> dict:
    fmap = FolderMap()
    folders = db.session.query(Folder).filter(Folder.delete_batch == batch).all()
    tables = db.session.query(InvTable).filter(InvTable.delete_batch == batch).all()
    items = db.session.query(Item).filter(Item.delete_batch == batch).all()
    if not (folders or tables or items):
        raise NotFound("Nothing to restore")
    batch_folders = {f.id for f in folders}
    warnings = []
    for f in folders:
        f.deleted_at = f.deleted_by = f.delete_batch = None
        # Parent still in the trash (from another delete): bring it to the root
        if f.parent_id and f.parent_id not in batch_folders and fmap.effective_deleted(f.parent_id):
            f.parent_id = None
            warnings.append(f"Folder '{f.name}' restored to the top level (its parent is in the trash).")
    for t in tables:
        t.deleted_at = t.deleted_by = t.delete_batch = None
        if t.folder_id and t.folder_id not in batch_folders and fmap.effective_deleted(t.folder_id):
            t.folder_id = None
            warnings.append(f"Table '{t.name}' restored to the top level (its folder is in the trash).")
    for it in items:
        it.deleted_at = it.deleted_by = it.delete_batch = None
        if it.table.deleted_at is not None:
            warnings.append(f"'{it.description}' belongs to table '{it.table.name}', which is in the trash.")
    db.session.commit()
    return {"restored": {"folders": len(folders), "tables": len(tables), "items": len(items)},
            "warnings": warnings}


def purge(batch: str | None = None, older_than_days: int | None = None) -> dict:
    """Permanently delete trashed rows (one batch, everything older than N days, or all)."""
    def flt(model):
        q = db.session.query(model).filter(model.deleted_at.isnot(None))
        if batch:
            q = q.filter(model.delete_batch == batch)
        if older_than_days is not None:
            q = q.filter(model.deleted_at < utcnow() - timedelta(days=older_than_days))
        return q

    folders, tables, items = flt(Folder).all(), flt(InvTable).all(), flt(Item).all()
    fmap = FolderMap()
    folder_ids = set()
    for f in folders:
        folder_ids.add(f.id)
        folder_ids.update(fmap.descendants(f.id))
    table_ids = {t.id for t in tables}
    if folder_ids:
        table_ids.update(t for (t,) in db.session.query(InvTable.id).filter(InvTable.folder_id.in_(folder_ids)))
    item_ids = {i.id for i in items}
    if table_ids:
        item_ids.update(i for (i,) in db.session.query(Item.id).filter(Item.table_id.in_(table_ids)))
    shas = {s for (s,) in db.session.query(Photo.sha256).filter(Photo.item_id.in_(item_ids or [0]))}

    # Children first; FK cascades would handle it, but explicit deletes keep counts honest
    if item_ids:
        db.session.query(Photo).filter(Photo.item_id.in_(item_ids)).delete(synchronize_session=False)
        db.session.query(Item).filter(Item.id.in_(item_ids)).delete(synchronize_session=False)
    if table_ids:
        db.session.query(InvTable).filter(InvTable.id.in_(table_ids)).delete(synchronize_session=False)
    for fid in sorted(folder_ids, key=lambda i: -len(fmap.chain(i))):
        db.session.query(Folder).filter(Folder.id == fid).delete(synchronize_session=False)
    db.session.commit()
    db.session.expire_all()
    for sha in shas:
        photo_svc.delete_files_if_orphan(sha)

    from ..search import index
    index.enqueue({("reconcile", 0)})
    return {"folders": len(folder_ids), "tables": len(table_ids), "items": len(item_ids), "photos": len(shas)}


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None
