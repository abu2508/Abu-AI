# Abu-AI

**Abu AI** is a personal AI operating system with three pillars — a **financial assistant**, a
**personal messaging assistant** and a **daily coach** — built to run on a single modest VPS with a
hard AI API budget of ₹0–₹300 per month.

> Status: **Milestone 5 — hybrid memory.** No real model or embedding provider has been called, no
> paid provider is enabled (spend is INR 0), no personal data leaves this deployment, and there is no
> mobile app yet. See the roadmap below.

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
$A kill-switch activate|deactivate --reason "..."     $A kill-switch status
$A spend status
$A spend set-budget --monthly 300 --daily 20          # hard ceiling INR 300/month
$A spend set-rate --rate 88.5 --source "RBI reference rate 2026-11-23"
$A spend paid on|off                                  # default off
$A spend unfreeze --reason "..."                      # after a hold/unknown-cost incident
$A spend resolve <ledger-id> --confirmed-inr 1.25 | --not-charged
$A executions list --limit 20
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

## Governance and cost control (Milestone 4)

### Spending: INR 300/month is a ceiling, not a target. The default is INR 0.

| Setting | Default | Who can change it |
|---|---|---|
| Monthly paid budget | **INR 0** | operator CLI only; hard maximum **INR 300** (code + DB `CHECK`) |
| Daily paid budget | INR 0 (never above monthly) | operator CLI only |
| Paid models enabled | **off** | operator CLI only (needs a budget > 0 and a rate) |
| USD→INR rate | **unset** (paid calls refused) | operator CLI only, with a named source; refused when older than 14 days |
| Per-run cost cap | INR 0 per agent version (max 50) | operator CLI (agent version) |

Raising the ceiling above INR 300 needs a migration, i.e. an explicit design decision.

**The ledger (`spend_ledger`)** has one row per *paid provider attempt*: `reserved → settled | released`, or
`reserved → held`. Settled/released rows are immutable and nothing is ever deleted (DB triggers).
Counting for the budget: `settled` = confirmed cost, `reserved`/`held` = the **worst-case reservation**,
`released` = 0, so an unresolved amount is never counted as zero. Periods use a configurable UTC offset
(default IST, +05:30): day and month roll over at local midnight; a call is booked to the period it was
*reserved* in.

**No overspending under concurrency.** `reserve()` runs in its own *committed* transaction and takes
`SELECT … FOR UPDATE` on the single `spending_config` row, so all paid reservations are serialised and each
sees every earlier hold. The provider is only called after the reservation commits. (Tested: 12 concurrent
INR-40 reservations against a INR-300 budget → exactly 7 succeed.)

**Fail closed.**

| Situation | What happens |
|---|---|
| Cost cannot be determined (no usage, no reported cost) | held at the worst case, **paid spending frozen** until the operator resolves it |
| Timeout / 5xx / dropped connection after the request was sent | held, **frozen** (the provider may have billed) |
| Connect failure / 4xx (provider never processed it) | released, not frozen |
| Actual cost > reservation | the real amount is recorded, **frozen** |
| Missing/stale rate, spending state unreadable | paid refused |
| Same `call_key` presented again (retry/replay) | returns the existing row: **no second hold, no second provider call** |

**No double counting.** One ledger row per call. Its confirmed amount is LiteLLM's reported cost
(`x-litellm-response-cost`) if present, otherwise provider-reported tokens × the registry price. The other
figure is stored only for reconciliation; LiteLLM's own spend tracking is never added on top.

**Currency is explicit.** Registry prices are USD; the operator-set rate and its source are stored on every
ledger row and execution call, and amounts are rounded **up**.

**Routing never silently goes paid.** A paid model is a candidate only if governance allows paid spending
*and no free route precedes it* in priority, whether that free route is failing, disabled or ineligible.
Paid → free fallback is allowed. Every attempt, fallback and budget refusal is recorded.

### Kill switch

| Action | Operator CLI | Authenticated app |
|---|---|---|
| Activate the global kill switch | ✅ | ✅ |
| Disable an individual agent | ✅ | ✅ |
| **Deactivate** the kill switch | ✅ | ❌ 403, audited |
| **Re-enable** an agent | ✅ | ❌ 403, audited |
| Change budgets, rate, paid flag, model policies, provider config | ✅ | ❌ 403, audited |

Activation records **who, when, why and through which interface**, and is stored in the database, so it
survives restarts. A repeat activation keeps the original record (the attempt is still audited).

- **Blocks:** new agent executions (`start_run`), every model call (`ModelGateway`, even with an existing
  context), every tool call (`ToolExecutor`), and every paid reservation.
