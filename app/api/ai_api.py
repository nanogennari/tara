from flask import jsonify, request

from ..ai import service as ai_service
from ..ai.providers import AIError
from ..extensions import db
from ..services import inventory as inv
from ..services.photos import PhotoError
from . import body, bp, editor_required, int_list


@bp.post("/tables/<int:tid>/ai/propose")
@editor_required
def ai_propose(tid):
    t = inv.get_table(tid)
    try:
        res = ai_service.propose(
            t, request.files.getlist("photos"), request.form.get("notes", ""),
            request.form.get("skip_existing", "1") not in ("0", "false"))
    except (AIError, PhotoError) as e:
        db.session.rollback()
        return jsonify(error=str(e)), 422
    return jsonify(res)


@bp.post("/tables/<int:tid>/ai/commit")
@editor_required
def ai_commit(tid):
    d = body()
    res = ai_service.commit(inv.get_table(tid), d.get("items") or [], int_list(d.get("photo_ids")))
    return jsonify(res), 201


@bp.post("/ai/discard")
@editor_required
def ai_discard():
    ai_service.discard(int_list(body().get("photo_ids")))
    return jsonify(ok=True)


@bp.post("/tables/<int:tid>/ai/refine")
@editor_required
def ai_refine(tid):
    d = body()
    row_index = d.get("row_index")
    try:
        res = ai_service.refine(
            inv.get_table(tid), int_list(d.get("photo_ids")), d.get("items") or [],
            d.get("instruction", ""), int(row_index) if row_index is not None else None,
            [str(h) for h in (d.get("history") or [])], d.get("notes", ""))
    except AIError as e:
        return jsonify(error=str(e)), 422
    return jsonify(res)


@bp.post("/ai/chat")
def ai_chat():
    from ..ai import chat
    d = body()
    try:
        res = chat.ask(d.get("message", ""), d.get("history"), d.get("context"))
    except AIError as e:
        return jsonify(error=str(e)), 422
    return jsonify(res)
