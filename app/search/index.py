"""Background search indexer.

Tasks (kind, id):
  item/table/folder      rebuild that entity's SearchDoc + FTS row
  table_tree/folder_tree same for everything beneath (path or effective state changed)
  reconcile              add missing docs, drop docs whose entity was purged
  embed                  embed every doc whose vector is stale for the current model

A single daemon thread per process drains the queue. Lexical (FTS) rows are written
immediately; embeddings are computed in batches afterwards, so a slow or failing
embedding provider never blocks lexical search.
"""
import logging
import queue
import threading
import time

import numpy as np
from flask import Flask

from ..extensions import db
from ..models import Folder, InvTable, Item, SearchDoc
from ..services.tree import FolderMap
from . import embed, fts
from .text import content_hash, folder_doc, item_doc, table_doc

log = logging.getLogger(__name__)

BATCH = 64
RETRY_SECONDS = 60

_q: "queue.Queue[tuple[str, int]]" = queue.Queue()
_app: Flask | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()
_state = {"version": 0, "last_error": None, "last_error_at": None, "busy": False}


# ---------------------------------------------------------------- public API

def init_app(app: Flask):
    global _app
    _app = app
    with app.app_context():
        with db.engine.begin() as conn:
            fts.ensure(conn)
    if not app.config.get("INDEX_SYNC"):
        _start_thread()
    enqueue({("reconcile", 0)})


def enqueue(tasks):
    if _app is None:
        return
    if _app.config.get("INDEX_SYNC"):
        # Tests: process inline but in a separate thread/app context so we never reuse
        # the committing session.
        t = threading.Thread(target=_run_tasks, args=(set(tasks),))
        t.start()
        t.join()
        return
    for task in tasks:
        _q.put(task)


def version() -> int:
    return _state["version"]


def status() -> dict:
    model_id = embed.current_model_id()
    total = db.session.query(SearchDoc.id).filter(SearchDoc.deleted.is_(False)).count()
    fresh = 0
    if model_id:
        fresh = (db.session.query(SearchDoc.id)
                 .filter(SearchDoc.deleted.is_(False), SearchDoc.embed_model == model_id,
                         SearchDoc.embed_hash == SearchDoc.content_hash).count())
    return {
        "model": model_id, "total": total, "embedded": fresh,
        "stale": (total - fresh) if model_id else 0,
        "queue": _q.qsize(), "busy": _state["busy"],
        "last_error": _state["last_error"], "last_error_at": _state["last_error_at"],
    }


def rebuild():
    """Drop all vectors and FTS rows and re-index everything."""
    enqueue({("rebuild", 0)})


# ---------------------------------------------------------------- worker

def _start_thread():
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _thread = threading.Thread(target=_loop, name="search-indexer", daemon=True)
        _thread.start()


def _loop():
    next_retry = None
    while True:
        try:
            timeout = max(0.5, next_retry - time.time()) if next_retry else None
            first = _q.get(timeout=timeout)
            tasks = {first}
        except queue.Empty:
            tasks = {("embed", 0)}
            next_retry = None
        while len(tasks) < 500:
            try:
                tasks.add(_q.get_nowait())
            except queue.Empty:
                break
        ok = _run_tasks(tasks)
        if not ok and next_retry is None:
            next_retry = time.time() + RETRY_SECONDS


def _run_tasks(tasks: set) -> bool:
    """Returns False if embedding failed (caller schedules a retry)."""
    _state["busy"] = True
    try:
        with _app.app_context():
            try:
                return _process(tasks)
            except Exception:  # noqa: BLE001 - worker must survive anything
                log.exception("search indexing failed")
                db.session.rollback()
                return False
            finally:
                db.session.remove()
    finally:
        _state["busy"] = False


def _process(tasks: set) -> bool:
    kinds = {k for k, _ in tasks}
    fmap = FolderMap()
    if "rebuild" in kinds:
        fts.clear(db.session.connection())
        db.session.query(SearchDoc).delete()
        db.session.commit()
        kinds.add("reconcile")

    items: set[int] = set()
    tables: set[int] = set()
    folders: set[int] = set()
    for kind, eid in tasks:
        if kind == "item":
            items.add(eid)
        elif kind == "table":
            tables.add(eid)
        elif kind == "folder":
            folders.add(eid)
        elif kind == "table_tree":
            tables.add(eid)
            items.update(i for (i,) in db.session.query(Item.id).filter(Item.table_id == eid))
        elif kind == "folder_tree":
            sub = [eid] + fmap.descendants(eid)
            folders.update(sub)
            tids = [t for (t,) in db.session.query(InvTable.id).filter(InvTable.folder_id.in_(sub))]
            tables.update(tids)
            if tids:
                items.update(i for (i,) in db.session.query(Item.id).filter(Item.table_id.in_(tids)))

    if "reconcile" in kinds:
        _reconcile(items, tables, folders)

    changed = _index_entities(fmap, items, tables, folders)
    if changed:
        _bump()
    return _embed_stale()