- **Queued work:** any queued or scheduled job must start through `start_run`, so the switch is checked at
  dequeue time. (There is no job queue yet; this is the contract for when there is.)
- **Fail closed:** if the state is unreadable or its row is missing, execution is blocked.
- **Stays available:** reads, execution history, audit access, authentication, session revocation, disabling
  agents. (No data-export feature exists yet; when it does it must not depend on the kill switch.)
- The explicit app routes for forbidden actions exist **only to answer 403 and write a durable audit
  record**, so a compromised token probing for privilege leaves a trail.

### Execution records

`executions` (run level) and `execution_calls` (one per model attempt, so fallbacks are visible) record
trace/execution/user/agent/version IDs, task type, alias, provider and exact model ID, tokens, **estimated
(worst-case) and confirmed cost kept separately**, currency and rate, latency, status and a machine-coded
failure reason, fallback info, policy decisions and timestamps. They are written in their own transactions
(they survive a rolled-back request) and **hold no prompts, completions or tool arguments**: a test checks
every governance table for a planted prompt and for content-capable column names. `GET /v1/executions`
returns the caller's own records only.

### Refresh-token retry (lost response)

Assessed as practical. A client may send an `Idempotency-Key` header with a refresh. If the response is
lost, the **same token + same key**, within **30 s**, **once**, and while the issued child is still unused,
gets a replacement and the first child is marked used (superseded). Everything else is still reuse and
revokes the session: no key, a different key, a second retry, after 30 s, or after the child was used.
Someone holding only a stolen token lacks the key; someone who captured the whole request gets at most one
replacement inside 30 s, and the real client's next refresh then trips reuse detection.

## Hybrid memory (Milestone 5)

| Kind | Where it lives |
|---|---|
| Working memory | LangGraph checkpoints, PostgreSQL schema `lg_checkpoints` (separate from long-term memory), 7-day default retention |
| Episodic / semantic / procedural | `memory_records`, content always encrypted; 180 d / until deleted / until deleted |
| Structured domain memory | `memory_records` with a registered, validated schema; exact lookup by `(namespace, key)` in SQL; **never embedded** |
| Semantic retrieval | `memory_embeddings` (pgvector) over **approved, redacted summaries only** |

PostgreSQL stays the source of truth. Exact financial/transactional questions use SQL, never vectors.

### Namespaces and access
`<agent_id>/…` is agent-specific; `user/…` is user-level memory that an agent can reach **only** if its active
version was explicitly granted it. There is no way to grant one agent another agent's namespace. Every query is
scoped by the authenticated user id (never by input); another user's ids look nonexistent. Agents get an
`AgentMemory` handle bound to their `RunContext`: it exposes propose / read / search / context but **no**
approve, reject, delete or erase. Agent-driven memory access stops under the kill switch; the owner's own access
and deletion do not.

### Writing memory: proposals are validated
Anything an LLM proposes is validated (schema, size, control characters, namespace, grant) and the server never
trusts a declared sensitivity downward (PII, financial/health words and `finance_agent/…` raise it). It then
waits for the user when it is **sensitive, third-party, a procedural rule, a consequential structured change,
instruction-like, or would replace a user-confirmed fact**. A confirmed fact is **never silently overwritten**:
the replacement is a pending proposal; the old value stays active until the user approves (then it is kept
encrypted for a 30-day undo window and removed from search). Hard DB `CHECK`s back this up: third-party text can
never be procedural, structured data can never be embedded, unconfirmed third-party text can never be embedded.

### Messaging privacy (built now, used by the Messaging Agent later)
- **Raw third-party messages are never stored**, logged, embedded or put in audit. `raw_source` is used only to
  reject near-verbatim copies and injection patterns, then discarded.
- Only a **summary** can be proposed, in `<agent>/<platform>/<conversation_id>` (opaque ids, never display
  names). It is a pending, encrypted proposal that **expires in 2 days** unless approved; approved summaries are
  kept 90 days by default.
- **Embeddings** come only from an `embed_text` that the user explicitly approved and that passed PII
  redaction checks (e-mails, handles, URLs, IPs, long number runs). Never from raw messages. Regex redaction is
  best-effort (it cannot find names); the human approval is the real control.
- **Injection:** instruction-like text is flagged, forced to wait for the user, kept out of search until
  reviewed, and always rendered as one JSON line of quoted data (`<`/`>` escaped) under a preface that says it is
  data. Memory text can never change permissions or tools.

