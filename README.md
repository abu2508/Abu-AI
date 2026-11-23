# Abu-AI

**Abu AI** is a personal AI operating system with three pillars — a **financial assistant**, a
**personal messaging assistant** and a **daily coach** — built to run on a single modest VPS with a
hard AI API budget of ₹0–₹300 per month.

> Status: **Milestone 1 — repository and infrastructure foundation.** No agents, model registry
> or mobile app yet. See the roadmap below.

## Architecture (current)

```
            127.0.0.1:8000
  client ───────────────────▶  api (FastAPI)  ──┐
                                                │  private "data" network (internal: no internet)
                          litellm (proxy) ◀─────┤
                                │               │
                                ▼               ▼
                         PostgreSQL 16 + pgvector
                         ├─ abu_ai   (owner: abu_app)   — application data, pgvector enabled
                         └─ litellm  (owner: litellm)   — LiteLLM's own state
```

- `db` has **no published ports** and lives only on an internal Docker network.
- `litellm` has **no published ports** (the dev overlay binds it to loopback only), no models and no
  provider keys. It gets an extra `egress` network solely so it can reach providers later.
- Separate logical databases and users; the app role cannot connect to LiteLLM's database.
- Secrets come from `.env` (git-ignored). Nothing in the repo contains a real secret.

Repository layout follows the master spec: `apps/`, `services/`, `core/`, `agents/`, `domain/`,
`database/`, `tests/`, `infrastructure/`. Directories for later milestones contain only `.gitkeep`.

## Prerequisites

Python ≥ 3.12 (tested on 3.13), [uv](https://docs.astral.sh/uv/), Docker with Compose v2.

## Setup

```bash
# 1. Python environment (exact versions come from uv.lock)
uv sync

# 2. Generate .env with random secrets (refuses to overwrite an existing .env)
./infrastructure/scripts/gen-secrets.sh

# 3. Validate the Compose files
docker compose --env-file .env -f infrastructure/docker-compose.yml config -q
```

## Run the stack

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml up -d --build
docker compose --env-file .env -f infrastructure/docker-compose.yml ps
curl -s localhost:8000/health          # {"status":"ok"}
curl -s localhost:8000/health/ready    # database, pgvector and litellm all "ok"
```

`/health` is liveness (no dependencies). `/health/ready` returns **503** with per-dependency status
if PostgreSQL, pgvector or LiteLLM is unavailable; it never echoes hostnames or credentials.

Stop with `docker compose ... down` (add `-v` to also delete the database volume).

## Development and tests

```bash
uv run ruff format . && uv run ruff check .
uv run mypy
uv run pytest                      # unit + security tests; DB tests are skipped
```

### Run integration tests

The dev overlay exposes PostgreSQL on loopback only (never use it on the VPS):

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml \
  -f infrastructure/docker-compose.dev.yml up -d --build
set -a; . ./.env; set +a
ABU_TEST_DATABASE_URL="postgresql+asyncpg://$APP_DB_USER:$APP_DB_PASSWORD@127.0.0.1:5432/$APP_DB_NAME" \
  uv run pytest
```

## Budget policy

Paid models are **off by default** and no provider key is configured. The ₹300/month ceiling, the
spending ledger and per-run/daily limits arrive in Milestone 4; until then nothing can reach a model
provider because LiteLLM has an empty model list.

## Roadmap

1. ✅ Repository and infrastructure foundation
2. Model Registry · 3. Agent Registry · 4. Governance and cost control · 5. Memory
6. Orchestrator · 7. Domain agents · 8. Mobile app · 9. Testing and deployment

## Known issues

- LiteLLM `v1.83.14-stable.patch.3` still serves the static `/ui/` login shell even with
  `DISABLE_ADMIN_UI=True`. Its authenticated API returns 401 without the master key, and LiteLLM is
  never published or proxied publicly, but this needs to be revisited before any HTTPS deployment.
- Starlette emits a deprecation warning about `httpx` in `TestClient`; harmless for now.
