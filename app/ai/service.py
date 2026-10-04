"""AI wizard: propose items from photos, then commit the reviewed rows."""
import json
import logging
import threading
import time
from collections import defaultdict, deque

from ..extensions import db
from ..models import Item, Photo
from ..services import inventory as inv
from ..services import photos as photo_svc
from ..services.tracking import current_user_id
from . import prompt as prompt_mod
from . import usage
from .providers import AIBadOutput, AIError, get_provider
from .schema import ProposalError, build_schema, normalise

log = logging.getLogger(__name__)

MAX_PHOTOS = 20
RATE_LIMIT = 5          # calls
RATE_WINDOW = 60        # seconds

_calls: dict[int, deque] = defaultdict(deque)
_rate_lock = threading.Lock()


def _check_rate(uid: int):
    now = time.time()
    with _rate_lock:
        q = _calls[uid]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            raise AIError(f"Too many AI requests. Wait {int(RATE_WINDOW - (now - q[0])) + 1}s and try again.")
        q.append(now)


def _generate(provider, images, system, user_prompt, schema, table, kind="propose") -> dict:
    """Call the model; retry once when the output is malformed or off-schema."""
    with usage.track(provider, kind):
        for attempt in range(2):
            try:
                raw = provider.generate(images, system, user_prompt, schema)
                return normalise(raw, table, len(images))
            except (AIBadOutput, ProposalError) as exc:
                if attempt == 1:
                    msg = str(exc) if isinstance(exc, AIBadOutput) and "token budget" in str(exc) else \
                        "The AI response didn't match the expected format. Try again."
                    raise AIError(msg) from exc
    raise AssertionError("unreachable")


def propose(table, files, notes: str = "", skip_existing: bool = True) -> dict:
    inv.ensure_table_writable(table)
    files = [f for f in files if f]
    if not files:
        raise AIError("Add at least one photo")
    if len(files) > MAX_PHOTOS:
        raise AIError(f"At most {MAX_PHOTOS} photos per request")
    uid = current_user_id()
    _check_rate(uid or 0)

    pending = []
    for f in files:
        pending.append(photo_svc.store_bytes(f.read(), getattr(f, "filename", None), uid, None))
    db.session.commit()

    provider = get_provider()
    images = [photo_svc.read_for_ai(p) for p in pending]
    system = prompt_mod.system_prompt()
    user_prompt = prompt_mod.build_user_prompt(table, notes, len(images), skip_existing)
    schema = build_schema(table)

    started = time.time()
    result = _generate(provider, images, system, user_prompt, schema, table)
    log.info("AI proposal: %d items from %d photos in %.1fs (%s)", len(result["items"]), len(images),
             time.time() - started, provider.model)

    from ..search.query import similar_items
    dupes = similar_items([i["description"] for i in result["items"]], exclude_table=table.id)
    for item, d in zip(result["items"], dupes):
        item["possible_duplicates"] = d

    return {
        "photos": [p.to_dict() for p in pending],
        "items": result["items"],
        "notes": result["notes"],
        "model": f"{provider.name}:{provider.model}",
    }


def _pending_photos(photo_ids: list[int]) -> list[Photo]:
    photos = {p.id: p for p in db.session.query(Photo).filter(
        Photo.id.in_(photo_ids or [0]), Photo.item_id.is_(None))}
    ordered = [photos[pid] for pid in photo_ids if pid in photos]
    if len(ordered) != len(photo_ids):
        raise AIError("The photos for this draft have expired. Start again with new photos.")
    return ordered


def _draft_for_prompt(rows: list[dict]) -> list[dict]:
    keep = ("description", "quantity", "observation", "custom", "photo_indexes", "confidence")
    return [{k: r.get(k) for k in keep} for r in rows]


