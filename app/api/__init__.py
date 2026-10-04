from functools import wraps

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from flask_wtf.csrf import CSRFError

from ..extensions import db
from ..services.inventory import InventoryError

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.before_request
@login_required
def _require_login():
    return None


@bp.errorhandler(InventoryError)
def _inv_error(e: InventoryError):
    db.session.rollback()
    return jsonify(error=str(e)), e.status


@bp.errorhandler(CSRFError)
def _csrf_error(e):
    return jsonify(error="Session expired, please reload the page"), 400


@bp.errorhandler(400)
@bp.errorhandler(404)
@bp.errorhandler(413)
def _http_error(e):
    msg = getattr(e, "description", None) or str(e)
    if getattr(e, "code", None) == 413:
        msg = "Upload too large"
    return jsonify(error=msg), getattr(e, "code", 400)


def body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def int_list(val) -> list[int]:
    if val is None:
        return []
    if isinstance(val, str):
        val = val.split(",")
    out = []
    for v in val:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


def editor_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.can_edit:
            return jsonify(error="You have read-only access"), 403
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user.is_admin:
            return jsonify(error="Admins only"), 403
        return fn(*a, **kw)
    return wrapper


from . import admin_api, ai_api, io_api, items, me, nodes, search_api  # noqa: E402,F401
