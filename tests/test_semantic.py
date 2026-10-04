"""End-to-end hybrid search on the sample inventory with the local multilingual model."""
import io

import pytest

from sample_workbook import build, stats

pytestmark = pytest.mark.semantic


@pytest.fixture
def loaded(client):
    client.post("/api/import/xlsx", data={"file": (io.BytesIO(build()), "sample.xlsx")},
                content_type="multipart/form-data")
    return client


def items(client, q, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    r = client.get(f"/api/search?q={q}&{qs}").get_json()
    assert r["semantic_error"] is None
    return [h for h in r["results"] if h["type"] == "item"]


def test_index_fully_embedded(loaded, app):
    with app.app_context():
        from app.search import index
        st = index.status()
    assert st["stale"] == 0 and st["total"] >= stats()["items"] + stats()["tables"]


@pytest.mark.parametrize("query,expect_any", [
    ("curativo", ["Band-Aid", "Bandages"]),                  # Portuguese -> English
    ("something for cuts", ["Band-Aid", "Bandages", "Neosporin"]),
    ("painkiller", ["Advil", "Aleve", "Tylenol", "Excedrin"]),
    ("remédio para dor de cabeça", ["Advil", "Aleve", "Tylenol", "Excedrin"]),
    ("computador", ["ThinkPad"]),
    ("fita adesiva", ["tape"]),
])
def test_semantic_queries(loaded, query, expect_any):
    top = [h["title"] for h in items(loaded, query)[:5]]
    assert any(any(e.lower() in t.lower() for e in expect_any) for t in top), (query, top)


def test_semantic_hits_are_marked(loaded):
    hits = items(loaded, "painkiller")
    assert any("semantic" in h["match"] for h in hits)
