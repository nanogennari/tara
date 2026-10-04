"""Search document construction and text normalisation."""
import hashlib
import re
import unicodedata

from .. import quantity

_WS = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Lowercase, strip accents, collapse whitespace ("Remédio  X" -> "remedio x")."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return _WS.sub(" ", s.lower()).strip()


def _custom_lines(columns: list[dict], custom: dict) -> list[str]:
    out = []
    labels = {c["key"]: c.get("label") or c["key"] for c in columns or []}
    for key, val in (custom or {}).items():
        if val in (None, "", False):
            continue
        if val is True:
            val = "yes"
        out.append(f"{labels.get(key, key)}: {val}")
    return out


def item_doc(item, table, path: list[str]) -> tuple[str, str]:
    """(title, text) for an item. Text is what gets embedded and lexically indexed."""
    title = item.description or "(no description)"
    parts = [title]
    q = quantity.format(item.quantity)
    if q:
        parts.append(f"Quantity: {q}")
    if item.observation:
        parts.append(item.observation)
    parts.extend(_custom_lines(table.columns if table else [], item.custom))
    if path:
        parts.append("In: " + " / ".join(path))
    return title, "\n".join(parts)


def table_doc(table, path: list[str]) -> tuple[str, str]:
    parts = [table.name]
    if table.summary:
        parts.append(table.summary)
    if table.context:
        parts.append(table.context)
    if path:
        parts.append("In: " + " / ".join(path))
    return table.name, "\n".join(parts)


def folder_doc(folder, path: list[str]) -> tuple[str, str]:
    parts = [folder.name]
    if len(path) > 1:
        parts.append("In: " + " / ".join(path[:-1]))
    return folder.name, "\n".join(parts)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def trigrams(q: str) -> list[str]:
    """Distinct trigrams of each normalised token (tokens < 3 chars are kept whole)."""
    seen, out = set(), []
    for tok in normalize(q).split(" "):
        tok = tok.strip()
        if not tok:
            continue
        grams = [tok] if len(tok) < 3 else [tok[i:i + 3] for i in range(len(tok) - 2)]
        for g in grams:
            if g not in seen:
                seen.add(g)
                out.append(g)
    return out
