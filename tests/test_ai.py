import io
import json

from PIL import Image

from app.ai import prompt as prompt_mod
from app.ai import providers
from app.ai.schema import build_schema


def _img():
    buf = io.BytesIO()
    Image.new("RGB", (80, 60), "green").save(buf, "JPEG")
    buf.seek(0)
    return buf


class FakeProvider(providers.Provider):
    name = "fake"
    calls = []

    def generate(self, images, system, prompt, schema):
        FakeProvider.calls.append({"images": images, "system": system, "prompt": prompt, "schema": schema})
        self.add_usage(1000, 200)
        return {
            "items": [
                {"description": "Sharpie fine point, black", "quantity": {"type": "count", "value": 22, "unit": "", "text": "", "estimated": True},
                 "observation": "Counted roughly", "custom": {"expiry": "2027-02", "bogus": "x"},
                 "photo_indexes": [0, 1], "confidence": "medium"},
                {"description": "Duct tape", "quantity": {"type": "pack", "value": 2, "unit": "rolls", "text": "", "estimated": False},
                 "observation": "", "custom": {"expiry": None}, "photo_indexes": [1, 7], "confidence": "high"},
                {"description": "", "quantity": {}, "observation": "", "custom": {}, "photo_indexes": [], "confidence": "low"},
            ],
            "notes": "Check the tape brand",
        }


def test_schema_is_strict(app):
    with app.app_context():
        from app.services import inventory as inv
        t = inv.create_table("Box")
        inv.add_column(t, {"label": "Expiry", "type": "date"})
        s = build_schema(t)
        item = s["properties"]["items"]["items"]
        assert item["additionalProperties"] is False
        assert set(item["required"]) == set(item["properties"])
        assert item["properties"]["custom"]["required"] == ["expiry"]
        json.dumps(s)


def test_prompt_contains_context(app):
    with app.app_context():
        from app.services import inventory as inv
        from app.services import settings
        settings.set("ai.org_context", "SigmaCamp Brasil, a math camp")
        f = inv.create_folder("Rio")
        t = inv.create_table("Box 04", f.id, summary="Office supplies", context="Location: Storage room")
        inv.create_item(t, {"description": "Scotch tape", "quantity": "3 rolls"})
        p = prompt_mod.build_user_prompt(t, "top shelf", 2)
        for s in ("SigmaCamp Brasil", "Rio / Box 04", "Office supplies", "Storage room",
                  "Scotch tape", "3 rolls", "top shelf", "2 photo(s)", "Do NOT repeat"):
            assert s in p, s


