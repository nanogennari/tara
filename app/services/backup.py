"""Backup/restore: a zip with a consistent SQLite snapshot plus uploads and branding files."""
import io
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from flask import current_app

from ..extensions import db

DB_NAME = "inventory.db"


def _db_path() -> Path:
    return Path(current_app.config["DATA_DIR"]) / DB_NAME


def create() -> tuple[bytes, str]:
    data_dir = Path(current_app.config["DATA_DIR"])
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        snap = Path(tmp) / DB_NAME
        src = sqlite3.connect(_db_path())
        dst = sqlite3.connect(snap)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(snap, DB_NAME)
            for sub in ("uploads", "branding"):
                root = data_dir / sub
                for p in root.rglob("*"):
                    if p.is_file():
                        zf.write(p, f"{sub}/{p.relative_to(root)}", compress_type=zipfile.ZIP_STORED)
    name = f"inventory-backup-{datetime.now():%Y%m%d-%H%M}.zip"
    return buf.getvalue(), name


class RestoreError(ValueError):
    pass


def restore(fileobj) -> dict:
    data_dir = Path(current_app.config["DATA_DIR"])
    try:
        zf = zipfile.ZipFile(fileobj)
    except zipfile.BadZipFile as exc:
        raise RestoreError("Not a valid backup zip") from exc
    names = zf.namelist()
    if DB_NAME not in names:
        raise RestoreError("Backup is missing inventory.db")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / DB_NAME
        tmp_db.write_bytes(zf.read(DB_NAME))
        check = sqlite3.connect(tmp_db)
        try:
            ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            has_tables = check.execute(
                "SELECT count(*) FROM sqlite_master WHERE name IN ('user','inv_table','item')").fetchone()[0] == 3
        finally:
            check.close()
        if not (ok and has_tables):
            raise RestoreError("The database inside the backup is damaged or not from this app")

        db.session.remove()
        db.engine.dispose()
        src = sqlite3.connect(tmp_db)
        dst = sqlite3.connect(_db_path())
        with dst:
            src.backup(dst)  # online copy into the live DB file
        src.close()
        dst.close()
        db.engine.dispose()

    files = 0
    for n in names:
        if not (n.startswith("uploads/") or n.startswith("branding/")) or n.endswith("/"):
            continue
        target = (data_dir / n).resolve()
        if not str(target).startswith(str(data_dir.resolve()) + os.sep):
            continue  # zip-slip guard
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(zf.read(n))
            files += 1

    import app as app_pkg
    app_pkg._users_exist = False
    from ..search import index
    index.rebuild()
    return {"files": files}
