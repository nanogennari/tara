"""Typed key/value settings stored in the Setting table, with encrypted secrets."""
import base64
import copy
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from ..extensions import db
from ..models import Setting

PROVIDERS = {
    "anthropic": {
        "label": "Anthropic Claude", "needs_key": True, "default_model": "claude-opus-5-5",
        "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5", "claude-fable-5-1"],
        "base_url": "",
    },
    "openai": {
        "label": "OpenAI (ChatGPT)", "needs_key": True, "default_model": "gpt-5",
        "models": ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o"], "base_url": "",
    },
    "google": {
        "label": "Google Gemini", "needs_key": True, "default_model": "gemini-2.5-flash",
        "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"], "base_url": "",
    },
    "ollama": {
        "label": "Ollama", "hint": "Local / self-hosted models", "needs_key": False, "default_model": "qwen2.5vl",
        "models": ["qwen2.5vl", "llama3.2-vision", "gemma3", "llava"],
        "base_url": "http://host.docker.internal:11434",
    },
    "openai_compatible": {
        "label": "OpenAI-compatible", "hint": "OpenRouter, Groq, Mistral, LM Studio, vLLM…",
        "needs_key": False, "default_model": "", "models": [],
        "base_url": "https://openrouter.ai/api/v1",
    },
}


def _provider_defaults():
    return {
        name: {"api_key": "", "model": p["default_model"], "base_url": p["base_url"],
               "temperature": 0.2, "max_tokens": 16000, "timeout": 170,
               # Claude only: reasoning effort and server-side refusal fallback
               "effort": "high", "fallback": True}
        for name, p in PROVIDERS.items()
    }


DEFAULTS = {
    "app.name": "Tara",
    "app.tagline": "The self-hosted, simple, AI-assisted inventory tool",
    "app.primary_color": "#4f46e5",
    "app.theme": "auto",
    "app.logo": None,
    "app.favicon": None,
    "server.base_url": "",
    "server.image_max_px": 1600,
    "server.image_quality": 85,
    "server.session_days": 30,
    "server.allow_registration": False,
    "server.trash_days": 30,
    "server.timezone": "UTC",
    "server.stale_days": 180,
    "search.provider": "local",
    "search.model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "search.min_similarity": 0.35,
    "ai.provider": "anthropic",
    "ai.providers": _provider_defaults(),
    "ai.org_context": "",
    "ai.system_prompt": "",
    "ai.max_existing_items": 200,
}

SECRET_FIELDS = ("api_key",)
_ENC_PREFIX = "enc:"


def _fernet() -> Fernet:
    key = hashlib.sha256(current_app.config["SECRET_KEY"].encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(s: str) -> str:
    if not s:
        return ""
    return _ENC_PREFIX + _fernet().encrypt(s.encode()).decode()


def decrypt(s: str) -> str:
    if not s or not s.startswith(_ENC_PREFIX):
        return s or ""
    try:
        return _fernet().decrypt(s[len(_ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        return ""


def get(key: str):
    row = db.session.get(Setting, key)
    default = copy.deepcopy(DEFAULTS.get(key))
    if row is None:
        return default
    val = row.value
    if isinstance(default, dict) and isinstance(val, dict):
        merged = default
        for k, v in val.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
        return merged
    return val


def set(key: str, value):  # noqa: A001 - mirrors get()
    row = db.session.get(Setting, key)
    if row is None:
        row = Setting(key=key, value=value)
        db.session.add(row)
    else:
        row.value = value
    db.session.commit()


def tz():
    """Configured display timezone (ZoneInfo), falling back to UTC."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        return ZoneInfo(get("server.timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local(dt):
    """Naive-UTC datetime from the DB -> aware datetime in the configured timezone."""
    from datetime import timezone as _tz
    return dt.replace(tzinfo=_tz.utc).astimezone(tz()) if dt else None


def get_many(prefix: str) -> dict:
    return {k: get(k) for k in DEFAULTS if k.startswith(prefix)}


def ai_config(reveal=False) -> dict:
    """AI config with secrets decrypted (reveal=True) or masked."""
    providers = get("ai.providers")
    for p in providers.values():
        raw = decrypt(p.get("api_key", ""))
        if reveal:
            p["api_key"] = raw
        else:
            p["has_key"] = bool(raw)
            p["api_key"] = mask(raw)
    return {
        "provider": get("ai.provider"),
        "providers": providers,
        "org_context": get("ai.org_context"),
        "system_prompt": get("ai.system_prompt"),
        "max_existing_items": get("ai.max_existing_items"),
    }


def mask(s: str) -> str:
    if not s:
        return ""
    return s[:4] + "…" + s[-4:] if len(s) > 12 else "••••"


def save_provider(name: str, data: dict):
    providers = get("ai.providers")
    cur = providers.setdefault(name, {})
    for field in ("model", "base_url"):
        if field in data:
            cur[field] = str(data[field] or "").strip()
    if data.get("effort") in ("low", "medium", "high", "xhigh", "max"):
        cur["effort"] = data["effort"]
    for flag in ("fallback", "think"):
        if flag in data:
            cur[flag] = bool(data[flag])
    for field, typ, lo, hi in (("temperature", float, 0, 2), ("max_tokens", int, 1024, 64000),
                               ("timeout", int, 10, 600)):
        if field in data and data[field] not in (None, ""):
            cur[field] = max(lo, min(hi, typ(data[field])))
    if "api_key" in data:
        key = (data["api_key"] or "").strip()
        # A masked value means "unchanged"; empty string with clear flag removes it
        if data.get("clear_key"):
            cur["api_key"] = ""
        elif key and "…" not in key and "••••" not in key:
            cur["api_key"] = encrypt(key)
    set("ai.providers", providers)
