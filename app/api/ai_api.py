import io

from flask import current_app, jsonify, request
from flask_login import current_user
from werkzeug.datastructures import FileStorage

from ..ai import jobs
from ..ai import service as ai_service
from ..ai.providers import AIError
from ..extensions import db
from ..services import inventory as inv
from ..services.photos import PhotoError
from . import body, bp, editor_required, int_list


def _run(kind: str, fn):
    """Start an AI job (202 + job id). Tests and ?wait=1 run it inline and return the result."""
    if current_app.config.get("TESTING") or request.args.get("wait") == "1":
        try:
            return jsonify(fn())
        except (AIError, PhotoError) as e:
            db.session.rollback()
            return jsonify(error=str(e)), 422
    return jsonify(job=jobs.submit(kind, fn)), 202


@bp.get("/ai/jobs/<jid>")
def ai_job(jid):
    job = jobs.get(jid, current_user.id)
    if job is None:
        return jsonify(error="This AI request is no longer available. Try again."), 404
    return jsonify(job)


@bp.post("/tables/<int:tid>/ai/propose")
@editor_required
def ai_propose(tid):
    inv.get_table(tid)
    # Read uploads now: the request (and its files) is gone by the time the job runs
    files = [FileStorage(io.BytesIO(f.read()), filename=f.filename) for f in request.files.getlist("photos")]
    notes = request.form.get("notes", "")
    skip = request.form.get("skip_existing", "1") not in ("0", "false")
    return _run("propose", lambda: ai_service.propose(inv.get_table(tid), files, notes, skip))


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
    inv.get_table(tid)
    return _run("refine", lambda: ai_service.refine(
        inv.get_table(tid), int_list(d.get("photo_ids")), d.get("items") or [],
        d.get("instruction", ""), int(row_index) if row_index is not None else None,
        [str(h) for h in (d.get("history") or [])], d.get("notes", "")))


@bp.post("/ai/chat")
def ai_chat():
    from ..ai import chat
    d = body()
    return _run("chat", lambda: chat.ask(d.get("message", ""), d.get("history"), d.get("context")))
