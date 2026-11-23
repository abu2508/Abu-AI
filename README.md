# Abu-AI

**Abu AI** is a personal AI operating system with three pillars — a **financial assistant**, a
**personal messaging assistant** and a **daily coach** — built to run on a single modest VPS with a
hard AI API budget of ₹0–₹300 per month.

> Status: **Milestone 3 — authentication and the Agent Registry.** No real model has been called,
> no sensitive data is sent anywhere, and there is no mobile app yet. See the roadmap below.

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

## Authentication (Milestone 3)

Designed for a future native iOS app. There is **no master API key anywhere**: not in the repo, not
in the app, not in the API's environment. Administration is a separate, server-side capability.

```
 operator (shell on the server)                 device (iOS app, later)                  API
 admin CLI: create-user / enroll ─► one-time code ─► POST /v1/auth/enroll ──► device session
   (128-bit, 10-min TTL, single use)               returns access JWT (10 min) + refresh token
                                                   refresh token ─► iOS Keychain (device-only)
 every request:  Authorization: Bearer <access JWT>
     signature/exp/aud/iss/typ checked (HS256 pinned) ─► session row must be live ─► Principal(user_id)
 POST /v1/auth/refresh: rotate. A refresh token that was ALREADY rotated and comes back ⇒ reuse
     (theft or stale client) ⇒ the whole session is revoked and audited.
 GET /v1/auth/sessions · DELETE /v1/auth/sessions/{id} · POST /v1/auth/logout   (instant revocation)
```

- **Stored:** only keyed HMAC-SHA256 hashes of refresh tokens and enrollment codes (key:
  `TOKEN_HASH_KEY`). A leaked database row cannot be replayed as a credential.
- **Signing:** access JWTs use `JWT_SECRET`, a different secret. Both are ≥ 32 chars, must differ, and
  the API refuses to start without them. Rotate by changing the secret (all sessions end).
- **Lifetimes:** access 10 min · refresh 30 days (sliding) · session 90 days absolute.
- **Every non-public route needs a bearer token.** The only public routes are `GET /health` (returns
  just `{"status":"ok"}`), `POST /v1/auth/enroll` and `POST /v1/auth/refresh`; a test enumerates the
  whole OpenAPI surface to enforce this. `/health/ready` (dependency detail) is authenticated.
- **Failures are indistinguishable** to callers (one generic 401); the audit log keeps the real reason.
- **Hardening:** per-IP rate limits (strict on enroll/refresh), per-user limits, a 64 KiB body limit
  (checked by `Content-Length` and while streaming), `Cache-Control: no-store`, no `Server` banner.
  `X-Forwarded-For` is ignored unless `TRUSTED_PROXY_HOPS` is set (use `1` behind Caddy).

### Operator CLI (separate from the app API)

```bash
A="docker compose --env-file .env -f infrastructure/docker-compose.yml run --rm -T admin"
$A create-user --name Abu                        # prints the user id
$A enroll --user <user-id> --label iPhone        # prints a ONE-TIME enrollment code
$A sessions list --user <user-id>;  $A sessions revoke <session-id>
$A agents list
$A version show|create|clone|activate|retire <agent_id> [n]
$A grant model|tool|memory <agent_id> <n> <value> [--mode read|read_write]
$A revoke model_alias|tool|memory <agent_id> <n> <value>
$A enable|disable <agent_id>
```

The `admin` service is opt-in (compose profile), publishes no ports and has no internet route.

## Agent Registry (Milestone 3)

`finance_agent`, `messaging_agent` and `daily_coach_agent` are registered **disabled**, each with an
empty draft version 1. Everything is **default-deny**.

- **Versions** (`draft → active → retired`): only a draft is editable; activation **freezes** it, so the
  audit trail always matches what ran. To change an active agent, *clone* it, edit the draft, activate
  it (the old version is retired atomically). Rollback = clone an old version and activate the clone.
  The database allows at most one active version per agent.
- **Run only if** the agent is `enabled` **and** has an active version. Retiring the active version
  disables the agent.
- **Per-version permissions:** model *aliases* (never provider model IDs), *tools*, and *memory
  namespaces* (read / read_write). Memory grants must sit under the agent's own `<agent_id>/…` prefix, and
  the runtime re-checks this on every access, so one agent can never reach another's memory.
