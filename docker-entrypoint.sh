#!/bin/sh
set -e
# Apply database migrations, then serve. One worker keeps a single in-memory search
# index / embedding model; threads handle concurrency (SQLite has one writer anyway).
SKIP_BACKGROUND=1 flask db upgrade
exec gunicorn "app:create_app()" \
  --bind 0.0.0.0:8000 \
  --workers 1 --threads "${GUNICORN_THREADS:-8}" --worker-class gthread \
  --timeout "${GUNICORN_TIMEOUT:-300}" \
  --access-logfile - --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}"
