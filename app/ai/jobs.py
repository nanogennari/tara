"""Background AI jobs.

Mobile browsers suspend or drop long-running requests when the app goes to the
background, so AI calls run in a server thread and the browser polls for the result
with short requests (which it can simply retry after coming back).
"""
import logging
import threading
import time
import uuid

from flask import current_app

from ..extensions import db
from ..services import tracking
from ..services.photos import PhotoError
from .providers import AIError

log = logging.getLogger(__name__)
KEEP_SECONDS = 30 * 60
_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _expire():
    cutoff = time.time() - KEEP_SECONDS
    for jid in [j for j, v in _jobs.items() if v["created"] < cutoff]:
        _jobs.pop(jid, None)


def submit(kind: str, fn) -> str:
    """Run fn() in a background thread as the current user; returns the job id."""
    app = current_app._get_current_object()
    uid = tracking.current_user_id()
    jid = uuid.uuid4().hex
    with _lock:
        _expire()
        _jobs[jid] = {"status": "running", "user_id": uid, "kind": kind, "created": time.time()}

    def run():
        with app.app_context():
            token = tracking.act_as(uid)
            try:
                result = fn()
                _jobs[jid].update(status="done", result=result)
            except (AIError, PhotoError) as e:
                db.session.rollback()
                _jobs[jid].update(status="error", error=str(e))
            except Exception:  # noqa: BLE001 - report something useful instead of hanging
                log.exception("AI job %s failed", kind)
                db.session.rollback()
                _jobs[jid].update(status="error", error="Something went wrong on the server. Try again.")
            finally:
                tracking.act_as_reset(token)
                db.session.remove()

    threading.Thread(target=run, name=f"ai-{kind}", daemon=True).start()
    return jid


def get(jid: str, user_id) -> dict | None:
    job = _jobs.get(jid)
    if job is None or job["user_id"] != user_id:
        return None
    out = {"status": job["status"], "kind": job["kind"]}
    if job["status"] == "done":
        out["result"] = job["result"]
    elif job["status"] == "error":
        out["error"] = job["error"]
    return out
