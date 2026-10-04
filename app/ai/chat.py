"""'Ask AI' assistant: provider-neutral tool-calling loop.

History from the browser contains only plain-text turns (user text, assistant final text).
Within one request the loop is append-only: each provider adapter keeps its own native
message list (Claude's content blocks, including thinking, are replayed verbatim), the
system prompt and tool set stay frozen, and the screen context is part of the new user turn.
"""
import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime

from ..services import settings
from ..services.tracking import current_user_id
from . import chat_tools, usage
from .providers import AIError, AnthropicProvider, GoogleProvider, OllamaProvider, OpenAIProvider, get_provider

log = logging.getLogger(__name__)

MAX_STEPS = 8
MAX_HISTORY_TURNS = 20
RATE_LIMIT, RATE_WINDOW = 20, 60
_calls: dict[int, deque] = defaultdict(deque)
_lock = threading.Lock()

SYSTEM = """You are the assistant inside "{app}", an inventory of physical items stored in boxes, bags and \
other containers. Items live in tables (one table per container) organised in nested folders.

{org}You can see what the user has on screen: each user message starts with a <screen> block describing \
the open tab and any selected rows or folders. When the user says "this", "these" or "here", they mean \
what is on screen.

Use the tools to look things up — never guess what is in the inventory. Search first when you don't \
know where something is; read tables or items for details. When the user asks to see, open or show \
something, use the open_* tools so the app navigates there; when the answer is about specific rows of a \
table, open that table with highlight_item_ids set to those rows.

Answer in the user's language, briefly. When you mention specific items, tables or folders, link them \
with markdown so the user can click: [Neosporin](item:12), [Box 01](table:3), [Rio](folder:2). Mention \
where things are (folder / table path). Say clearly when something is archived (an old inventory) or \
when quantities are estimates (shown with ~).

You can't change data. If the user wants to edit something, tell them how in the app: edit cells inline, \
"Add with AI", or guided add mode.

Today is {today}."""


def _check_rate(uid):
    now = time.time()
    with _lock:
        q = _calls[uid or 0]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            raise AIError("Too many questions in a short time. Wait a moment and try again.")
        q.append(now)


def _system_prompt() -> str:
    org = (settings.get("ai.org_context") or "").strip()
    return SYSTEM.format(
        app=settings.get("app.name"), org=f"About the organisation: {org}\n\n" if org else "",
        today=datetime.now(settings.tz()).strftime("%Y-%m-%d (%A)"))


def screen_text(ctx: dict | None) -> str:
    """Render the browser's screen context as a compact block for the model."""
    if not ctx:
        return "<screen>No context shared.</screen>"
    lines = []
    a = ctx.get("active") or {}
    if a.get("type") == "table":
        lines.append(f"Viewing table [{a.get('name')}](table:{a.get('id')}) at {a.get('path') or 'top level'}"
                     f"{' (archived)' if a.get('archived') else ''}, {a.get('item_count', '?')} rows.")
        if ctx.get("filter"):
            lines.append(f"Rows filtered by: “{ctx['filter']}”.")
    elif a.get("type") == "folder":
        lines.append(f"Viewing folder [{a.get('name')}](folder:{a.get('id')}) at {a.get('path') or 'top level'}.")
    elif a.get("type"):
        lines.append(f"Viewing the {a['type']} page.")
    else:
        lines.append("No table or folder open.")
    rows = ctx.get("selected_rows") or []
    if rows:
        lines.append(f"Selected rows ({len(rows)}):")
        lines.extend(f"- [{str(r.get('description') or '(empty)')[:80]}](item:{r.get('id')}) — {r.get('quantity') or ''}"
                     for r in rows[:40])
    nodes = ctx.get("selected_nodes") or []
    if nodes:
        lines.append("Selected in sidebar: " + ", ".join(f"[{n.get('name')}]({n.get('type')}:{n.get('id')})" for n in nodes[:30]))
    return "<screen>\n" + "\n".join(lines) + "\n</screen>"


# ---------------------------------------------------------------- adapters

class _Adapter:
    def __init__(self, provider, system, history, user_text):
        self.p = provider
        self.system = system
        self.history = history
        self.user_text = user_text

    def step(self) -> tuple[str, list[dict]]:
        """Returns (text, tool_calls[{id, name, args}])."""
        raise NotImplementedError

    def add_results(self, results: list[tuple[dict, str]]):
        raise NotImplementedError


class _AnthropicAdapter(_Adapter):
    def __init__(self, *a):
        super().__init__(*a)
        self.tools = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                      for t in chat_tools.TOOLS]
        self.messages = [{"role": m["role"], "content": m["content"]} for m in self.history]
        self.messages.append({"role": "user", "content": self.user_text})

    def step(self):
        kwargs = dict(model=self.p.model, max_tokens=min(self.p.max_tokens, 16000), system=self.system,
                      tools=self.tools, messages=self.messages,
                      output_config={"effort": "medium"})
        if self.p.conf.get("fallback", True):
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        msg = self.p.stream(**kwargs)
        # Replay the assistant turn verbatim (thinking blocks included) for the next step
        self.messages.append({"role": "assistant", "content": msg.content})
        if msg.stop_reason == "refusal":
            return "Sorry — I can't help with that request.", []
        text = "".join(b.text for b in msg.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "args": b.input} for b in msg.content if b.type == "tool_use"]
        return text, calls

    def add_results(self, results):
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": c["id"], "content": out} for c, out in results]})


