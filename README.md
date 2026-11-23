# Abu-AI

**Abu AI** is a personal AI operating system with three pillars — a **financial assistant**, a
**personal messaging assistant** and a **daily coach** — built to run on a single modest VPS with a
hard AI API budget of ₹0–₹300 per month.

> Status: **Milestone 2 — Model Registry, Agent Registry foundations, provider abstraction and a
> mock-model end-to-end flow.** No real model has been called, no sensitive data is sent anywhere,
> and there is no mobile app yet. See the roadmap below.

## Architecture (current)

```
            127.0.0.1:8000
  client ───────────────────▶  api (FastAPI)  ──┐
                                                │  private "data" network (internal: no internet)
        migrate (one-shot) ─────────────────────┤
                          litellm (proxy) ◀─────┤
                                │               │
                                ▼               ▼
                         PostgreSQL 16 + pgvector
                         ├─ abu_ai   (owner: abu_app)   — application data, pgvector enabled
                         └─ litellm  (owner: litellm)   — LiteLLM's own state
```

- `db` has **no published ports** and lives only on an internal Docker network.
- `litellm` has **no published ports** (the dev overlay binds it to loopback only). Its only model
  is a mock; it has no provider keys. It has an extra `egress` network solely so it can reach
  providers later.
- Separate logical databases and users; the app role cannot connect to LiteLLM's database.
- Secrets come from `.env` (git-ignored). Nothing in the repo contains a real secret.

### How a model call is decided (Milestone 2)

```
agent ──▶ ModelGateway ──▶ agent enabled?  ──▶ agent granted this alias?
                              │
                              ▼
                        ModelRouter(alias, data_class)
                          picks the first route whose model is:
                          registered · approved · enabled · FREE · policy verified ·
                          provider approved for this data class · has required capabilities
                              │            (otherwise: NoRouteError with per-model reasons)
                              ▼
                        adapter (in-process Mock | LiteLLM) ──▶ result
```

- Agents ask for a **logical alias** (e.g. `test-mock`); nobody — including an LLM — can name a
  provider model ID. LiteLLM is sent the registry's `gateway_model_name`.
- Every request must declare a **data class**: `synthetic` < `non_sensitive` < `sensitive`. A provider
  is approved up to a ceiling that **starts at `synthetic`**. Raising it needs a named approver, a
  verified policy, and (for `sensitive`) documented *no-training and no-human-review* terms;
  "unknown" is not enough. Providers that train on data or use human review are capped at `synthetic`.
- **Paid models can be approved but never enabled or routed** until spending controls exist
  (Milestone 4). There is no silent fallback to a paid model, and fallback stays inside the registry.
- The database itself rejects `enabled AND NOT approved` (CHECK constraint).
- Gateway logs contain metadata only (agent, alias, model, token counts, latency), never prompts.

### Registered models (seed data)

| Name | Provider | Exact provider ID | Data class ceiling | Enabled |
|---|---|---|---|---|
| `mock-local` | in-process mock | `mock-local` | sensitive (nothing leaves the process) | yes |
| `litellm-mock` | LiteLLM `mock_response` | `abu-mock` | sensitive (private network, no provider) | yes |
| `gemini-3.1-flash-lite-free` | Google AI Studio **free tier** | `gemini-3.1-flash-lite` | **synthetic only** | **no** |