def refine(table, photo_ids: list[int], rows: list[dict], instruction: str,
           row_index: int | None = None, history: list[str] | None = None, notes: str = "") -> dict:
    """Revise a draft (or one row of it) according to a natural-language instruction."""
    inv.ensure_table_writable(table)
    instruction = (instruction or "").strip()
    if not instruction:
        raise AIError("Tell the AI what to change")
    if row_index is not None and not 0 <= row_index < len(rows):
        raise AIError("That row no longer exists in the draft")
    _check_rate(current_user_id() or 0)

    pending = _pending_photos(photo_ids)
    provider = get_provider()
    images = [photo_svc.read_for_ai(p) for p in pending]
    base = prompt_mod.build_user_prompt(table, notes, len(images), skip_existing=True)

    parts = [base, "", "## Current draft (already reviewed by the user; manual edits are intentional)"]
    draft = _draft_for_prompt(rows)
    for i, r in enumerate(draft):
        parts.append(f"[{i}] " + json.dumps(r, ensure_ascii=False, default=str))
    if history:
        parts += ["", "## Earlier change requests (already applied)"] + [f"- {h}" for h in history[-10:]]
    parts.append("")
    if row_index is None:
        parts += ["## Change requested for the whole draft", instruction, "",
                  "Return the complete revised list of items. Keep rows the request doesn't concern "
                  "exactly as they are (including the user's manual edits)."]
    else:
        parts += [f"## Change requested for row [{row_index}] only", instruction, "",
                  f"Return only the item(s) that should replace row [{row_index}] — usually one, or several "
                  "if the request is to split it. Do not return the other rows."]
    result = _generate(provider, images, prompt_mod.system_prompt(), "\n".join(parts), build_schema(table), table,
                       kind="refine")

    new_rows = result["items"]
    if row_index is not None:
        if not new_rows:
            raise AIError("The AI returned no replacement for that row. Try rephrasing.")
        new_rows = rows[:row_index] + new_rows + rows[row_index + 1:]

    from ..search.query import similar_items
    for item, d in zip(new_rows, similar_items([i["description"] for i in new_rows], exclude_table=table.id)):
        item["possible_duplicates"] = d
    return {"items": new_rows, "notes": result["notes"], "model": f"{provider.name}:{provider.model}"}


def commit(table, rows: list[dict], photo_ids: list[int]) -> dict:
    inv.ensure_table_writable(table)
    uid = current_user_id()
    photos = (db.session.query(Photo)
              .filter(Photo.id.in_(photo_ids or [0]), Photo.item_id.is_(None)).all())
    by_id = {p.id: p for p in photos}
    ordered = [by_id.get(pid) for pid in photo_ids]  # photo_indexes refer to this order

    pos = inv._next_position(Item, table_id=table.id)
    used: set[int] = set()
    created = []
    for row in rows:
        if not (row.get("description") or "").strip():
            continue
        it = Item(table_id=table.id, position=pos, ai_generated=True, updated_by=uid, custom={})
        inv._apply_item_data(it, table, row)
        db.session.add(it)
        db.session.flush()
        ppos = 0
        for idx in row.get("photo_indexes") or []:
            if not isinstance(idx, int) or not 0 <= idx < len(ordered) or ordered[idx] is None:
                continue
            src = ordered[idx]
            if src.id not in used:
                src.item_id, src.position = it.id, ppos
                used.add(src.id)
            else:
                # Same photo shows several items: share the file via another row
                db.session.add(Photo(sha256=src.sha256, original_name=src.original_name, width=src.width,
                                     height=src.height, item_id=it.id, position=ppos, created_by=uid))
            ppos += 1
        created.append(it)
        pos += 1

    unused = [p for p in photos if p.id not in used]
    shas = {p.sha256 for p in unused}
    for p in unused:
        db.session.delete(p)
    db.session.commit()
    for sha in shas:
        photo_svc.delete_files_if_orphan(sha)
    return {"created": [i.to_dict() for i in created]}


def discard(photo_ids: list[int]):
    photos = db.session.query(Photo).filter(Photo.id.in_(photo_ids or [0]), Photo.item_id.is_(None),
                                            Photo.created_by == current_user_id()).all()
    shas = {p.sha256 for p in photos}
    for p in photos:
        db.session.delete(p)
    db.session.commit()
    for sha in shas:
        photo_svc.delete_files_if_orphan(sha)
