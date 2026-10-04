"""Items and photos."""
from flask import jsonify, request

from ..extensions import db
from ..models import Photo
from ..services import inventory as inv
from ..services.audit import audit
from ..services.photos import PhotoError
from . import body, bp, editor_required, int_list


@bp.post("/tables/<int:tid>/items")
@editor_required
def create_item(tid):
    d = body()
    it = inv.create_item(inv.get_table(tid), d, after_id=d.get("after_id"))
    return jsonify(it.to_dict()), 201


@bp.get("/items/<int:iid>")
def get_item(iid):
    return jsonify(inv.get_item(iid).to_dict())


@bp.patch("/items/<int:iid>")
@editor_required
def update_item(iid):
    it = inv.update_item(inv.get_item(iid), body())
    return jsonify(it.to_dict())


@bp.delete("/items/<int:iid>")
@editor_required
def delete_item(iid):
    return jsonify(inv.bulk_items([iid], "delete"))


@bp.post("/items/bulk")
@editor_required
def bulk_items():
    d = body()
    ids = int_list(d.get("ids"))
    res = inv.bulk_items(ids, d.get("action"), d)
    if d.get("action") == "delete":
        audit("delete", "items", None, f"ids={ids} batch={res['batch']}")
    return jsonify(res)


@bp.post("/items/<int:iid>/photos")
@editor_required
def upload_photos(iid):
    files = request.files.getlist("files") or request.files.getlist("file")
    if not files:
        return jsonify(error="No files uploaded"), 400
    try:
        photos = inv.add_photos(inv.get_item(iid), files)
    except PhotoError as e:
        db.session.rollback()
        return jsonify(error=str(e)), 400
    return jsonify([p.to_dict() for p in photos]), 201


def _photo(pid) -> Photo:
    p = db.session.get(Photo, pid)
    if p is None:
        raise inv.NotFound("Photo not found")
    return p


@bp.delete("/photos/<int:pid>")
@editor_required
def delete_photo(pid):
    inv.delete_photo(_photo(pid))
    return jsonify(ok=True)


@bp.post("/photos/<int:pid>/main")
@editor_required
def main_photo(pid):
    inv.make_main_photo(_photo(pid))
    return jsonify(ok=True)
