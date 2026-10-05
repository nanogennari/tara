"""Folder tree helpers: effective archive/delete state, paths, subtrees, last-updated rollups.

Folder counts are small (hundreds at most), so we load the whole folder map in one
query and walk it in Python rather than issuing recursive SQL per node.
"""
from dataclasses import dataclass

from sqlalchemy import func

from ..extensions import db
from ..models import Folder, InvTable, Item, User


@dataclass
class FNode:
    id: int
    parent_id: int | None
    name: str
    active: bool
    deleted: bool


class FolderMap:
    def __init__(self, include_deleted=True):
        rows = db.session.query(Folder.id, Folder.parent_id, Folder.name, Folder.active,
                                Folder.deleted_at).all()
        self.nodes = {r.id: FNode(r.id, r.parent_id, r.name, r.active, r.deleted_at is not None)
                      for r in rows}
        self.children: dict[int | None, list[int]] = {}
        for n in self.nodes.values():
            self.children.setdefault(n.parent_id, []).append(n.id)

    def chain(self, folder_id: int | None) -> list[FNode]:
        """Ancestors from root down to folder_id (inclusive)."""
        out, seen = [], set()
        while folder_id is not None and folder_id in self.nodes and folder_id not in seen:
            seen.add(folder_id)
            n = self.nodes[folder_id]
            out.append(n)
            folder_id = n.parent_id
        return list(reversed(out))

    def path(self, folder_id: int | None) -> list[str]:
        return [n.name for n in self.chain(folder_id)]

    def effective_active(self, folder_id: int | None) -> bool:
        return all(n.active for n in self.chain(folder_id))

    def effective_deleted(self, folder_id: int | None) -> bool:
        return any(n.deleted for n in self.chain(folder_id))

    def descendants(self, folder_id: int) -> list[int]:
        """All folder ids below folder_id (exclusive)."""
        out, stack = [], list(self.children.get(folder_id, []))
        while stack:
            fid = stack.pop()
            out.append(fid)
            stack.extend(self.children.get(fid, []))
        return out

    def is_descendant(self, maybe_child: int, ancestor: int) -> bool:
        return any(n.id == ancestor for n in self.chain(maybe_child))


def ai_context(table_id: int | None = None, folder_id: int | None = None) -> dict:
    """The organisation context the AI should use here. Walks up from the table through its folders:
    a context set to replace stops the walk; one set to append is added on top of what's above it;
    reaching the top adds the global setting. Returns {text, sources: [{type, id, name} | None]}
    with sources outermost first (None = the global setting)."""
    from . import settings
    parts: list[tuple[dict | None, str]] = []  # innermost first

    def take(source, text, append) -> bool:
        """Record a node's context; True when the walk should stop here."""
        if not (text or "").strip():
            return False
        parts.append((source, text.strip()))
        return not append

    done = False
    if table_id is not None:
        t = db.session.get(InvTable, table_id)
        if t is not None:
            done = take({"type": "table", "id": t.id, "name": t.name}, t.ai_context, t.ai_context_append)
            folder_id = t.folder_id
    if not done and folder_id is not None:
        rows = {r.id: r for r in db.session.query(Folder.id, Folder.ai_context, Folder.ai_context_append)
                .filter(Folder.ai_context.isnot(None))}
        for n in reversed(FolderMap().chain(folder_id)):
            r = rows.get(n.id)
            if r and take({"type": "folder", "id": n.id, "name": n.name}, r.ai_context, r.ai_context_append):
                done = True
                break
    if not done:
        g = (settings.get("ai.org_context") or "").strip()
        if g:
            parts.append((None, g))
    parts.reverse()
    return {"text": "\n\n".join(text for _, text in parts), "sources": [src for src, _ in parts]}


def table_state(t: InvTable, fmap: FolderMap | None = None) -> dict:
    fmap = fmap or FolderMap()
    return {
        "effective_active": t.active and fmap.effective_active(t.folder_id),
        "effective_deleted": t.deleted_at is not None or fmap.effective_deleted(t.folder_id),
        "path": fmap.path(t.folder_id),
    }


def tree_payload(include_archived=True) -> dict:
    """Everything the sidebar needs in one response."""
    fmap = FolderMap()
    folders = (db.session.query(Folder).filter(Folder.deleted_at.is_(None))
               .order_by(Folder.position, Folder.name).all())
    counts = dict(db.session.query(Item.table_id, func.count(Item.id))
                  .filter(Item.deleted_at.is_(None)).group_by(Item.table_id).all())
    users = dict(db.session.query(User.id, func.coalesce(User.display_name, User.username)).all())
    tables = (db.session.query(InvTable).filter(InvTable.deleted_at.is_(None))
              .order_by(InvTable.position, InvTable.name).all())

    live_folder_ids = {f.id for f in folders if not fmap.effective_deleted(f.id)}
    t_out = []
    for t in tables:
        if t.folder_id is not None and t.folder_id not in live_folder_ids:
            continue
        eff_active = t.active and fmap.effective_active(t.folder_id)
        if not include_archived and not eff_active:
            continue
        t_out.append({
            "id": t.id, "folder_id": t.folder_id, "name": t.name, "summary": t.summary or "",
            "position": t.position, "active": t.active, "effective_active": eff_active,
            "item_count": counts.get(t.id, 0),
            "content_updated_at": _iso(t.content_updated_at),
            "content_updated_by": users.get(t.content_updated_by),
        })

    # Folder last-updated = newest table beneath it
    latest: dict[int, str] = {}
    for t in t_out:
        for n in fmap.chain(t["folder_id"]):
            cur = latest.get(n.id)
            if t["content_updated_at"] and (cur is None or t["content_updated_at"] > cur):
                latest[n.id] = t["content_updated_at"]

    f_out = []
    for f in folders:
        if f.id not in live_folder_ids:
            continue
        eff_active = fmap.effective_active(f.id)
        if not include_archived and not eff_active:
            continue
        f_out.append({
            "id": f.id, "parent_id": f.parent_id, "name": f.name, "position": f.position,
            "active": f.active, "effective_active": eff_active,
            "content_updated_at": latest.get(f.id),
        })
    return {"folders": f_out, "tables": t_out}


def _iso(dt):
    return dt.isoformat() + "Z" if dt else None
