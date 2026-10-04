import pytest

import app as app_pkg
from app import create_app
from app.extensions import db
from app.models import User
from app.services import settings


@pytest.fixture
def app(tmp_path, request):
    app_pkg._users_exist = False
    semantic = request.node.get_closest_marker("semantic") is not None
    application = create_app(DATA_DIR=tmp_path, TESTING=True, WTF_CSRF_ENABLED=False,
                             AUTO_CREATE_DB=True, INDEX_SYNC=True, SKIP_BACKGROUND=True)
    with application.app_context():
        if not semantic:
            settings.set("search.provider", "off")
        from app.search import index
        index.init_app(application)
        for name, role in (("admin", "admin"), ("editor", "editor"), ("viewer", "viewer")):
            u = User(username=name, role=role)
            u.set_password("password123")
            db.session.add(u)
        db.session.commit()
    yield application
    with application.app_context():
        db.session.remove()
        db.engine.dispose()


def _login(app, username):
    c = app.test_client()
    r = c.post("/login", data={"username": username, "password": "password123"})
    assert r.status_code == 302, r.data
    return c


@pytest.fixture
def client(app):
    return _login(app, "admin")


@pytest.fixture
def editor(app):
    return _login(app, "editor")


@pytest.fixture
def viewer(app):
    return _login(app, "viewer")


def pytest_configure(config):
    config.addinivalue_line("markers", "semantic: uses the real local embedding model (slow)")
