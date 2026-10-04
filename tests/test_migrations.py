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
