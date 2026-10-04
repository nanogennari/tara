"""Hybrid search: trigram FTS + embeddings, fused with Reciprocal Rank Fusion."""
import logging
import threading
from dataclasses import dataclass, field

import numpy as np

from ..extensions import db
from ..models import Folder, InvTable, Item, SearchDoc
from ..services import settings
from ..services.tree import FolderMap
from . import embed, fts, index

log = logging.getLogger(__name__)

RRF_K = 60


@dataclass
class Scope:
    folders: set[int] = field(default_factory=set)
    exclude_folders: set[int] = field(default_factory=set)
    tables: set[int] = field(default_factory=set)
    include_archived: bool = False

    @property
    def restricted(self) -> bool:
        return bool(self.folders or self.tables)

    def allows(self, table_id, folder_ids: str) -> bool:
        chain = _parse_chain(folder_ids)
        if self.exclude_folders and chain & self.exclude_folders:
            return False
        if not self.restricted:
            return True
        return bool(chain & self.folders) or (table_id is not None and table_id in self.tables)


def _parse_chain(s: str) -> set[int]:
    return {int(x) for x in (s or "").split(",") if x}


# ---------------------------------------------------------------- vector cache

class _VectorCache:
    """All current-model vectors in one matrix; reloaded when the index version changes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.key = None
        self.ids = np.zeros(0, dtype=np.int64)
        self.matrix = np.zeros((0, 0), dtype=np.float32)

    def get(self, model_id: str):
        key = (model_id, index.version())
        with self.lock:
            if key != self.key:
                rows = (db.session.query(SearchDoc.id, SearchDoc.embedding)
                        .filter(SearchDoc.embed_model == model_id, SearchDoc.embedding.isnot(None),
                                SearchDoc.deleted.is_(False)).all())
                if rows:
                    self.ids = np.array([r[0] for r in rows], dtype=np.int64)
                    self.matrix = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                else:
                    self.ids = np.zeros(0, dtype=np.int64)
                    self.matrix = np.zeros((0, 0), dtype=np.float32)
                self.key = key
            return self.ids, self.matrix


_vectors = _VectorCache()


def _semantic(q: str, limit: int) -> tuple[list[tuple[int, float]], str | None]:
    model_id = embed.current_model_id()
    if not model_id:
        return [], None
    try:
        embedder = embed.get_embedder()
        qv = embedder.embed([q], kind="query")[0]
    except Exception as exc:  # noqa: BLE001 - search must degrade to lexical
        log.warning("query embedding failed: %s", exc)
        return [], str(exc)[:200]
    ids, matrix = _vectors.get(model_id)
    if not len(ids) or matrix.shape[1] != qv.shape[0]:
        return [], None
    sims = matrix @ qv
    min_sim = float(settings.get("search.min_similarity") or 0.0)
    top = np.argsort(-sims)[: limit * 4]
    return [(int(ids[i]), float(sims[i])) for i in top if sims[i] >= min_sim], None


# ---------------------------------------------------------------- search

def search(q: str, scope: Scope | None = None, limit: int = 30) -> dict:
    scope = scope or Scope()
    q = (q or "").strip()
    if not q:
        return {"query": q, "results": [], "semantic_error": None}

    lexical = fts.search(db.session.connection(), q, limit=limit * 4)
    semantic, sem_err = _semantic(q, limit)

    cand_ids = {d for d, _ in lexical} | {d for d, _ in semantic}
    if not cand_ids:
        return {"query": q, "results": [], "semantic_error": sem_err}
    docs = {d.id: d for d in db.session.query(SearchDoc).filter(SearchDoc.id.in_(cand_ids))}

    def visible(doc_id):
        d = docs.get(doc_id)
        if d is None or d.deleted:
            return False
        if not d.active and not scope.include_archived:
            return False
        return scope.allows(d.table_id, d.folder_ids)

    lexical = [(d, s) for d, s in lexical if visible(d)]
    semantic = [(d, s) for d, s in semantic if visible(d)]

    fused: dict[int, dict] = {}
    for rank, (doc_id, score) in enumerate(lexical):
        e = fused.setdefault(doc_id, {"score": 0.0, "match": set()})
        e["score"] += 1.0 / (RRF_K + rank + 1)
        e["match"].add("lexical")
        e["lexical_score"] = score
    for rank, (doc_id, sim) in enumerate(semantic):
        e = fused.setdefault(doc_id, {"score": 0.0, "match": set()})
        e["score"] += 1.0 / (RRF_K + rank + 1)
        e["match"].add("semantic")
        e["similarity"] = round(sim, 3)

    ranked = sorted(fused.items(), key=lambda kv: kv[1]["score"], reverse=True)[:limit]
    results = _hydrate([(docs[d], meta) for d, meta in ranked])
    return {"query": q, "results": results, "semantic_error": sem_err}


def _hydrate(pairs) -> list[dict]:
    fmap = FolderMap()
    item_ids = [d.entity_id for d, _ in pairs if d.entity_type == "item"]
    table_ids = {d.table_id for d, _ in pairs if d.table_id} | {
        d.entity_id for d, _ in pairs if d.entity_type == "table"}
    items = {i.id: i for i in db.session.query(Item).filter(Item.id.in_(item_ids))} if item_ids else {}
    tables = {t.id: t for t in db.session.query(InvTable).filter(InvTable.id.in_(table_ids))} if table_ids else {}

    out = []
    for d, meta in pairs:
        base = {
            "type": d.entity_type, "id": d.entity_id, "title": d.title,
            "active": d.active, "score": round(meta["score"], 5),
            "match": sorted(meta["match"]), "similarity": meta.get("similarity"),
        }
        if d.entity_type == "item":
            it = items.get(d.entity_id)
            t = tables.get(d.table_id)
            if it is None or t is None:
                continue
            itd = it.to_dict()
            base.update({
                "table_id": t.id, "table_name": t.name, "path": fmap.path(t.folder_id) + [t.name],
                "quantity": itd["quantity_display"], "observation": it.observation or "",
                "thumb": itd["photos"][0]["thumb"] if itd["photos"] else None,
                "table_updated_at": t.to_dict()["content_updated_at"],
                "created_at": itd["created_at"], "created_by": itd["created_by"],
            })
        elif d.entity_type == "table":
            t = tables.get(d.entity_id)
            if t is None:
                continue
            base.update({
                "table_id": t.id, "path": fmap.path(t.folder_id), "summary": t.summary or "",
                "item_count": t.live_item_count(),
                "table_updated_at": t.to_dict()["content_updated_at"],
            })
        else:
            f = db.session.get(Folder, d.entity_id)
            if f is None:
                continue
            base.update({"folder_id": f.id, "path": fmap.path(f.parent_id)})
        out.append(base)
    return out


def similar_items(texts: list[str], exclude_table: int | None = None, threshold: float = 0.8,
                  per_text: int = 3) -> list[list[dict]]:
    """For the AI wizard: likely duplicates of each proposed description among active items."""
    model_id = embed.current_model_id()
    if not model_id or not texts:
        return [[] for _ in texts]
    try:
        qv = embed.get_embedder().embed(texts, kind="query")
    except Exception:  # noqa: BLE001
        return [[] for _ in texts]
    ids, matrix = _vectors.get(model_id)
    if not len(ids) or matrix.shape[1] != qv.shape[1]:
        return [[] for _ in texts]
    sims = qv @ matrix.T
    docs_needed = set()
    picks = []
    for row in sims:
        order = np.argsort(-row)[: per_text * 5]
        cand = [(int(ids[i]), float(row[i])) for i in order if row[i] >= threshold]
        picks.append(cand)
        docs_needed.update(d for d, _ in cand)
    docs = {d.id: d for d in db.session.query(SearchDoc).filter(SearchDoc.id.in_(docs_needed))} if docs_needed else {}
    tables = {}
    out = []
    for cand in picks:
        hits = []
        for doc_id, sim in cand:
            d = docs.get(doc_id)
            if d is None or d.entity_type != "item" or not d.active or d.deleted:
                continue
            if d.table_id not in tables:
                tables[d.table_id] = db.session.get(InvTable, d.table_id)
            t = tables[d.table_id]
            hits.append({"item_id": d.entity_id, "title": d.title, "table_id": d.table_id,
                         "table_name": t.name if t else "", "same_table": d.table_id == exclude_table,
                         "similarity": round(sim, 3)})
            if len(hits) >= per_text:
                break
        out.append(hits)
    return out