def _reconcile(items: set, tables: set, folders: set):
    existing = {(t, i): d for t, i, d in db.session.query(SearchDoc.entity_type, SearchDoc.entity_id, SearchDoc.id)}
    live = {
        "item": {i for (i,) in db.session.query(Item.id)},
        "table": {i for (i,) in db.session.query(InvTable.id)},
        "folder": {i for (i,) in db.session.query(Folder.id)},
    }
    gone = [d for (t, i), d in existing.items() if i not in live[t]]
    if gone:
        fts.delete(db.session.connection(), gone)
        db.session.query(SearchDoc).filter(SearchDoc.id.in_(gone)).delete(synchronize_session=False)
        db.session.commit()
        _bump()
    items.update(i for i in live["item"] if ("item", i) not in existing)
    tables.update(i for i in live["table"] if ("table", i) not in existing)
    folders.update(i for i in live["folder"] if ("folder", i) not in existing)


def _chain_str(fmap: FolderMap, folder_id) -> str:
    ids = [n.id for n in fmap.chain(folder_id)]
    return "," + ",".join(map(str, ids)) + "," if ids else ""


def _index_entities(fmap: FolderMap, items: set, tables: set, folders: set) -> bool:
    docs = {(d.entity_type, d.entity_id): d for d in _existing_docs(items, tables, folders)}
    fts_rows: list[tuple[SearchDoc, str, str]] = []
    table_cache: dict[int, InvTable] = {}

    def get_table(tid):
        if tid not in table_cache:
            table_cache[tid] = db.session.get(InvTable, tid)
        return table_cache[tid]

    def upsert(etype, eid, title, body, table_id, folder_id, active, deleted):
        d = docs.get((etype, eid))
        if d is None:
            d = SearchDoc(entity_type=etype, entity_id=eid)
            db.session.add(d)
            docs[(etype, eid)] = d
        h = content_hash(body)
        text_changed = d.content_hash != h or d.title != title
        d.title, d.text, d.content_hash = title, body, h
        d.table_id, d.folder_ids = table_id, _chain_str(fmap, folder_id)
        d.active, d.deleted = active, deleted
        if text_changed or d.id is None:
            fts_rows.append((d, title, body))

    for fid in folders:
        f = db.session.get(Folder, fid)
        if f is None:
            continue
        path = fmap.path(fid)
        title, body = folder_doc(f, path)
        upsert("folder", fid, title, body, None, fid,
               fmap.effective_active(fid), fmap.effective_deleted(fid))

    for tid in tables:
        t = get_table(tid)
        if t is None:
            continue
        title, body = table_doc(t, fmap.path(t.folder_id))
        upsert("table", tid, title, body, tid, t.folder_id,
               t.active and fmap.effective_active(t.folder_id),
               t.deleted_at is not None or fmap.effective_deleted(t.folder_id))

    for chunk in _chunks(sorted(items), 500):
        for it in db.session.query(Item).filter(Item.id.in_(chunk)):
            t = get_table(it.table_id)
            if t is None:
                continue
            title, body = item_doc(it, t, fmap.path(t.folder_id) + [t.name])
            upsert("item", it.id, title, body, t.id, t.folder_id,
                   t.active and fmap.effective_active(t.folder_id),
                   it.deleted_at is not None or t.deleted_at is not None
                   or fmap.effective_deleted(t.folder_id))

    if not docs:
        return False
    db.session.flush()
    if fts_rows:
        conn = db.session.connection()
        for d, title, body in fts_rows:
            fts.upsert(conn, d.id, title, body)
    db.session.commit()
    return True


def _existing_docs(items, tables, folders):
    out = []
    for etype, ids in (("item", items), ("table", tables), ("folder", folders)):
        for chunk in _chunks(sorted(ids), 500):
            out.extend(db.session.query(SearchDoc).filter(
                SearchDoc.entity_type == etype, SearchDoc.entity_id.in_(chunk)))
    return out


def _embed_stale() -> bool:
    model_id = embed.current_model_id()
    if not model_id:
        return True
    try:
        embedder = embed.get_embedder()
    except Exception as exc:  # noqa: BLE001
        _record_error(exc)
        return False
    any_done = False
    while True:
        batch = (db.session.query(SearchDoc)
                 .filter((SearchDoc.embed_model != model_id) | SearchDoc.embed_model.is_(None)
                         | (SearchDoc.embed_hash != SearchDoc.content_hash) | SearchDoc.embed_hash.is_(None))
                 .order_by(SearchDoc.deleted, SearchDoc.id).limit(BATCH).all())
        if not batch:
            break
        try:
            vecs = embedder.embed([d.text for d in batch], kind="passage")
        except Exception as exc:  # noqa: BLE001
            _record_error(exc)
            db.session.rollback()
            return False
        for d, v in zip(batch, vecs):
            d.embedding = np.asarray(v, dtype=np.float32).tobytes()
            d.embed_model = model_id
            d.embed_hash = d.content_hash
        db.session.commit()
        any_done = True
    if any_done:
        _state["last_error"] = None
        _bump()
    return True


def _record_error(exc):
    log.warning("embedding failed: %s", exc)
    _state["last_error"] = str(exc)[:500]
    _state["last_error_at"] = time.time()


def _bump():
    _state["version"] += 1


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
