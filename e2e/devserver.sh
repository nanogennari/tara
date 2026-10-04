#!/bin/sh
# Fresh dev server on :5055 with an empty database (used by the e2e scripts).
cd "$(dirname "$0")/.."
rm -rf .devdata e2e/screens
export DATA_DIR="$PWD/.devdata" FLASK_APP=app
SKIP_BACKGROUND=1 uv run flask db upgrade >/dev/null 2>&1
uv run python tests/sample_workbook.py .devdata/sample_inventory.xlsx
exec uv run flask run --port 5055 --host 127.0.0.1 > .devdata/server.log 2>&1
