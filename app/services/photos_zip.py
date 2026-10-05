"""Download every photo in a folder / set of tables as a ZIP, laid out like the inventory:
<folder>/<subfolder>/<table>/<NNN item description>.jpg"""
import re
import tempfile
import zipfile

from ..extensions import db
from ..models import Item, Photo
from . import photos as photo_svc
from .tree import FolderMap

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def _safe(name: str, fallback: str, limit: int = 80) -> str:
    s = _UNSAFE.sub(" ", name or "").strip().strip(".")
    return re.sub(r"\s+", " ", s)[:limit].strip() or fallback


def build(tables: list, root_folder_id: int | None = None):
    """Write the ZIP to a temp file; returns (file, photo_count). Paths are relative to root_folder_id's
    parent (so the ZIP's top level is that folder), or to the top level of the inventory."""
    fmap = FolderMap()
    skip = len(fmap.chain(root_folder_id)) - 1 if root_folder_id is not None else 0
    out = tempfile.TemporaryFile()
    count, used = 0, set()
    # Photos are already JPEG: storing them uncompressed is as small and much faster
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
        for t in tables:
            folders = [_safe(n, "Folder") for n in fmap.path(t.folder_id)[skip:]]
            base = "/".join([*folders, _safe(t.name, f"Table {t.id}")])
            items = (db.session.query(Item).filter(Item.table_id == t.id, Item.deleted_at.is_(None))
                     .order_by(Item.position, Item.id).all())
            photos = {}
            for p in (db.session.query(Photo).filter(Photo.item_id.in_([i.id for i in items] or [0]))
                      .order_by(Photo.position, Photo.id)):
                photos.setdefault(p.item_id, []).append(p)
            for n, it in enumerate(items, 1):
                ps = photos.get(it.id) or []
                stem = f"{n:03d} {_safe(it.description, 'Item', 60)}"
                for k, p in enumerate(ps, 1):
                    src = photo_svc.path_for(p.sha256, "original")
                    if not src.exists():
                        continue
                    name = f"{base}/{stem}{f' ({k})' if len(ps) > 1 else ''}.jpg"
                    while name in used:  # two tables with the same name in one folder
                        name = name[:-4] + "_.jpg"
                    used.add(name)
                    zf.write(src, name)
                    count += 1
    out.seek(0)
    return out, count
