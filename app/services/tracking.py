"""Session hooks: bump tables' "last updated" and feed the search indexer.

before_flush  -> InvTable.content_updated_at/by for any content change (items, photos, columns...)
after_flush   -> collect search index tasks
after_commit  -> hand tasks to the index worker (dropped on rollback)
"""
from contextvars import ContextVar

from flask import has_request_context
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from ..models import Folder, InvTable, Item, Photo, utcnow

# InvTable attributes that count as "content" for the last-updated chip.
_TABLE_CONTENT_ATTRS = ("name", "summary", "context", "columns")
# Attributes whose change alters search docs of descendants (path text / effective state).
_TREE_ATTRS = ("name", "active", "deleted_at", "parent_id", "folder_id")

_PENDING_KEY = "search_index_tasks"


_acting_user: ContextVar = ContextVar("acting_user", default=None)


def act_as(uid):
    """Attribute writes/usage in a background thread (no request) to this user."""
    return _acting_user.set(uid)


def act_as_reset(token):
    _acting_user.reset(token)


def current_user_id():
    acting = _acting_user.get()
    if acting is not None:
        return acting
    if not has_request_context():
        return None
    from flask_login import current_user
    try:
        return current_user.id if current_user.is_authenticated else None
    except Exception:  # noqa: BLE001 - user loader errors shouldn't break writes
        return None


def _changed(obj, attrs) -> bool:
    state = inspect(obj)
    return any(state.attrs[a].history.has_changes() for a in attrs if a in state.attrs)


def _table_id_for_photo(session: Session, photo: Photo):
    if photo.item is not None:
        return photo.item.table_id
    if photo.item_id:
        item = session.get(Item, photo.item_id)
        return item.table_id if item else None
    return None


@event.listens_for(Session, "before_flush")
def _bump_content_updated(session: Session, _ctx, _instances):
    table_ids: set[int] = set()
    direct_tables: list[InvTable] = []
    uid = current_user_id()
    for obj in session.new:
        if isinstance(obj, Item) and obj.created_by is None:
            obj.created_by = uid

    for obj in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(obj, Item):
            if obj in session.dirty and not session.is_modified(obj, include_collections=False):
                continue
            if obj.table_id:
                table_ids.add(obj.table_id)
            elif obj.table is not None:
                direct_tables.append(obj.table)
        elif isinstance(obj, Photo):
            if obj in session.dirty and not session.is_modified(obj, include_collections=False):
                continue
            tid = _table_id_for_photo(session, obj)
            if tid:
                table_ids.add(tid)
        elif isinstance(obj, InvTable) and obj in session.dirty:
            if _changed(obj, _TABLE_CONTENT_ATTRS):
                direct_tables.append(obj)

    if not table_ids and not direct_tables:
        return
    now = utcnow()
    tables = direct_tables + [t for tid in table_ids if (t := session.get(InvTable, tid)) is not None]
    for t in tables:
        if t in session.deleted:
            continue
        t.content_updated_at = now
        t.content_updated_by = uid


@event.listens_for(Session, "after_flush")
def _collect_index_tasks(session: Session, _ctx):
    tasks = session.info.setdefault(_PENDING_KEY, set())
    for obj in session.new | session.dirty:
        if isinstance(obj, Item):
            if obj.id:
                tasks.add(("item", obj.id))
        elif isinstance(obj, InvTable):
            if obj.id:
                tasks.add(("table", obj.id))
                if obj in session.dirty and _changed(obj, _TREE_ATTRS):
                    tasks.add(("table_tree", obj.id))
        elif isinstance(obj, Folder):
            if obj.id:
                tasks.add(("folder", obj.id))
                if obj in session.dirty and _changed(obj, _TREE_ATTRS):
                    tasks.add(("folder_tree", obj.id))
    for obj in session.deleted:
        if isinstance(obj, (Item, InvTable, Folder)):
            tasks.add(("reconcile", 0))


@event.listens_for(Session, "after_commit")
def _dispatch_index_tasks(session: Session):
    tasks = session.info.pop(_PENDING_KEY, None)
    if tasks:
        from ..search import index
        index.enqueue(tasks)


@event.listens_for(Session, "after_soft_rollback")
def _drop_index_tasks(session: Session, _previous_transaction):
    session.info.pop(_PENDING_KEY, None)
