"""Prompt construction for AI-assisted item cataloguing."""
import json

from .. import quantity
from ..services import settings
from ..services.tree import FolderMap

DEFAULT_SYSTEM = """You catalogue physical inventory from photos. You are given photos of the \
contents of one storage container (box, bag, shelf...) and must list every distinct item you can \
identify so it can be added to an inventory spreadsheet.

How to work:
- Look carefully at every photo. The same item may appear in several photos; list it once and \
reference all photos it appears in via photo_indexes (0-based, in the order given).
- Read labels, brands, model numbers, sizes, strengths and expiry dates when they are legible, and \
put them where the table's columns expect them.
- Count exactly when you can. When you can't (many small pieces, partially hidden items), give \
your best estimate, set quantity.estimated=true and briefly say how you estimated in observation.
- Choose the quantity type that fits: count (individual units), pack (packs/boxes/rolls/bottles... \
put the container word in unit), weight, volume, length, or text for anything that doesn't fit \
(e.g. "1 + 1"). Use only the units listed for each type, except pack which accepts any container word.
- Group identical items into one row with the total quantity. Keep different variants (sizes, \
colours, strengths) as separate rows when the difference matters.
- Match the naming style and level of detail of the existing rows when there are any.
- Do not invent items, brands or dates you cannot see. Lower confidence when unsure.
- Use notes for anything a person should double-check (illegible labels, possible damage, \
items that might belong elsewhere)."""


def system_prompt() -> str:
    override = (settings.get("ai.system_prompt") or "").strip()
    return override or DEFAULT_SYSTEM


def build_user_prompt(table, user_notes: str = "", n_photos: int = 0, skip_existing: bool = True) -> str:
    fmap = FolderMap()
    path = fmap.path(table.folder_id) + [table.name]
    org = (settings.get("ai.org_context") or "").strip()
    cap = int(settings.get("ai.max_existing_items") or 200)

    lines = []
    if org:
        lines += ["## Organisation context", org, ""]
    lines += ["## Destination table", f"Location in inventory: {' / '.join(path)}"]
    if table.summary:
        lines.append(f"Summary: {table.summary}")
    if table.context:
        lines.append(f"Context: {table.context}")
    lines.append("")

    lines.append("## Columns")
    for c in table.columns:
        if c.get("type") == "builtin":
            if c["key"] == "photos":
                continue
            lines.append(f"- {c['key']} (built-in, shown as \"{c.get('label')}\")")
        else:
            extra = f"; options: {', '.join(c.get('options') or [])}" if c["type"] == "select" else ""
            lines.append(f"- custom.{c['key']} \"{c.get('label')}\": {c['type']}{extra}")
    lines.append("")

    lines.append("## Quantity types and units")
    for name, d in quantity.QTY_TYPES.items():
        units = ", ".join(f'"{u}"' for u in d["units"]) if d["units"] else "(none, use text)"
        lines.append(f"- {name}: {units}")
    lines.append("")

    existing = table.live_items()
    if existing:
        lines.append(f"## Existing rows in this table ({len(existing)} total, showing up to {cap})")
        for it in existing[:cap]:
            q = quantity.format(it.quantity)
            row = {"description": it.description, "quantity": q}
            if it.observation:
                row["observation"] = it.observation[:200]
            if it.custom:
                row["custom"] = it.custom
            lines.append(json.dumps(row, ensure_ascii=False, default=str))
        lines.append("")
        if skip_existing:
            lines.append("Do NOT repeat items that are already listed above unless the photos clearly "
                         "show additional, different units (then describe only what is new).")
        else:
            lines.append("Existing rows are shown for naming style only; list everything visible in the photos.")
        lines.append("")

    lines.append("## Photos")
    lines.append(f"{n_photos} photo(s) attached, indexed 0 to {max(0, n_photos - 1)} in order.")
    if user_notes.strip():
        lines += ["", "## Notes from the person taking the photos", user_notes.strip()]
    lines += ["", "Return the items as JSON matching the provided schema."]
    return "\n".join(lines)
