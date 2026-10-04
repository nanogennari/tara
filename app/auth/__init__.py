from datetime import timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import Invite, User, utcnow
from ..services import settings
from ..services.audit import audit

bp = Blueprint("auth", __name__)

MIN_PASSWORD = 8


def _safe_next(target: str | None) -> str:
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("main.index")


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    if db.session.query(User.id).first() is not None:
        return redirect(url_for("auth.login"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        app_name = request.form.get("app_name", "").strip()
        if not username or len(password) < MIN_PASSWORD:
            error = f"Choose a username and a password with at least {MIN_PASSWORD} characters."
        elif password != request.form.get("password2"):
            error = "Passwords don't match."
        else:
            u = User(username=username, email=request.form.get("email", "").strip() or None,
                     display_name=request.form.get("display_name", "").strip() or None, role="admin")
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            if app_name:
                settings.set("app.name", app_name)
            tzname = request.form.get("timezone", "").strip()
            if tzname:
                from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
                try:
                    ZoneInfo(tzname)
                    settings.set("server.timezone", tzname)
                except (ZoneInfoNotFoundError, ValueError):
                    pass
            import app as app_pkg
            app_pkg._users_exist = True
            login_user(u, remember=True)
            return redirect(url_for("main.index"))
    return render_template("auth/setup.html", error=error)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_safe_next(request.args.get("next")))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        u = db.session.query(User).filter(
            (User.username == username) | (User.email == username)).first()
        if u and u.active and u.check_password(request.form.get("password", "")):
            days = int(settings.get("server.session_days") or 30)
            login_user(u, remember=bool(request.form.get("remember")), duration=timedelta(days=days))
            u.last_login = utcnow()
            db.session.commit()
            return redirect(_safe_next(request.args.get("next")))
        error = "Invalid username or password."
    return render_template("auth/login.html", error=error,
                           allow_registration=settings.get("server.allow_registration"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    if not settings.get("server.allow_registration"):
        return redirect(url_for("auth.login"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or len(password) < MIN_PASSWORD:
            error = f"Choose a username and a password with at least {MIN_PASSWORD} characters."
        elif db.session.query(User.id).filter_by(username=username).first():
            error = "That username is taken."
        else:
            u = User(username=username, email=request.form.get("email", "").strip() or None,
                     role="viewer")
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            audit("register", "user", u.id)
            login_user(u)
            return redirect(url_for("main.index"))
    return render_template("auth/register.html", error=error)


@bp.route("/join/<token>", methods=["GET", "POST"])
def join(token):
    """Register through an invite link (each link has a limited number of uses)."""
    inv = db.session.query(Invite).filter_by(token=token).first()
    if inv is None or not inv.usable:
        return render_template("auth/join.html", invalid=True), 410
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or len(password) < MIN_PASSWORD:
            error = f"Choose a username and a password with at least {MIN_PASSWORD} characters."
        elif password != request.form.get("password2"):
            error = "Passwords don't match."
        elif db.session.query(User.id).filter_by(username=username).first():
            error = "That username is taken."
        else:
            # Re-check under the same transaction so concurrent signups can't exceed max_uses
            claimed = (db.session.query(Invite)
                       .filter(Invite.id == inv.id, Invite.uses < Invite.max_uses, Invite.revoked.is_(False))
                       .update({Invite.uses: Invite.uses + 1}, synchronize_session=False))
            if not claimed:
                db.session.rollback()
                return render_template("auth/join.html", invalid=True), 410
            u = User(username=username, email=request.form.get("email", "").strip() or None,
                     display_name=request.form.get("display_name", "").strip() or None, role=inv.role)
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            audit("register", "user", u.id, f"invite #{inv.id}")
            login_user(u, remember=True)
            return redirect(url_for("main.index"))
    return render_template("auth/join.html", invite=inv, error=error)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if request.method == "POST":
        current_user.display_name = request.form.get("display_name", "").strip() or None
        current_user.email = request.form.get("email", "").strip() or None
        new = request.form.get("new_password", "")
        if new:
            if not current_user.check_password(request.form.get("current_password", "")):
                flash("Current password is incorrect.", "error")
                return redirect(url_for("auth.profile"))
            if len(new) < MIN_PASSWORD or new != request.form.get("new_password2"):
                flash(f"New password must have {MIN_PASSWORD}+ characters and match.", "error")
                return redirect(url_for("auth.profile"))
            current_user.set_password(new)
        db.session.commit()
        flash("Profile saved.", "ok")
        return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html")
