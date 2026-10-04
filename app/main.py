"""Page routes: app shell, media files, branding, theme CSS, health."""
import re

from flask import (Blueprint, Response, abort, current_app, jsonify, render_template,
                   send_file, send_from_directory)
from flask_login import current_user, login_required

from .extensions import db
from .models import Photo
from .services import photos as photo_svc
from .services import settings

bp = Blueprint("main", __name__)
SHA = re.compile(r"^[0-9a-f]{64}$")


@bp.get("/")
@login_required
def index():
    return render_template("app.html", user_json={
        "id": current_user.id, "name": current_user.name, "role": current_user.role,
        "can_edit": current_user.can_edit, "is_admin": current_user.is_admin,
    }, stale_days=settings.get("server.stale_days"), timezone=settings.get("server.timezone") or "UTC")


@bp.get("/healthz")
def healthz():
    db.session.execute(db.text("SELECT 1"))
    return jsonify(status="ok")


@bp.get("/media/<sha>/<variant>")
@login_required
def media(sha, variant):
    if not SHA.match(sha) or variant not in photo_svc.VARIANTS:
        abort(404)
    if db.session.query(Photo.id).filter_by(sha256=sha).first() is None:
        abort(404)
    path = photo_svc.path_for(sha, variant)
    if not path.exists():
        abort(404)
    resp = send_file(path, mimetype="image/jpeg", max_age=31536000, conditional=True)
    resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return resp


@bp.get("/branding/<name>")
def branding(name):
    if name not in ("logo.png", "logo.svg", "favicon.png", "favicon.ico"):
        abort(404)
    return send_from_directory(current_app.config["BRANDING_DIR"], name, max_age=3600)


@bp.get("/favicon.ico")
def favicon():
    if settings.get("app.favicon"):
        p = current_app.config["BRANDING_DIR"] / "favicon.ico"
        if p.exists():
            return send_file(p, mimetype="image/x-icon", max_age=3600)
    return send_from_directory(current_app.static_folder, "favicon.svg", mimetype="image/svg+xml", max_age=3600)


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(rgb, other, t):
    return tuple(round(a + (b - a) * t) for a, b in zip(rgb, other))


def _hex(rgb):
    return "#" + "".join(f"{c:02x}" for c in rgb)


def _luminance(rgb):
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def theme_vars(color: str) -> dict:
    try:
        rgb = _hex_to_rgb(color)
    except (ValueError, IndexError):
        rgb = _hex_to_rgb("#4f46e5")
    white, black = (255, 255, 255), (0, 0, 0)
    on = white if _contrast(rgb, white) >= _contrast(rgb, black) else black
    # Text-safe variant: darken (light theme) / lighten (dark theme) until 4.5:1 on the background
    text_light = rgb
    while _contrast(text_light, white) < 4.5:
        text_light = _mix(text_light, black, 0.1)
    text_dark = rgb
    bg_dark = (17, 19, 24)
    while _contrast(text_dark, bg_dark) < 4.5:
        text_dark = _mix(text_dark, white, 0.1)
    return {
        "primary": _hex(rgb), "on_primary": _hex(on),
        "primary_hover": _hex(_mix(rgb, black, 0.12)),
        "primary_soft": _hex(_mix(rgb, white, 0.88)), "primary_soft_dark": _hex(_mix(rgb, bg_dark, 0.78)),
        "primary_text": _hex(text_light), "primary_text_dark": _hex(text_dark),
        "rgb": ",".join(map(str, rgb)),
    }


@bp.get("/theme.css")
def theme_css():
    v = theme_vars(settings.get("app.primary_color") or "#4f46e5")
    css = f""":root {{
  --primary: {v['primary']}; --on-primary: {v['on_primary']}; --primary-hover: {v['primary_hover']};
  --primary-soft: {v['primary_soft']}; --primary-text: {v['primary_text']}; --primary-rgb: {v['rgb']};
}}
:root[data-theme="dark"] {{ --primary-soft: {v['primary_soft_dark']}; --primary-text: {v['primary_text_dark']}; }}
@media (prefers-color-scheme: dark) {{
  :root[data-theme="auto"] {{ --primary-soft: {v['primary_soft_dark']}; --primary-text: {v['primary_text_dark']}; }}
}}
"""
    return Response(css, mimetype="text/css", headers={"Cache-Control": "no-cache"})
