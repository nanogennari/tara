"""Hourly housekeeping: purge old trash and abandoned AI-wizard photos."""
import logging
import threading
import time
from datetime import timedelta

from flask import Flask

from ..extensions import db
from ..models import Photo, utcnow

log = logging.getLogger(__name__)
INTERVAL = 3600
PENDING_PHOTO_HOURS = 24

_started = False


def init_app(app: Flask):
    global _started
    if _started or app.config.get("INDEX_SYNC"):
        return
    _started = True
    threading.Thread(target=_loop, args=(app,), name="maintenance", daemon=True).start()


def _loop(app: Flask):
    time.sleep(30)
    while True:
        with app.app_context():
            try:
                run_once()
            except Exception:  # noqa: BLE001
                log.exception("maintenance failed")
                db.session.rollback()
            finally:
                db.session.remove()
        time.sleep(INTERVAL)


def run_once() -> dict:
    from . import inventory, settings
    from . import photos as photo_svc

    days = int(settings.get("server.trash_days") or 30)
    purged = inventory.purge(older_than_days=days)

    cutoff = utcnow() - timedelta(hours=PENDING_PHOTO_HOURS)
    stale = db.session.query(Photo).filter(Photo.item_id.is_(None), Photo.created_at < cutoff).all()
    shas = {p.sha256 for p in stale}
    for p in stale:
        db.session.delete(p)
    db.session.commit()
    for sha in shas:
        photo_svc.delete_files_if_orphan(sha)
    return {"trash": purged, "pending_photos": len(stale)}
