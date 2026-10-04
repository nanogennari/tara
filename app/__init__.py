import logging
import os

from flask import Flask, jsonify, redirect, request, url_for

from .config import Config
from .extensions import csrf, db, login_manager, migrate


def create_app(**overrides) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config(**overrides))
    app.json.sort_keys = False  # keep provider/column order as defined
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    csrf.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    from . import models
    from .services import tracking  # noqa: F401 - registers session hooks

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(models.User, int(uid))

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/"):
            return jsonify(error="Not logged in"), 401
        return redirect(url_for("auth.login", next=request.full_path))

    from .admin import bp as admin_bp
    from .api import bp as api_bp
    from .auth import bp as auth_bp
    from .main import bp as main_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)

    @app.before_request
    def first_run_setup():
        if request.endpoint in ("auth.setup", "static", "main.healthz", "main.favicon",
                                "main.theme_css") or request.endpoint is None:
            return None
        if not _has_users():
            if request.path.startswith("/api/"):
                return jsonify(error="Setup required"), 409
            return redirect(url_for("auth.setup"))
        return None

    @app.context_processor
    def inject_branding():
        from .services import settings
        return {"brand": settings.get_many("app."), "brand_version": _brand_version()}

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        return resp

    if app.config.get("AUTO_CREATE_DB"):
        with app.app_context():
            db.create_all()

    if not app.config.get("SKIP_BACKGROUND"):
        with app.app_context():
            if _db_ready():
                from .search import index
                from .services import maintenance
                index.init_app(app)
                maintenance.init_app(app)

    return app


_users_exist = False


def _has_users() -> bool:
    global _users_exist
    if _users_exist:
        return True
    from .models import User
    try:
        _users_exist = db.session.query(User.id).first() is not None
    except Exception:  # noqa: BLE001 - DB not migrated yet
        return False
    return _users_exist


def _db_ready() -> bool:
    from sqlalchemy import inspect
    return inspect(db.engine).has_table("search_doc")


def _brand_version() -> str:
    from .services import settings
    return str(abs(hash((settings.get("app.logo"), settings.get("app.favicon"),
                         settings.get("app.primary_color")))) % 10**8)