`gemini-3.1-flash-lite` facts, read from Google's own pages on 2026-11-23
([pricing](https://ai.google.dev/gemini-api/docs/pricing),
[model](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite),
[terms](https://ai.google.dev/gemini-api/terms)):

- Free tier: input and output **free of charge**; context 1,048,576 / output 65,536 tokens.
- **"Used to improve our products: Yes"** on the free tier; unpaid quota content may be read by human
  reviewers; Google says *"Do not submit sensitive, confidential, or personal information."*
- **Not verified:** per-model RPM/RPD (not published in the docs; visible per project in AI Studio),
  and a live call (no API key is configured). Free-tier availability can change at any time.
- Registered `enabled = false`. To use it, a key must be supplied and a LiteLLM model added; it will
  only ever receive `synthetic` data.

Do not send real financial records, private messages or other sensitive data to any free-tier
provider until its data policy has been explicitly approved.

### Agents

`finance_agent`, `messaging_agent` and `daily_coach_agent` are registered **disabled**, with no model
permissions. Versioning, tool/memory permissions and limits come in Milestone 3.

## Prerequisites

Python ≥ 3.12 (tested on 3.13), [uv](https://docs.astral.sh/uv/), Docker with Compose v2.

## Setup

```bash
uv sync                                   # exact versions come from uv.lock
./infrastructure/scripts/gen-secrets.sh   # creates .env with random secrets (never overwrites)
docker compose --env-file .env -f infrastructure/docker-compose.yml config -q
```

## Run the stack

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml up -d --build
docker compose --env-file .env -f infrastructure/docker-compose.yml ps -a
curl -s localhost:8000/health/ready    # database, pgvector and litellm all "ok"
```

The one-shot `migrate` service runs `alembic upgrade head` before the API starts (it exits 0 when
done). Seed the registry (idempotent; never overwrites existing rows):

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml run --rm --no-deps migrate \
  python -m database.seed
```

## Migrations

```bash
export ABU_DATABASE_URL="postgresql+psycopg://USER:PASS@127.0.0.1:5432/abu_ai"   # dev overlay port
uv run alembic upgrade head            # 0001 model registry, 0002 agent foundations
uv run alembic downgrade base
uv run alembic revision --autogenerate -m "describe change" --rev-id 0003
```

Always review autogenerated migrations by hand. Tests verify fresh creation, stepwise upgrade with
data preserved, downgrade and re-upgrade, idempotent re-run, and ORM↔migration drift.

## Development and tests

```bash
uv run ruff format . && uv run ruff check .
uv run mypy
uv run pytest        # unit + security tests; integration tests skip without services
```

### Run integration tests

The dev overlay exposes PostgreSQL and LiteLLM on loopback only (never use it on the VPS):

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml \
  -f infrastructure/docker-compose.dev.yml up -d --build
set -a; . ./.env; set +a
export ABU_TEST_ADMIN_URL="postgresql+psycopg://$POSTGRES_ADMIN_USER:$POSTGRES_ADMIN_PASSWORD@127.0.0.1:5432/postgres"
export ABU_TEST_DATABASE_URL="postgresql+psycopg://$APP_DB_USER:$APP_DB_PASSWORD@127.0.0.1:5432/$APP_DB_NAME"
export ABU_TEST_LITELLM_URL=http://127.0.0.1:4000 ABU_TEST_LITELLM_KEY=$LITELLM_MASTER_KEY
uv run pytest
```

Each integration test gets a throwaway database built by the real migrations and dropped afterwards.

## LiteLLM notes (pinned `v1.83.14-stable.patch.3`)

- **`max_budget: 0` means "no limit", not "zero".** LiteLLM only enforces a budget when
  `litellm.max_budget > 0` (`auth_checks.py`, `user_api_key_auth.py`). Verified empirically against
  this image with a mock model priced at 30 per call: with `max_budget: 0` five consecutive calls all
  succeeded; with `max_budget: 0.01` the second call was rejected with `budget_exceeded`.
  Milestone 1 shipped `0` (i.e. unlimited gateway spend); it is now `0.01` USD, a second guard behind
  the application ledger (Milestone 4), and a test fails if it is ever `<= 0`.
- **The admin UI cannot be turned off by a flag.** `DISABLE_ADMIN_UI` only changes a login hint; the
  static app is mounted unconditionally. We bind-mount an inert stub (`services/litellm/ui-disabled/`)
  over the bundled UI, so `/ui/` returns a one-line page and every asset path 404s. `NO_DOCS`/`NO_REDOC`
  remove Swagger and ReDoc. The mount targets the venv copy of the package
  (`/app/.venv/lib/python3.13/site-packages/litellm/proxy/_experimental/out`) — **re-verify this path
  whenever the image tag changes.** LiteLLM stays private regardless.

## Budget policy

Paid models are **off by default**, no provider key is configured, and the router refuses any paid
model. The ₹300/month ceiling, spending ledger and per-run/daily limits arrive in Milestone 4.

## Roadmap

1. ✅ Repository and infrastructure foundation
2. ✅ Model Registry, Agent Registry foundations, provider abstraction, mock end-to-end flow
3. Agent Registry (versions, tool/memory permissions, limits) · 4. Governance and cost control
5. Memory · 6. Orchestrator · 7. Domain agents · 8. Mobile app · 9. Testing and deployment

## Known issues and limitations

- No API authentication, rate limiting or kill switch yet; there is deliberately **no HTTP endpoint
  that executes a model**. The mock flow runs at service level.
- Not implemented yet: spending ledger, `model_versions`, `model_evaluations`, execution records.
- No real provider has been called. `gemini-3.1-flash-lite` availability and rate limits are unverified
  live; provider terms can change.
- Registry tables are system-level and carry no `user_id`; user-owned tables (memory, finance,
  messaging) will.
- Starlette emits a deprecation warning about `httpx` in `TestClient`; harmless.
- Building the API image inside this agent's sandbox needed a CA workaround for its egress proxy; the
  committed Dockerfile has no such change and has not been built unmodified here.
