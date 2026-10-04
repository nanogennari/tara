"""Photo storage: content-addressed files with display and thumbnail variants."""
import hashlib
import io
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps

from ..extensions import db
from ..models import Photo

THUMB_PX = 320
VARIANTS = ("original", "display", "thumb")

Image.MAX_IMAGE_PIXELS = 120_000_000


class PhotoError(ValueError):
    pass


def _dir(sha: str) -> Path:
    d = Path(current_app.config["UPLOAD_DIR"]) / sha[:2]
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_for(sha: str, variant: str) -> Path:
    if variant not in VARIANTS:
        raise PhotoError("bad variant")
    suffix = "" if variant == "original" else f".{variant}"
    return _dir(sha) / f"{sha}{suffix}.jpg"


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def store_bytes(data: bytes, original_name: str | None = None, user_id: int | None = None,
                item_id: int | None = None, position: int = 0) -> Photo:
    """Normalise an uploaded image, write variants and create a Photo row (not committed)."""
    from . import settings

    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img = _to_rgb(img)
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types
        raise PhotoError(f"Not a valid image: {original_name or 'upload'}") from exc

    max_px = int(settings.get("server.image_max_px") or 1600)
    quality = int(settings.get("server.image_quality") or 85)

    # Original is re-encoded (strips EXIF/GPS) but capped at 4096 px to bound disk use.
    orig = img.copy()
    orig.thumbnail((4096, 4096), Image.LANCZOS)
    buf = io.BytesIO()
    orig.save(buf, "JPEG", quality=92, optimize=True)
    orig_bytes = buf.getvalue()
    sha = hashlib.sha256(orig_bytes).hexdigest()

    p_orig = path_for(sha, "original")
    if not p_orig.exists():
        p_orig.write_bytes(orig_bytes)
        disp = img.copy()
        disp.thumbnail((max_px, max_px), Image.LANCZOS)
        disp.save(path_for(sha, "display"), "JPEG", quality=quality, optimize=True)
        th = img.copy()
        th.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
        th.save(path_for(sha, "thumb"), "JPEG", quality=80, optimize=True)

    photo = Photo(sha256=sha, original_name=(original_name or "")[:255] or None,
                  width=orig.width, height=orig.height, created_by=user_id,
                  item_id=item_id, position=position)
    db.session.add(photo)
    return photo


def read_for_ai(photo: Photo, max_px: int = 1568) -> bytes:
    """JPEG bytes sized for vision models."""
    p = path_for(photo.sha256, "original")
    img = Image.open(p)
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    return p.read_bytes()


def delete_files_if_orphan(sha: str):
    """Remove files once no Photo row references the hash any more."""
    if db.session.query(Photo.id).filter_by(sha256=sha).first():
        return
    for v in VARIANTS:
        try:
            path_for(sha, v).unlink()
        except FileNotFoundError:
            pass
