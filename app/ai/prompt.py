"""Prompt construction for AI-assisted item cataloguing."""
import json

from .. import quantity
from ..services import settings
from ..services.tree import FolderMap

DEFAULT_SYSTEM = """You catalogue physical inventory from photos. You are given photos of the \
contents of one storage container (box, bag, shelf...) and must list every distinct item you can \
identify so it can be added to an inventory spreadsheet.

How to work:
- Look carefully at every photo. Each photo is preceded by a "Photo N" label (N starts at 0). The \
same item may appear in several photos; list it once.
- photo_indexes says which photos will be attached to the item in the inventory, so choose them \
carefully: only photos where this item is clearly shown (a main subject of the photo, or clearly \
identifiable), BEST PHOTO FIRST — the first one becomes the item's thumbnail. Don't include photos \
where the item only appears in the background of a photo that is about other items, unless that is \
the only photo showing it. Usually 1–2 photos per item.
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


NEW_COLUMNS_RULE = """## New columns
new_columns must be [] (and every item's new_values []) unless the user explicitly asks you to add, \
create or suggest new columns/fields. When they do, propose only columns that don't already exist, \
and put each item's values for them in new_values (by column label) — never in custom."""


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
    if n_photos:
        lines.append(f"{n_photos} photo(s) attached, labeled Photo 0 to Photo {max(0, n_photos - 1)} in the order given.")
        if user_notes.strip():
            lines += ["", "## Notes from the person taking the photos", user_notes.strip()]
    else:
        lines.append("No photos this time. Create the items from the user's description below; photo_indexes "
                     "must be []. Don't add items the description doesn't mention.")
        lines += ["", "## Items to add, as described by the user", user_notes.strip()]
    lines += ["", NEW_COLUMNS_RULE, "", "Return the items as JSON matching the provided schema."]
    return "\n".join(lines)
