from flask import Blueprint, abort, render_template
from flask_login import current_user, login_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.get("/")
@login_required
def settings_page():
    if not current_user.is_admin:
        abort(403)
    return render_template("admin/settings.html")
