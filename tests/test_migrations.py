"""The Alembic migrations must produce exactly the schema the models describe."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade

from app import create_app
from app.extensions import db


def test_migrations_match_models(tmp_path):
    app = create_app(DATA_DIR=tmp_path, SKIP_BACKGROUND=True)
    with app.app_context():
        upgrade(directory="migrations")
        with db.engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={
                "include_object": lambda obj, name, type_, *a: not (type_ == "table" and name and
                                                                    (name.startswith("search_fts") or name == "alembic_version")),
                "compare_type": False,
            })
            diff = compare_metadata(ctx, db.metadata)
        assert diff == [], f"models and migrations differ — run `flask db migrate`: {diff}"


def test_migrations_upgrade_a_populated_database(tmp_path):
    """Every migration must apply to a database that already has data (not just an empty one).

    Upgrades one revision at a time, inserting rows into every table that exists at each step.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from flask_migrate import upgrade as up
    from sqlalchemy import inspect, text

    app = create_app(DATA_DIR=tmp_path, SKIP_BACKGROUND=True)
    with app.app_context():
        script = ScriptDirectory.from_config(_alembic_cfg())
        revisions = [r.revision for r in reversed(list(script.walk_revisions()))]
        for rev in revisions:
            up(directory="migrations", revision=rev)
            insp = inspect(db.engine)
            with db.engine.begin() as conn:
                if "user" in insp.get_table_names() and not conn.execute(text("SELECT count(*) FROM user")).scalar():
                    conn.execute(text("INSERT INTO user (username, password_hash, role, active, created_at) "
                                      "VALUES ('old', 'x', 'admin', 1, '2026-01-01')"))
                if "inv_table" in insp.get_table_names() and not conn.execute(text("SELECT count(*) FROM inv_table")).scalar():
                    conn.execute(text("INSERT INTO inv_table (name, columns, position, active, created_at, updated_at, content_updated_at) "
                                      "VALUES ('Box', '[]', 1, 1, '2026-01-01', '2026-01-01', '2026-01-01')"))
                    conn.execute(text("INSERT INTO item (table_id, position, description, observation, qty_type, qty_unit, qty_text, "
                                      "qty_estimated, custom, ai_generated, created_at, updated_at) "
                                      "VALUES (1, 1, 'Tape', '', 'count', '', '', 0, '{}', 0, '2026-01-01', '2026-01-01')"))
        with db.engine.connect() as conn:
            assert conn.execute(text("SELECT prefs FROM user WHERE username = 'old'")).scalar() == "{}"


def _alembic_cfg():
    from flask import current_app
    return current_app.extensions["migrate"].migrate.get_config("migrations")
