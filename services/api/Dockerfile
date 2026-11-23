# Build context: repository root.   docker build -f services/api/Dockerfile .
FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

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
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]
CMD ["uvicorn", "services.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
