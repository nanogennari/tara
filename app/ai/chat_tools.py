"""Tools available to the "Ask AI" assistant.

Data tools run on the server and are read-only. UI tools (open_*) are recorded as actions for
the browser to perform after the reply arrives; the model only gets an acknowledgement.
"""
import json

from sqlalchemy import func

from .. import quantity
from ..extensions import db
from ..models import Folder, InvTable, Item
from ..search.query import Scope, search
from ..services.tree import FolderMap, tree_payload

MAX_RESULT_CHARS = 14000

TOOLS = [
    {
        "name": "search_inventory",
        "description": "Search items, tables and folders by meaning and by text (typos and other languages are fine). "
                       "Use this first to find where something is. Archived inventories are excluded unless include_archived is true.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for"},
                "folder_ids": {"type": "array", "items": {"type": "integer"}, "description": "Only search inside these folders (and their subfolders)"},
                "table_ids": {"type": "array", "items": {"type": "integer"}, "description": "Only search inside these tables"},
                "include_archived": {"type": "boolean"},
                "limit": {"type": "integer", "description": "Max results (default 15, max 50)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_folder",
        "description": "List the subfolders and tables directly inside a folder (omit folder_id for the top level), with item counts and last-updated dates.",
        "parameters": {"type": "object", "properties": {"folder_id": {"type": "integer"}}},
    },
    {
        "name": "get_table",
        "description": "Get a table's details, columns and rows (items). Optionally filter rows by a text contained in description/observation/custom fields.",
        "parameters": {
            "type": "object",
            "properties": {
                "table_id": {"type": "integer"},
                "contains": {"type": "string", "description": "Only rows containing this text"},
                "limit": {"type": "integer", "description": "Max rows (default 200)"},
            },
            "required": ["table_id"],
        },
    },
    {
        "name": "get_items",
        "description": "Get full details of specific items by id (location path, quantity, observation, custom fields, who added it and when).",
        "parameters": {"type": "object", "properties": {"item_ids": {"type": "array", "items": {"type": "integer"}}},
                       "required": ["item_ids"]},
    },
    {
        "name": "inventory_overview",
        "description": "Overview of the whole inventory: folder tree with tables, item counts, archived state and last update.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "open_table",
        "description": "Show a table to the user in the app, optionally highlighting specific items. Use when the user asks to see/open/show something.",
        "parameters": {"type": "object", "properties": {
            "table_id": {"type": "integer"},
            "highlight_item_ids": {"type": "array", "items": {"type": "integer"}}},
            "required": ["table_id"]},
    },
    {
        "name": "open_folder",
        "description": "Show a folder overview to the user in the app.",
        "parameters": {"type": "object", "properties": {"folder_id": {"type": "integer"}}, "required": ["folder_id"]},
    },
    {
        "name": "show_search_results",
        "description": "Open the full search results page for a query in the app.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "folder_ids": {"type": "array", "items": {"type": "integer"}}},
            "required": ["query"]},
    },
]

UI_TOOLS = {"open_table", "open_folder", "show_search_results"}


def _ints(v) -> list[int]:
    out = []
    for x in v or []:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            pass
    return out


def _item_row(it: Item, columns=None) -> dict:
    labels = {c["key"]: c.get("label") or c["key"] for c in (columns or [])}
    row = {"id": it.id, "description": it.description, "quantity": quantity.format(it.quantity)}
    if it.observation:
        row["observation"] = it.observation[:400]
    if it.custom:
        row["fields"] = {labels.get(k, k): v for k, v in it.custom.items() if v not in (None, "")}
    if it.photos:
        row["photos"] = len(it.photos)
    return row


def _clip(data) -> str:
    s = json.dumps(data, ensure_ascii=False, default=str)
    if len(s) > MAX_RESULT_CHARS:
        s = s[:MAX_RESULT_CHARS] + '..."(truncated — narrow the request)"'
    return s