### Encryption (application-level, envelope)
Each record gets its own random data key (AES-256-GCM); that key is wrapped by a master key from a keyring
**outside PostgreSQL** (`secrets/memory-keyring.json`, mode 600, mounted as a Docker secret, git-ignored). The
database stores only ciphertext, nonce, wrapped key and the key *id*. Ciphertext is bound to its user, record and
purpose, so copying a blob to another row fails. Rotate with `memory add-key` (host) + `memory rotate-keys`
(re-wraps keys, content untouched); verified on the live stack with the old key removed. **Back the keyring up
separately from database backups: losing it makes encrypted memory unrecoverable.**
Checkpoints are encrypted too. LangGraph writes plain string/number state inline as plaintext JSON, bypassing any
serializer cipher, so `EncryptedPostgresSaver` boxes those values into encrypted blobs and refuses to load
unencrypted data. (Checkpoint ciphertext has a fixed context label, so it is authenticated but not bound to a
row; `checkpoints.metadata` stays plaintext — never put content in config metadata.)

### Embeddings
Provider-independent interface; **only a deterministic local TEST embedder exists** (`local-test-hash`): it
hashes words and word pairs, so it measures *lexical overlap, not meaning*, and results are labelled
`lexical-test`. It is refused in production unless `ALLOW_TEST_EMBEDDER_IN_PRODUCTION=true`. Any other provider
is refused until `embedding_external_approved` is set after you review its data policy, retention, training
practices and pricing; none is registered. The vector dimension (256) is fixed by migration 0006; a different
model/dimension needs a new migration and an explicit `memory reembed`. The app refuses to start on a mismatch.
**Embeddings are sensitive and stored UNENCRYPTED**: encrypting the vector column would stop pgvector from
computing distances. Mitigations: disk and backup encryption, per-user filtering, cascade deletion, access logs,
and vectors are never returned by any API.

### Deletion and retention
Deleting a memory removes the record, derived summaries (`derived_from`), every older version, its embedding,
scrubs the access-log references, and deletes **every historical checkpoint** of its source thread
(`purge_sources`, default on). `POST /v1/memory/erase` / `memory erase-user` erase all memory, all threads and all
memory access logs of a user. A receipt of counts (no content) goes to the audit log. Retention (`memory
purge-expired`, an operator job; there is no scheduler yet): proposals 2 d, third-party summaries 90 d, episodic
180 d, superseded 30 d, threads 7 d. Callers can only shorten retention.
Deleted rows physically remain in PostgreSQL until vacuum, and in older backups until they rotate out.

### Volume and backup encryption at rest
- `infrastructure/scripts/encrypted_backup.py`: streaming AES-256-GCM backups (chunk index + final flag are
  authenticated, so truncation, reordering and bit flips are detected; the key file stays off the database).
  `pg_dump -Fc … | encrypted_backup.py encrypt --key-file K > db.enc`; restore by decrypting into `pg_restore`.
- `infrastructure/scripts/verify-encryption-at-rest.sh` reports whether Docker's data volume sits on dm-crypt/LUKS.
  **It reports NOT VERIFIED in this development sandbox, and will on any host without disk encryption.** Enable
  provider volume encryption or LUKS on the VPS and re-run it before storing real personal data.

