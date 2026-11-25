# Build context: repository root.   docker build -f services/api/Dockerfile .
# Release builds pass the identity of the source:
#   docker build -f services/api/Dockerfile --target runtime \
#     --build-arg GIT_SHA="$(git rev-parse HEAD)" --build-arg VERSION=v0.1.0-rc.1 -t abu-api:v0.1.0-rc.1 .
# Base images are pinned by DIGEST (immutable). The tag is kept only for readability; the digest is what is pulled.
# Refresh them deliberately, in a reviewed change (docs/operations/DEPLOYMENT.md, "Updating pinned images").
FROM python:3.13-slim@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
# The runtime has no package manager. Dependencies are installed above by uv into /app/.venv; the base image's own pip (and
# the setuptools/wheel it may carry) is never used at runtime and carries vendored libraries (msgpack, urllib3, ...) whose
# advisories would otherwise be inherited. Removing it also takes pip away from anyone who gains code execution.
RUN python -m pip uninstall -y pip setuptools wheel >/dev/null 2>&1 || true \
 && rm -rf /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.13 \
           /usr/local/lib/python3.13/site-packages/pip /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
           /usr/local/lib/python3.13/site-packages/setuptools* /usr/local/lib/python3.13/site-packages/wheel*

FROM base AS runtime
COPY services ./services
COPY core ./core
COPY agents ./agents
COPY domain ./domain
COPY database ./database
COPY alembic.ini ./
RUN useradd --system --uid 10001 --no-create-home abu
USER abu
ENV PATH="/app/.venv/bin:$PATH"
ARG GIT_SHA=unknown
ARG VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="abu-ai-api" \
      org.opencontainers.image.source="https://github.com/abu2508/Abu-AI" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.version="${VERSION}"
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
# create_checked_app = create_app plus the fail-closed deployment guard for ABU_ENV=staging|production (a no-op in
# development). SIGTERM triggers a graceful shutdown; in-flight requests get 20 s before the process exits.
CMD ["uvicorn", "--factory", "services.api.entrypoint:create_checked_app", "--no-server-header", "--timeout-graceful-shutdown", "20", "--host", "0.0.0.0", "--port", "8000"]