class _OpenAIAdapter(_Adapter):
    def __init__(self, *a):
        super().__init__(*a)
        self.tools = [{"type": "function", "function": t} for t in chat_tools.TOOLS]
        self.messages = [{"role": "system", "content": self.system}] + \
            [{"role": m["role"], "content": m["content"]} for m in self.history] + \
            [{"role": "user", "content": self.user_text}]

    def step(self):
        kwargs = dict(model=self.p.model, messages=self.messages, tools=self.tools)
        if self.p._reasoning_model():
            kwargs["max_completion_tokens"] = self.p.max_tokens
        else:
            kwargs.update(max_tokens=min(self.p.max_tokens, 8000), temperature=self.p.temperature)
        resp = self.p.create(**kwargs)
        m = resp.choices[0].message
        self.messages.append(m.model_dump(exclude_none=True))
        calls = []
        for tc in m.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append({"id": tc.id, "name": tc.function.name, "args": args})
        return m.content or "", calls

    def add_results(self, results):
        for c, out in results:
            self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})


class _GoogleAdapter(_Adapter):
    def __init__(self, *a):
        super().__init__(*a)
        from google.genai import types
        self.types = types
        self.contents = [types.Content(role="model" if m["role"] == "assistant" else "user",
                                       parts=[types.Part.from_text(text=m["content"])]) for m in self.history]
        self.contents.append(types.Content(role="user", parts=[types.Part.from_text(text=self.user_text)]))
        self.config = types.GenerateContentConfig(
            system_instruction=self.system, temperature=self.p.temperature, max_output_tokens=min(self.p.max_tokens, 16000),
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"], parameters_json_schema=t["parameters"])
                for t in chat_tools.TOOLS])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    def step(self):
        resp = self.p.call(self.contents, self.config)
        cand = (resp.candidates or [None])[0]
        if cand is None or cand.content is None:
            return "", []
        self.contents.append(cand.content)
        text = "".join(p.text for p in cand.content.parts or [] if getattr(p, "text", None) and not getattr(p, "thought", False))
        calls = [{"id": fc.id or uuid.uuid4().hex, "name": fc.name, "args": dict(fc.args or {})}
                 for fc in (resp.function_calls or [])]
        return text, calls

    def add_results(self, results):
        t = self.types
        self.contents.append(t.Content(role="user", parts=[
            t.Part.from_function_response(name=c["name"], response={"result": json.loads(out)}) for c, out in results]))


class _OllamaAdapter(_Adapter):
    def __init__(self, *a):
        super().__init__(*a)
        self.tools = [{"type": "function", "function": t} for t in chat_tools.TOOLS]
        self.messages = [{"role": "system", "content": self.system}] + \
            [{"role": m["role"], "content": m["content"]} for m in self.history] + \
            [{"role": "user", "content": self.user_text}]

    def step(self):
        think = bool(self.p.conf.get("think"))
        data = self.p.chat_request({
            "model": self.p.model, "stream": False, "messages": self.messages, "tools": self.tools, "think": think,
            "options": {"temperature": self.p.temperature, "num_predict": min(self.p.max_tokens, 8000) * (3 if think else 1)},
        })
        m = data.get("message") or {}
        self.messages.append({k: v for k, v in m.items() if k in ("role", "content", "tool_calls", "thinking")})
        calls = []
        for i, tc in enumerate(m.get("tool_calls") or []):
            fn = tc.get("function") or {}
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append({"id": tc.get("id") or f"call_{i}", "name": fn.get("name"), "args": args})
        return m.get("content") or "", calls

    def add_results(self, results):
        for c, out in results:
            self.messages.append({"role": "tool", "content": out, "tool_name": c["name"]})


def _adapter_for(provider):
    if isinstance(provider, AnthropicProvider):
        return _AnthropicAdapter
    if isinstance(provider, OpenAIProvider):
        return _OpenAIAdapter
    if isinstance(provider, GoogleProvider):
        return _GoogleAdapter
    if isinstance(provider, OllamaProvider):
        return _OllamaAdapter
    raise AIError("This AI provider doesn't support the assistant")


# ---------------------------------------------------------------- entry point

def _clean_history(history) -> list[dict]:
    out = []
    for m in (history or [])[-MAX_HISTORY_TURNS * 2:]:
        if isinstance(m, dict) and m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip():
            out.append({"role": m["role"], "content": str(m["content"])[:8000]})
    while out and out[0]["role"] != "user":  # providers expect the conversation to start with the user
        out.pop(0)
    return out


def ask(message: str, history=None, context: dict | None = None, provider=None) -> dict:
    message = (message or "").strip()
    if not message:
        raise AIError("Type a question")
    _check_rate(current_user_id())
    provider = provider or get_provider()
    adapter = _adapter_for(provider)(provider, _system_prompt(), _clean_history(history),
                                     f"{screen_text(context)}\n\n{message[:4000]}")
    actions: list[dict] = []
    steps: list[dict] = []
    text = ""
    with usage.track(provider, "chat"):
        for _ in range(MAX_STEPS):
            text, calls = adapter.step()
            if not calls:
                break
            results = []
            for c in calls:
                out = chat_tools.run_tool(c["name"], c["args"], actions)
                steps.append({"tool": c["name"], "label": chat_tools.describe_step(c["name"], c["args"])})
                results.append((c, out))
            adapter.add_results(results)
        else:
            text = (text + "\n\n" if text else "") + "_(Stopped after several lookups — ask me to continue if needed.)_"
    return {"reply": text.strip() or "Done.", "actions": actions, "steps": steps,
            "model": f"{provider.name}:{provider.model}"}