### Memory API and CLI
`/v1/memory` (list, create, edit, approve, reject, delete, namespace delete, erase, proposals, access-log,
threads) is the user's Memory Manager. Operator: `memory stats | purge-expired | add-key | rotate-keys | reembed |
erase-user`.

## Orchestrator (Milestone 6) - mock workflows only

The orchestrator is the single, governed entry point from a user request to an agent. **Every workflow is
a mock**: it runs the real pipeline (classification, routing, permissions, memory, spending, validation,
audit) against the in-process mock model and returns a placeholder reply labelled `mock: true`. No
finance, messaging or coaching functionality exists yet, and no external model provider is wired in
(`Orchestrator` composes only `MockAdapter`; a test blocks every non-loopback connection).

```
POST /v1/orchestrator/run   {"message": "..."}  + Authorization: Bearer ..  [+ Idempotency-Key: 16-128 chars]
GET  /v1/orchestrator/runs[?limit&before]        own run history (metadata only)
GET  /v1/orchestrator/runs/{id}                  own run status (+ reply while its encrypted copy has not expired)
GET  /v1/orchestrator/workflows                  registered workflows and their bounds
```

**Identity.** The execution identity is the authenticated user; the body has no user field and extra
fields are rejected (422). Another user's run id is a 404.

**Pipeline** (`core/orchestrator/graph.py`, LangGraph, compiled *without* a checkpointer so nothing is
saved automatically): classify -> select agent -> govern (`start_run`: kill switch, agent registry,
active version, execution record; model-alias permission) -> assemble context -> execute <-> validate
(bounded retry) -> remember (proposals only) -> respond; any handled failure goes to `fail`.

| Intent | Agent | Notes |
|---|---|---|
| finance / messaging / coaching | `finance_agent` / `messaging_agent` / `daily_coach_agent` | mock workflow |
| general | `general_agent` (new front-desk agent, seeded disabled) | mock workflow |
| ambiguous, clarification needed | `general_agent` | static clarification question, no model call |
| unsupported (payments, auto-send, governance bypass, other users) | `general_agent` | static refusal, no model call |

The classifier is deterministic and rule-based behind a `Classifier` protocol and can only return an
`Intent` enum, so neither user text nor a future model-based classifier can name an agent, model or tool.
Text such as "use the finance_agent" is flagged and ignored.

**Bounds per workflow** (`WorkflowSpec`): max graph steps, timeout, retries (provider errors and
malformed output only; governance refusals are never retried), max memory proposals, context budget,
model alias, readable and writable memory roots, and an empty tool allowlist (the registry refuses
tools). The agent version's own limits (steps, tokens, cost, time) and the spending controller apply on top.

**Memory.** Only the workflow's own agent namespace, plus `user` only if the agent version was explicitly
granted it, is read; another agent's namespace or another user's memory is unreachable. Retrieved memory
is passed as quoted untrusted data. Model-proposed memories go through `MemoryService.propose` with a
forced `agent_inference` source, only into the agent's own namespace, capped per run; every one needs the user's approval
(initial-phase policy). Conversations are not saved.

**Idempotency.** With an `Idempotency-Key`, a repeat returns the stored reply (`replayed: true`); the same
key with a different body is 409; a concurrent duplicate is 409; a failed run can be retried under the
key. The request is stored only as a keyed hash, the reply is envelope-encrypted and scrubbed after
`ORCHESTRATOR_RESPONSE_TTL_HOURS` (24h). Without a key no reply is stored at all.

**Kill switch.** Checked at the entry (before anything is claimed or replayed), again in `start_run`, and
before every model call. Covered for the HTTP route, `run_user_request`, `run_scheduled_task`, replays
and mid-run activation. `run_scheduled_task` takes a fixed enum, never free text.

Operator setup (agents are registered disabled; nothing runs until this is done):

```bash
docker compose --env-file .env -f infrastructure/docker-compose.yml run --rm admin orchestrator bootstrap-mock
docker compose --env-file .env -f infrastructure/docker-compose.yml run --rm admin orchestrator purge   # scrub expired replies, delete old runs
```

### Deployment security gates (tracked, not yet satisfied)

Do **not** use real personal, financial or messaging data until every gate is satisfied.
`infrastructure/scripts/security-gates.sh` prints `BLOCKED` for each until the operator acknowledges it.

| Gate | Status |
|---|---|
| Disk and Docker-volume encryption verified for Postgres and backups | **not verified** |
| Independent keyring backup + rehearsed recovery (losing the keyring makes memory unrecoverable) | **not done** |
| LangGraph checkpoint metadata is plaintext (content is encrypted) | **open** (accept or remove) |
| Production embedding provider approved (only the deterministic test embedder exists) | **none** |

Other Milestone 6 limits: the rule-based classifier is brittle (unusual phrasing, English only, no
conversational context; misses degrade to clarification/general, never to an action); the mock model
proves orchestration, not answer quality; `general_agent` is a fourth agent beyond the three mandatory
ones; the 24 h reply cache is the only place a reply is persisted and only when a key is sent.

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
uv run alembic upgrade head   # 0001 registry · 0002 agents · 0003 auth+audit · 0004 agent versions · 0005 governance · 0006 memory
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

Paid models are **off by default**, no provider key is configured and LiteLLM's only model is a mock.
Monthly paid spend defaults to **INR 0** with an absolute ceiling of **INR 300** enforced by the ledger
described above. LiteLLM's `max_budget: 0.01` USD is only a secondary gateway guard, not the budget
controller.

## Roadmap

1. ✅ Repository and infrastructure foundation
2. ✅ Model Registry, Agent Registry foundations, provider abstraction, mock end-to-end flow
3. ✅ Authentication and the Agent Registry (versions, permissions, limits, audit)
4. ✅ Governance and cost control (INR 300/month ledger, kill switch, execution records)
5. ✅ Hybrid memory (encrypted, isolated, deletable; local test embedder)
6. Orchestrator · 7. Domain agents · 8. Mobile app · 9. Testing and deployment

## Known issues and limitations

- **Memory — no production embedding model.** Search quality today is lexical (test embedder). A real
  provider needs your approval of its data policy, retention, training and pricing first.
- **Embeddings are unencrypted** (see above); a stolen database *without* disk/backup encryption would expose
  them. Disk encryption is **not verified** here.
- **Deletion is logical-then-physical:** rows persist until vacuum; old backups keep them until they expire.
- **Redaction is regex, not NER**; it cannot detect names. The user's approval of `embed_text` is the control.
- **Checkpoint metadata is plaintext**, and checkpoint ciphertext is not row-bound (LangGraph gives the cipher no
  row context). The saver subclass overrides one protected library method; the library is pinned and
  regression-tested.
- **HNSW + per-user filtering** relies on pgvector >= 0.8 iterative scan (0.8.7 here); with older versions a
  filtered search could return fewer rows than requested.
- **No retention scheduler or job queue**; `memory purge-expired` must be run by the operator.
- **Key custody:** the keyring is a file next to the application; anyone controlling the running app (or the
  host) can read it. A KMS/HSM `KeyProvider` is a drop-in later.
- Messaging tables (`people`, `conversations`, …) are not built; this milestone provides the privacy layer they
  will use.
- **Only the orchestrator endpoint executes a model (mock only); no endpoint executes a tool.** The tool registry is empty; approvals are enforced and
  tested but there is no approval-granting API yet.
- **No paid provider has ever been called.** The spending machinery is verified with scripted fake
  providers only. `gemini-3.1-flash-lite` availability and rate limits are unverified live.
- **Paid spending is single-lane by design.** Every reservation takes one row lock, so paid calls are
  serialised. Fine for one user; it is a throughput limit.
- **Initial-phase memory policy:** every long-term memory proposed by an agent stays `proposed` until the
  user approves it (`REQUIRE_APPROVAL_FOR_AGENT_WRITES` in `core/memory/service.py`, a code constant, not a
  setting). An agent update of an existing memory is a pending proposal; the old value stays active until
  the user approves. Approval resets retention to the type default, so an agent's requested shorter
  retention is not carried across approval (the user's own writes honour it).
- **Milestone 6 first full test run had 2 failures:** `test_migrations.py` (both tests) still expected the
  pre-0007 table set. Reproduced deterministically on the exact commit (`9707259`) in an isolated worktree:
  the same two failures, nothing else. A genuine omission in a test expectation (not environmental, not
  flaky, no product defect); fixed in the Milestone 6 commit. The earlier report of "one identified, one
  unidentified" was wrong: my run's output was truncated to its last lines, which hid that both failures
  were the same cause.
- **Held amounts are deliberately conservative.** A timeout or unknown cost counts the full worst-case
  reservation and freezes paid spending until the operator reconciles it (`spend resolve`, `spend unfreeze`).
  That is manual by design: the operator must read the provider's invoice.
- **A call is booked to the day/month it was reserved in**, even if it finishes after midnight.
- **Cost trust:** confirmed cost relies on provider/gateway-reported usage or cost. A provider that
  under-reports cannot be detected until reconciliation against an invoice.
- **The USD→INR rate is operator-maintained** (stale after 14 days → paid refused). There is no live FX feed.
- **The kill switch stops NEW work.** A model call already in flight when it is activated is allowed to
  finish. There is no job queue yet; queued work is covered by the "start through `start_run`" contract.
- **Data export** is listed as "must remain available" but no export feature exists yet.
- **Rate limits are per process and in memory** (no Redis by design). Fine for one worker; scaling to
  several multiplies the effective limit. They reset on restart.
- **Refresh tokens are bearer tokens, not device-bound.** Rotation, reuse detection and the one-shot
  idempotent retry limit the damage of theft; device binding (App Attest / DPoP-style) is future work.
  The iOS app must keep the refresh token in the Keychain (`ThisDeviceOnly`).
- **HS256 shared secret:** rotating `JWT_SECRET` ends all sessions.
- **Enrollment is a manual, operator-issued code** (single-user by design).
- Still not built: `model_versions`, `model_evaluations`, domain agents, mobile app. The orchestrator exists but runs mock workflows only.
- Registry tables are system-level (no `user_id`); user-owned tables will carry one.
- Starlette emits a deprecation warning about `httpx` in `TestClient`; harmless.
- Building the API image inside this agent's sandbox needed a CA workaround for its egress proxy; the
  committed Dockerfile has no such change and has not been built unmodified here.