- **Per-version limits** (hard ceilings in code *and* DB CHECKs): steps ≤ 50, timeout ≤ 300 s, input tokens
  ≤ 200k, output tokens ≤ 100k, **per-run cost ≤ ₹50 (default ₹0 = free models only)**.
- **Tools** are defined in code (`ToolRegistry`); the registry is **intentionally empty** in this
  milestone, so no tool can be granted yet. When tools arrive: names come from code only, inputs are
  validated against a Pydantic schema (`extra="forbid"`, 16 KiB cap), `WRITE` tools always need a
  human approval bound to the *exact arguments* (single use, 5 min), and `IRREVERSIBLE` tools
  (payments, sending messages) cannot be granted at all.
- **Enforcement** happens server-side on every call through `RunContext` (built only from the DB state of
  an enabled agent's active version): steps, deadline, token and cost budgets, alias/tool/memory checks.
  `ModelGateway` clamps `max_tokens` to the remaining budget and refuses over-budget calls *before*
  contacting a provider.
- **Audit:** `audit_events` is append-only (a DB trigger rejects UPDATE/DELETE/TRUNCATE), stores no
  secrets or raw tool arguments, and denials are written in their own transaction so they survive a
  rollback.
- **App API surface:** read agents/versions/audit, plus `POST /v1/agents/{id}/disable` (it can only
  *reduce* privilege). Enabling, activating and every grant are operator-only, so a stolen app token cannot
  widen an agent.

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
curl -s localhost:8000/health          # {"status":"ok"}; /health/ready needs a bearer token
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
uv run alembic upgrade head   # 0001 registry · 0002 agents · 0003 auth+audit · 0004 agent versions
uv run alembic downgrade base
uv run alembic revision --autogenerate -m "describe change" --rev-id 0003
```

Always review autogenerated migrations by hand. Tests verify fresh creation, stepwise upgrade with
data preserved (0004 converts Milestone 2 agent-level permissions into an active version 1 without
widening them), downgrade and re-upgrade, idempotent re-run, and ORM↔migration drift.

## Development and tests

```bash
uv run ruff format . && uv run ruff check .
uv run mypy
uv run pytest        # unit + security tests; integration tests skip without services
uv run uvicorn --factory services.api.main:create_app   # needs ABU_* env (see .env)
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
3. ✅ Authentication and the Agent Registry (versions, permissions, limits, audit)
4. Governance and cost control (the ₹300/month ledger) · 5. Memory · 6. Orchestrator
7. Domain agents · 8. Mobile app · 9. Testing and deployment

## Known issues and limitations

- **No HTTP endpoint executes a model or tool.** The runtime is exercised at service level; the
  orchestrator (Milestone 6) will expose it. Tool registry is empty; approvals are enforced and tested
  but there is no approval-granting API yet.
- **Rate limits are per process and in memory** (no Redis by design). Fine for one worker; scaling to
  several multiplies the effective limit. They reset on restart.
- **Refresh tokens are bearer tokens.** Rotation + reuse detection limit the damage of theft, but they
  are not bound to the device. Device binding (Apple App Attest / DPoP-style proof) is a future hardening
  step; the iOS app must keep the refresh token in the Keychain (`ThisDeviceOnly`).
- **Strict rotation can sign a legitimate client out** if a refresh response is lost in transit after
  the server rotated: the retry looks like reuse and kills the session (re-enroll). This is the
  deliberate security-over-convenience trade-off; a short grace window could be added later.
- **HS256 (shared secret)** is used because one service both signs and verifies. Rotating
  `JWT_SECRET` ends all sessions.
- **Enrollment is a manual, operator-issued code.** There is no self-service signup (single-user by design).
- The ₹ cost limit uses a conservative placeholder USD→INR rate (`ABU_USD_INR_RATE`, default 100); the
  real ledger arrives in Milestone 4. No paid model is routable regardless.
- Still not built: spending ledger, execution records, `model_versions`, `model_evaluations`.
- No real provider has been called. `gemini-3.1-flash-lite` availability and rate limits are unverified
  live; provider terms can change.
- Registry tables are system-level (no `user_id`); user-owned tables will carry one.
- Starlette emits a deprecation warning about `httpx` in `TestClient`; harmless.
- Building the API image inside this agent's sandbox needed a CA workaround for its egress proxy; the
  committed Dockerfile has no such change and has not been built unmodified here.