def test_propose_and_commit(client, app, monkeypatch):
    monkeypatch.setattr(providers, "get_provider", lambda *a, **k: FakeProvider({"model": "fake-1"}))
    from app.ai import service
    monkeypatch.setattr(service, "get_provider", lambda *a, **k: FakeProvider({"model": "fake-1"}))

    t = client.post("/api/tables", json={"name": "Box 04"}).get_json()
    client.post(f"/api/tables/{t['id']}/columns", json={"label": "Expiry", "type": "date"})
    r = client.post(f"/api/tables/{t['id']}/ai/propose",
                    data={"photos": [(_img(), "a.jpg"), (_img(), "b.jpg")], "notes": "office box"},
                    content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    prop = r.get_json()
    assert len(prop["items"]) == 2  # empty description dropped
    assert prop["items"][0]["custom"] == {"expiry": "2027-02"}
    assert prop["items"][1]["photo_indexes"] == [1]  # out-of-range index dropped
    assert prop["notes"] == "Check the tape brand"
    assert len(FakeProvider.calls[-1]["images"]) == 2
    assert "office box" in FakeProvider.calls[-1]["prompt"]

    photo_ids = [p["id"] for p in prop["photos"]]
    rows = [prop["items"][0], {**prop["items"][1], "description": "Duck duct tape"}]
    r = client.post(f"/api/tables/{t['id']}/ai/commit", json={"items": rows, "photo_ids": photo_ids})
    assert r.status_code == 201
    data = client.get(f"/api/tables/{t['id']}").get_json()
    assert [i["description"] for i in data["items"]] == ["Sharpie fine point, black", "Duck duct tape"]
    assert all(i["ai_generated"] for i in data["items"])
    assert len(data["items"][0]["photos"]) == 2 and len(data["items"][1]["photos"]) == 1
    assert data["items"][0]["quantity_display"] == "~22"

    # Editing clears the AI badge
    client.patch(f"/api/items/{data['items'][0]['id']}", json={"observation": "ok"})
    assert client.get(f"/api/tables/{t['id']}").get_json()["items"][0]["ai_generated"] is False


def test_text_only_propose_with_new_columns(client, monkeypatch):
    from app.ai import service

    class Texter(FakeProvider):
        def generate(self, images, system, prompt, schema):
            FakeProvider.calls.append({"images": images, "prompt": prompt, "schema": schema})
            return {"items": [
                {"description": "Duct tape", "quantity": {"type": "pack", "value": 3, "unit": "rolls", "text": "", "estimated": False},
                 "observation": "", "custom": {}, "photo_indexes": [0], "confidence": "high",
                 "new_values": [{"column": "colour", "value": "grey"}, {"column": "Nope", "value": "x"}]}],
                "notes": "", "new_columns": [{"label": "Colour", "type": "text", "options": []},
                                             {"label": "Description", "type": "text", "options": []}]}

    monkeypatch.setattr(service, "get_provider", lambda *a, **k: Texter({"model": "fake-1"}))
    t = client.post("/api/tables", json={"name": "Box 05"}).get_json()
    assert client.post(f"/api/tables/{t['id']}/ai/propose", data={}).status_code == 422

    r = client.post(f"/api/tables/{t['id']}/ai/propose", data={"notes": "3 rolls of grey duct tape, add a colour column"})
    assert r.status_code == 200, r.get_json()
    prop = r.get_json()
    call = FakeProvider.calls[-1]
    assert call["images"] == [] and "add a colour column" in call["prompt"] and "No photos" in call["prompt"]
    assert "new_columns must be []" in call["prompt"]
    assert prop["photos"] == []
    assert prop["new_columns"] == [{"label": "Colour", "type": "text", "options": []}]  # clash with built-in dropped
    item = prop["items"][0]
    assert item["photo_indexes"] == [] and item["new_values"] == {"Colour": "grey"}

    # Rejected suggestion: no column, value dropped
    client.post(f"/api/tables/{t['id']}/ai/commit", json={"items": [item], "photo_ids": []})
    data = client.get(f"/api/tables/{t['id']}").get_json()
    assert "Colour" not in [c["label"] for c in data["columns"]] and data["items"][0]["custom"] == {}

    # Accepted suggestion: column created and filled
    client.post(f"/api/tables/{t['id']}/ai/commit",
                json={"items": [item], "photo_ids": [], "new_columns": prop["new_columns"]})
    data = client.get(f"/api/tables/{t['id']}").get_json()
    col = next(c for c in data["columns"] if c["label"] == "Colour")
    assert data["items"][1]["custom"] == {col["key"]: "grey"}


def test_ai_rate_limit(client, monkeypatch):
    from app.ai import service
    monkeypatch.setattr(service, "get_provider", lambda *a, **k: FakeProvider({"model": "fake-1"}))
    service._calls.clear()
    t = client.post("/api/tables", json={"name": "B"}).get_json()
    codes = [client.post(f"/api/tables/{t['id']}/ai/propose", data={"photos": [(_img(), "a.jpg")]},
                         content_type="multipart/form-data").status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 422


def test_anthropic_request_shape(monkeypatch):
    captured = {}

    class Msg:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text", "text": '{"items": [], "notes": ""}'})()]

    class Stream:
        def __init__(self, **kw):
            captured.update(kw)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def get_final_message(self):
            return Msg()

    class Client:
        class beta:
            class messages:
                stream = staticmethod(lambda **kw: Stream(**kw))

    p = providers.AnthropicProvider({"api_key": "k", "model": "claude-opus-5-5", "effort": "high", "fallback": True})
    monkeypatch.setattr(p, "_client", lambda: Client)
    out = p.generate([b"jpg"], "sys", "prompt", {"type": "object"})
    assert out == {"items": [], "notes": ""}
    assert captured["output_config"]["format"]["type"] == "json_schema"
    assert captured["fallbacks"] == "default"
    assert "temperature" not in captured and "tool_choice" not in captured
    content = captured["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "Photo 0"} and content[1]["type"] == "image"


def test_openai_compatible_falls_back_without_json_schema(monkeypatch):
    import openai
    calls = []

    class Resp:
        def __init__(self, text):
            msg = type("M", (), {"content": text, "refusal": None})()
            self.choices = [type("C", (), {"message": msg, "finish_reason": "stop"})()]

    def create(**kw):
        calls.append(kw)
        if kw.get("response_format", {}).get("type") == "json_schema":
            raise openai.BadRequestError("no json_schema", response=type("R", (), {"status_code": 400, "headers": {}, "request": None})(), body=None)
        return Resp('```json\n{"items": [], "notes": "ok"}\n```')

    class Client:
        class chat:
            class completions:
                pass
    Client.chat.completions.create = staticmethod(create)
    p = providers.OpenAIProvider({"model": "llava", "base_url": "http://x"}, compatible=True)
    monkeypatch.setattr(p, "_client", lambda: Client)
    assert p.generate([b"x"], "s", "p", {"type": "object"})["notes"] == "ok"
    assert [c.get("response_format", {}).get("type") for c in calls] == ["json_schema", "json_object"]


def test_refine_row_and_draft(client, monkeypatch):
    from app.ai import service

    class Refiner(FakeProvider):
        def generate(self, images, system, prompt, schema):
            Refiner.last_prompt = prompt
            if "for row [1] only" in prompt:
                mk = lambda c: {"description": f"Sharpie {c}", "quantity": {"type": "count", "value": 5, "unit": "", "text": "", "estimated": False},
                                "observation": "", "custom": {}, "photo_indexes": [0], "confidence": "high"}
                return {"items": [mk("red"), mk("blue")], "notes": ""}
            return FakeProvider.generate(self, images, system, prompt, schema)

    monkeypatch.setattr(service, "get_provider", lambda *a, **k: Refiner({"model": "fake-1"}))
    service._calls.clear()
    t = client.post("/api/tables", json={"name": "Box"}).get_json()
    prop = client.post(f"/api/tables/{t['id']}/ai/propose", data={"photos": [(_img(), "a.jpg")]},
                       content_type="multipart/form-data").get_json()
    ids = [p["id"] for p in prop["photos"]]
    draft = [{**prop["items"][0], "observation": "MANUAL EDIT"}, prop["items"][1]]

    r = client.post(f"/api/tables/{t['id']}/ai/refine", json={
        "photo_ids": ids, "items": draft, "instruction": "split by colour", "row_index": 1,
        "history": ["use Portuguese names"]}).get_json()
    assert [i["description"] for i in r["items"]] == ["Sharpie fine point, black", "Sharpie red", "Sharpie blue"]
    assert r["items"][0]["observation"] == "MANUAL EDIT"  # untouched rows pass through
    assert "MANUAL EDIT" in Refiner.last_prompt and "use Portuguese names" in Refiner.last_prompt

    r = client.post(f"/api/tables/{t['id']}/ai/refine", json={
        "photo_ids": ids, "items": draft, "instruction": "count again"})
    assert r.status_code == 200 and "whole draft" in Refiner.last_prompt

    assert client.post(f"/api/tables/{t['id']}/ai/refine", json={
        "photo_ids": ids, "items": draft, "instruction": " "}).status_code == 422
    assert client.post(f"/api/tables/{t['id']}/ai/refine", json={
        "photo_ids": [999], "items": draft, "instruction": "x"}).status_code == 422


def test_malformed_output_is_retried_once(client, monkeypatch):
    from app.ai import service

    class Flaky(FakeProvider):
        n = 0

        def generate(self, *a):
            Flaky.n += 1
            if Flaky.n == 1:
                return providers._parse_json('{"items": [ broken')
            return FakeProvider.generate(self, *a)

    monkeypatch.setattr(service, "get_provider", lambda *a, **k: Flaky({"model": "f"}))
    service._calls.clear()
    t = client.post("/api/tables", json={"name": "B"}).get_json()
    r = client.post(f"/api/tables/{t['id']}/ai/propose", data={"photos": [(_img(), "a.jpg")]},
                    content_type="multipart/form-data")
    assert r.status_code == 200 and Flaky.n == 2


def test_normalise_accepts_drifted_shapes(app):
    from app.ai.schema import normalise
    with app.app_context():
        from app.services import inventory as inv
        t = inv.create_table("Box")
        out = normalise([{"description": "Black zip ties", "quantity": 1, "observation": "bundle"},
                         {"name": "Tape", "quantity": "~3 rolls", "photo_indexes": 0}], t, 1)
        assert [i["description"] for i in out["items"]] == ["Black zip ties", "Tape"]
        assert out["items"][1]["quantity"]["estimated"] and out["items"][1]["quantity"]["unit"] == "rolls"
        assert out["items"][0]["photo_indexes"] == [0]
        one = normalise({"description": "Solo", "quantity": 2}, t, 1)
        assert one["items"][0]["description"] == "Solo"
        out = normalise({"products": [{"description": "X", "quantity": {"type": "count", "value": 2}}]}, t, 2)
        assert out["items"][0]["quantity"]["value"] == 2.0


def test_usage_is_recorded_per_user(client, editor, monkeypatch):
    from app.ai import service
    monkeypatch.setattr(service, "get_provider", lambda *a, **k: FakeProvider({"model": "fake-1"}))
    service._calls.clear()
    t = client.post("/api/tables", json={"name": "B"}).get_json()
    for c in (client, editor, editor):
        assert c.post(f"/api/tables/{t['id']}/ai/propose", data={"photos": [(_img(), "a.jpg")]},
                      content_type="multipart/form-data").status_code == 200
    u = client.get("/api/admin/usage?days=7").get_json()
    by = {x["user"]: x for x in u["per_user"]}
    assert by["editor"]["calls"] == 2 and by["editor"]["input"] == 2000 and by["editor"]["output"] == 400
    assert by["admin"]["calls"] == 1
    assert u["per_kind"][0]["kind"] == "propose" and u["recent"][0]["model"] == "fake:fake-1"


def test_google_client_is_kept_alive():
    # google-genai closes its HTTP client when the Client is garbage-collected;
    # a throwaway client per call made every request fail with "client has been closed".
    p = providers.GoogleProvider({"api_key": "k", "model": "gemini-2.5-flash"})
    assert p._client() is p._client()


def test_gemini_retries_transient_errors(monkeypatch):
    from google.genai import errors

    calls = []

    class Models:
        def generate_content(self, **kw):
            calls.append(1)
            if len(calls) < 3:
                raise errors.ServerError(503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
            return type("R", (), {"usage_metadata": None, "text": "{}"})()

    p = providers.GoogleProvider({"api_key": "k", "model": "gemini-2.5-flash"})
    monkeypatch.setattr(p, "_client", lambda: type("C", (), {"models": Models()})())
    monkeypatch.setattr(providers.GoogleProvider, "RETRY_DELAYS", (0, 0))
    p.call([], None)
    assert len(calls) == 3

    calls.clear()
    monkeypatch.setattr(providers.GoogleProvider, "RETRY_DELAYS", (0,))
    import pytest
    with pytest.raises(providers.AIError, match="server error"):
        p.call([], None)


def test_gemini_network_errors_are_retried_then_reported(monkeypatch):
    import httpx
    import pytest
    calls = []

    class Models:
        def generate_content(self, **kw):
            calls.append(1)
            raise httpx.ConnectError("[Errno -3] Temporary failure in name resolution")

    p = providers.GoogleProvider({"api_key": "k", "model": "gemini-2.5-flash"})
    monkeypatch.setattr(p, "_client", lambda: type("C", (), {"models": Models()})())
    monkeypatch.setattr(providers.GoogleProvider, "RETRY_DELAYS", (0, 0))
    with pytest.raises(providers.AIError, match="Could not reach Gemini"):
        p.call([], None)
    assert len(calls) == 3


def test_photo_order_is_kept_best_first(app):
    from app.ai.schema import normalise
    with app.app_context():
        from app.services import inventory as inv
        t = inv.create_table("Box")
        out = normalise({"items": [{"description": "Splendor", "photo_indexes": [9, 4, 9, 2, 7, 1, 30]}]}, t, 10)
        # model's ranking kept (first = thumbnail), duplicates/out-of-range dropped, capped at 4
        assert out["items"][0]["photo_indexes"] == [9, 4, 2, 7]


def test_background_job_flow(client, editor, app, monkeypatch):
    """AI calls run as background jobs; the browser polls (mobile drops long requests)."""
    import time
    from app.ai import service
    monkeypatch.setattr(service, "get_provider", lambda *a, **k: FakeProvider({"model": "fake-1"}))
    service._calls.clear()
    t = client.post("/api/tables", json={"name": "B"}).get_json()
    app.config["TESTING"] = False
    try:
        r = editor.post(f"/api/tables/{t['id']}/ai/propose", data={"photos": [(_img(), "a.jpg")]},
                        content_type="multipart/form-data")
        assert r.status_code == 202
        jid = r.get_json()["job"]
        for _ in range(100):
            job = editor.get(f"/api/ai/jobs/{jid}").get_json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
    finally:
        app.config["TESTING"] = True
    assert job["status"] == "done" and len(job["result"]["items"]) == 2
    assert client.get(f"/api/ai/jobs/{jid}").status_code == 404  # other users can't read it
    by = {u["user"]: u for u in client.get("/api/admin/usage").get_json()["per_user"]}
    assert by["editor"]["calls"] == 1  # usage attributed to the requesting user


def test_ai_context_overrides(client, app):
    from app.services import settings
    with app.app_context():
        settings.set("ai.org_context", "GLOBAL CTX")
    top = client.post("/api/folders", json={"name": "Brasil"}).get_json()
    sub = client.post("/api/folders", json={"name": "Rio", "parent_id": top["id"]}).get_json()
    t = client.post("/api/tables", json={"name": "Box", "folder_id": sub["id"]}).get_json()

    def used(tid):
        with app.app_context():
            from app.services import inventory as inv
            return prompt_mod.build_user_prompt(inv.get_table(tid), "", 1)

    assert "GLOBAL CTX" in used(t["id"])
    client.patch(f"/api/folders/{top['id']}", json={"ai_context": "BRASIL CTX"})
    p = used(t["id"])
    assert "BRASIL CTX" in p and "GLOBAL CTX" not in p  # replaced, not appended
    client.patch(f"/api/tables/{t['id']}", json={"ai_context": "TABLE CTX"})
    assert "TABLE CTX" in used(t["id"])

    ctx = client.get(f"/api/ai/context?table_id={t['id']}").get_json()
    assert ctx["own"] == "TABLE CTX" and [x["name"] for x in ctx["inherited"]["sources"]] == ["Brasil"]
    client.patch(f"/api/tables/{t['id']}", json={"ai_context": "  "})  # cleared -> inherits again
    assert client.get(f"/api/ai/context?table_id={t['id']}").get_json()["own"] == ""

    with app.app_context():
        from app.ai import chat
        assert "BRASIL CTX" in chat._system_prompt({"active": {"type": "folder", "id": sub["id"]}})
        assert "GLOBAL CTX" in chat._system_prompt({"active": {"type": "search"}})


def test_ai_context_append_stacks_up_the_tree(client, app):
    from app.services import settings
    from app.services.tree import ai_context
    with app.app_context():
        settings.set("ai.org_context", "GLOBAL")
    top = client.post("/api/folders", json={"name": "Brasil"}).get_json()
    sub = client.post("/api/folders", json={"name": "Rio", "parent_id": top["id"]}).get_json()
    t = client.post("/api/tables", json={"name": "Box", "folder_id": sub["id"]}).get_json()
    client.patch(f"/api/folders/{sub['id']}", json={"ai_context": "RIO", "ai_context_append": True})
    client.patch(f"/api/tables/{t['id']}", json={"ai_context": "BOX", "ai_context_append": True})
    with app.app_context():
        assert ai_context(table_id=t["id"])["text"] == "GLOBAL\n\nRIO\n\nBOX"
    # A replacing folder above stops the walk: the global context is dropped
    client.patch(f"/api/folders/{top['id']}", json={"ai_context": "BRASIL"})
    with app.app_context():
        c = ai_context(table_id=t["id"])
        assert c["text"] == "BRASIL\n\nRIO\n\nBOX"
        assert [x["name"] for x in c["sources"]] == ["Brasil", "Rio", "Box"]
    ctx = client.get(f"/api/ai/context?table_id={t['id']}").get_json()
    assert ctx["append"] is True and ctx["inherited"]["text"] == "BRASIL\n\nRIO"
