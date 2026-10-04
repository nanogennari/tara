"""SQLite FTS5 (trigram) index keyed by search_doc.id."""
from sqlalchemy import text

from .text import normalize, trigrams

FTS_TABLE = "search_fts"


def ensure(conn):
    conn.execute(text(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} "
        "USING fts5(title, body, tokenize='trigram')"
    ))


def upsert(conn, doc_id: int, title: str, body: str):
    conn.execute(text(f"DELETE FROM {FTS_TABLE} WHERE rowid = :id"), {"id": doc_id})
    conn.execute(text(f"INSERT INTO {FTS_TABLE}(rowid, title, body) VALUES (:id, :t, :b)"),
                 {"id": doc_id, "t": normalize(title), "b": normalize(body)})


def delete(conn, doc_ids):
    for doc_id in doc_ids:
        conn.execute(text(f"DELETE FROM {FTS_TABLE} WHERE rowid = :id"), {"id": doc_id})


def clear(conn):
    conn.execute(text(f"DELETE FROM {FTS_TABLE}"))


def _quote(g: str) -> str:
    return '"' + g.replace('"', '""') + '"'


def search(conn, q: str, limit: int = 100, min_coverage: float = 0.5) -> list[tuple[int, float]]:
    """[(doc_id, score)] best first.

    Trigram OR-query gives typo/partial tolerance; candidates are then re-ranked by the
    fraction of query trigrams they contain (dropping weak overlaps like "tool" for
    "zometool"), with bm25 (title weighted 3x) as tie-breaker.
    """
    nq = normalize(q)
    if not nq:
        return []
    grams = [g for g in trigrams(q) if len(g) >= 3]
    if not grams:
        # Too short for trigrams: substring scan (fine at inventory scale)
        rows = conn.execute(text(
            f"SELECT rowid FROM {FTS_TABLE} WHERE title LIKE :p OR body LIKE :p LIMIT :n"
        ), {"p": f"%{nq}%", "n": limit}).all()
        return [(r[0], 1.0) for r in rows]
    match = " OR ".join(_quote(g) for g in grams)
    rows = conn.execute(text(
        f"SELECT rowid, title, body, bm25({FTS_TABLE}, 3.0, 1.0) AS s FROM {FTS_TABLE} "
        f"WHERE {FTS_TABLE} MATCH :m ORDER BY s LIMIT :n"
    ), {"m": match, "n": limit * 4}).all()
    scored = []
    for rowid, title, body, bm in rows:
        hay = f"{title}\n{body}"
        coverage = sum(1 for g in grams if g in hay) / len(grams)
        if nq in hay:
            coverage += 1.0  # exact substring match always wins
        if coverage >= min_coverage:
            # bm25 is negative (lower = better)
            scored.append((rowid, coverage, -bm))
    scored.sort(key=lambda r: (r[1], r[2]), reverse=True)
    return [(r[0], r[1]) for r in scored[:limit]]
