"""Chat assistant loop with a scripted fake provider (no network)."""
import json

from app.ai import chat, providers


class ScriptedOllama(providers.OllamaProvider):
    """Plays back tool calls, then a final answer; records what it was sent."""

    def __init__(self, script):
        super().__init__({"model": "fake", "base_url": "http://x"})
        self.script = list(script)
        self.requests = []

    def chat_request(self, body):
        self.requests.append(json.loads(json.dumps(body)))
        self.add_usage(100, 10)
        return {"message": self.script.pop(0)}


def _seed(client):
    f = client.post("/api/folders", json={"name": "Rio"}).get_json()
    t = client.post("/api/tables", json={"name": "Box 04", "folder_id": f["id"]}).get_json()
    it = client.post(f"/api/tables/{t['id']}/items", json={"description": "Duck duct tape", "quantity": "2 rolls"}).get_json()
    return f, t, it


def test_chat_tool_loop_and_actions(client, app, monkeypatch):
    f, t, it = _seed(client)
    fake = ScriptedOllama([
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "search_inventory", "arguments": {"query": "duct tape"}}}]},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "open_table", "arguments": {"table_id": t["id"], "highlight_item_ids": [it["id"]]}}}]},
        {"role": "assistant", "content": f"It's in [Box 04](table:{t['id']}): [Duck duct tape](item:{it['id']}), 2 rolls."},
    ])
    monkeypatch.setattr(chat, "get_provider", lambda: fake)
    ctx = {"active": {"type": "folder", "id": f["id"], "name": "Rio", "path": ""},
           "selected_rows": [{"id": it["id"], "description": "Duck duct tape", "quantity": "2 rolls"}]}
    r = client.post("/api/ai/chat", json={"message": "where is the tape?", "context": ctx,
                                          "history": [{"role": "assistant", "content": "orphan"}, {"role": "user", "content": "hi"},
                                                      {"role": "assistant", "content": "hello"}]})
    assert r.status_code == 200, r.get_json()
    d = r.get_json()
    assert "Box 04" in d["reply"]
    assert d["actions"] == [{"type": "open_table", "table_id": t["id"], "highlight": [it["id"]]}]
    assert [s["tool"] for s in d["steps"]] == ["search_inventory", "open_table"]

    first = fake.requests[0]["messages"]
    assert first[0]["role"] == "system" and "inventory" in first[0]["content"]
    assert [m["role"] for m in first[1:]] == ["user", "assistant", "user"]  # orphan assistant dropped
    assert "<screen>" in first[-1]["content"] and "Selected rows (1)" in first[-1]["content"]
    # search results were fed back to the model
    tool_msg = fake.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "Duck duct tape" in tool_msg["content"]
    # Append-only: request 2 starts with request 1's messages unchanged
    assert fake.requests[1]["messages"][:len(first)] == first

    usage = client.get("/api/admin/usage").get_json()
    chat_row = next(k for k in usage["per_kind"] if k["kind"] == "chat")
    assert chat_row["calls"] == 1 and chat_row["input"] == 300


def test_chat_tools_direct(client, app):
    f, t, it = _seed(client)
    from app.ai import chat_tools
    with app.test_request_context():
        actions = []
        out = json.loads(chat_tools.run_tool("get_table", {"table_id": t["id"], "contains": "duct"}, actions))
        assert out["rows"][0]["quantity"] == "2 rolls" and out["path"] == "Rio"
        out = json.loads(chat_tools.run_tool("list_folder", {"folder_id": f["id"]}, actions))
        assert out["tables"][0]["name"] == "Box 04"
        out = json.loads(chat_tools.run_tool("get_items", {"item_ids": [it["id"]]}, actions))
        assert out["items"][0]["added_by"] == "admin"
        assert "error" in json.loads(chat_tools.run_tool("open_table", {"table_id": 999}, actions))
        assert actions == []


def test_anthropic_adapter_replays_content_verbatim(monkeypatch):
    sent = []

    class Block:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class Msg:
        def __init__(self, content, stop="end_turn"):
            self.content, self.stop_reason = content, stop
            self.usage = None

    replies = [
        Msg([Block(type="thinking", thinking="", signature="sig"), Block(type="tool_use", id="tu1", name="inventory_overview", input={})], "tool_use"),
        Msg([Block(type="text", text="All good")]),
    ]
    p = providers.AnthropicProvider({"api_key": "k", "model": "claude-opus-5-5"})
    monkeypatch.setattr(p, "stream", lambda **kw: (sent.append({**kw, "messages": list(kw["messages"])}), replies.pop(0))[1])
    a = chat._AnthropicAdapter(p, "SYS", [], "<screen/> hi")
    text, calls = a.step()
    assert calls == [{"id": "tu1", "name": "inventory_overview", "args": {}}]
    a.add_results([(calls[0], "{}")])
    text, calls = a.step()
    assert text == "All good" and not calls
    second = sent[1]
    assert second["system"] == sent[0]["system"] and second["tools"] == sent[0]["tools"]
    assert second["messages"][1]["content"][0].signature == "sig"  # thinking block replayed unchanged
    assert second["messages"][2]["content"][0]["type"] == "tool_result"
    assert "temperature" not in second and "tool_choice" not in second


def test_gemini_adapter_accepts_clipped_tool_output():
    from app.ai.chat import _json_or_text
    assert _json_or_text('{"a": 1}') == {"a": 1}
    clipped = '{"items": [1, 2..."(truncated — narrow the request)"'
    assert _json_or_text(clipped) == clipped


def test_chat_wraps_up_at_the_effort_limit(client, monkeypatch):
    _seed(client)
    search = {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "search_inventory", "arguments": {"query": "paper"}}}]}
    fake = ScriptedOllama([search] * 4 + [{"role": "assistant", "content": "We have A4 paper."}])
    monkeypatch.setattr(chat, "get_provider", lambda: fake)
    r = client.post("/api/ai/chat", json={"message": "what paper do we have?", "effort": "low"})
    d = r.get_json()
    assert d["reply"] == "We have A4 paper." and len(d["steps"]) == 4
    assert fake.requests[-1]["tools"] == []  # final turn: tools disabled
    assert fake.requests[-1]["messages"][-1] == {"role": "user", "content": chat.WRAP_UP}