def run_tool(name: str, args: dict, actions: list) -> str:
    """Execute a tool; returns the JSON string given back to the model."""
    args = args if isinstance(args, dict) else {}
    try:
        if name == "search_inventory":
            scope = Scope(folders=set(_ints(args.get("folder_ids"))), tables=set(_ints(args.get("table_ids"))),
                          include_archived=bool(args.get("include_archived")))
            res = search(str(args.get("query") or ""), scope, limit=max(1, min(50, int(args.get("limit") or 15))))
            out = []
            for r in res["results"]:
                e = {"type": r["type"], "id": r["id"], "name": r["title"], "path": " / ".join(r.get("path") or [])}
                if r["type"] == "item":
                    e.update(table_id=r["table_id"], quantity=r.get("quantity"))
                    if r.get("observation"):
                        e["observation"] = r["observation"][:200]
                if not r["active"]:
                    e["archived"] = True
                out.append(e)
            return _clip({"results": out})

        if name == "list_folder":
            fid = args.get("folder_id")
            fid = int(fid) if fid not in (None, "", 0) else None
            payload = tree_payload(include_archived=True)
            fmap = FolderMap()
            return _clip({
                "folder": {"id": fid, "path": " / ".join(fmap.path(fid)) or "(top level)"},
                "subfolders": [{"id": f["id"], "name": f["name"], "archived": not f["effective_active"],
                                "last_update": f["content_updated_at"]} for f in payload["folders"] if f["parent_id"] == fid],
                "tables": [{"id": t["id"], "name": t["name"], "summary": t["summary"], "items": t["item_count"],
                            "archived": not t["effective_active"], "last_update": t["content_updated_at"],
                            "updated_by": t["content_updated_by"]} for t in payload["tables"] if t["folder_id"] == fid],
            })

        if name == "get_table":
            t = db.session.get(InvTable, int(args.get("table_id") or 0))
            if t is None or t.deleted_at is not None:
                return _clip({"error": "table not found"})
            fmap = FolderMap()
            needle = str(args.get("contains") or "").lower().strip()
            rows = []
            for it in t.live_items():
                row = _item_row(it, t.columns)
                if needle and needle not in json.dumps(row, ensure_ascii=False).lower():
                    continue
                rows.append(row)
            limit = max(1, min(500, int(args.get("limit") or 200)))
            return _clip({
                "id": t.id, "name": t.name, "path": " / ".join(fmap.path(t.folder_id)), "summary": t.summary,
                "context": t.context, "archived": not (t.active and fmap.effective_active(t.folder_id)),
                "last_update": t.to_dict()["content_updated_at"],
                "columns": [c.get("label") or c["key"] for c in t.columns if not c.get("hidden")],
                "total_rows": len(rows), "rows": rows[:limit],
            })

        if name == "get_items":
            ids = _ints(args.get("item_ids"))[:50]
            fmap = FolderMap()
            out = []
            for it in db.session.query(Item).filter(Item.id.in_(ids or [0]), Item.deleted_at.is_(None)):
                t = it.table
                d = _item_row(it, t.columns)
                d.update(table_id=t.id, table=t.name, path=" / ".join(fmap.path(t.folder_id)),
                         added_by=it.creator.name if it.creator else None, added_at=it.to_dict()["created_at"],
                         ai_generated=it.ai_generated)
                out.append(d)
            return _clip({"items": out})

        if name == "inventory_overview":
            payload = tree_payload(include_archived=True)
            fmap = FolderMap()
            total = db.session.query(func.count(Item.id)).filter(Item.deleted_at.is_(None)).scalar()
            tables = [{"id": t["id"], "name": t["name"], "path": " / ".join(fmap.path(t["folder_id"])),
                       "items": t["item_count"], "archived": not t["effective_active"],
                       "last_update": t["content_updated_at"]} for t in payload["tables"]]
            folders = [{"id": f["id"], "path": " / ".join(fmap.path(f["id"])), "archived": not f["effective_active"]}
                       for f in payload["folders"]]
            return _clip({"total_items": total, "folders": folders, "tables": tables})

        if name in UI_TOOLS:
            if name == "open_table":
                t = db.session.get(InvTable, int(args.get("table_id") or 0))
                if t is None or t.deleted_at is not None:
                    return _clip({"error": "table not found"})
                actions.append({"type": "open_table", "table_id": t.id, "highlight": _ints(args.get("highlight_item_ids"))})
            elif name == "open_folder":
                f = db.session.get(Folder, int(args.get("folder_id") or 0))
                if f is None or f.deleted_at is not None:
                    return _clip({"error": "folder not found"})
                actions.append({"type": "open_folder", "folder_id": f.id})
            else:
                actions.append({"type": "open_search", "query": str(args.get("query") or ""),
                                "folder_ids": _ints(args.get("folder_ids"))})
            return _clip({"ok": True, "note": "Shown to the user."})
    except (TypeError, ValueError) as exc:
        return _clip({"error": f"bad arguments: {exc}"})
    return _clip({"error": f"unknown tool {name}"})


def describe_step(name: str, args: dict) -> str:
    """Short human label for the UI ("Searched “tape”")."""
    args = args or {}
    return {
        "search_inventory": f"Searched “{args.get('query', '')}”",
        "list_folder": "Looked inside a folder" if args.get("folder_id") else "Looked at the top level",
        "get_table": "Read a table" + (f" (rows with “{args['contains']}”)" if args.get("contains") else ""),
        "get_items": "Read item details",
        "inventory_overview": "Reviewed the whole inventory",
        "open_table": "Opened a table",
        "open_folder": "Opened a folder",
        "show_search_results": "Opened search results",
    }.get(name, name)
