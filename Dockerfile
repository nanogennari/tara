# syntax=docker/dockerfile:1.7
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    FASTEMBED_CACHE_PATH=/opt/models
WORKDIR /srv

# ---- dependencies (cached layer)
FROM base AS deps
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
# Bake the default multilingual embedding model into the image so search works offline
RUN /srv/.venv/bin/python -c "from fastembed import TextEmbedding; \
TextEmbedding('sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir='/opt/models')"

# ---- runtime
FROM base AS runtime
RUN useradd --uid 1000 --create-home app && mkdir -p /data && chown app:app /data
COPY --from=deps /srv/.venv /srv/.venv
COPY --from=deps --chown=app:app /opt/models /opt/models
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app docker-entrypoint.sh ./
ENV PATH="/srv/.venv/bin:$PATH" DATA_DIR=/data FLASK_APP=app
USER app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
ENTRYPOINT ["./docker-entrypoint.sh"]
