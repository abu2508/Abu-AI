# Milestone 8: backend prerequisites and development connectivity (plan)

Two small backend tasks are approved *in principle* and will be implemented only after you approve the refined plan. Each is a
separate, reviewable change with its own tests and the **full existing backend regression suite**. No existing security control is
weakened. Synthetic data and the mock model only; the four production gates stay BLOCKED.

## B1 - Messaging inbox batch endpoint (closes G5; needed before M8.4)

### Problem
`GET /v1/messaging/conversations` returns conversation metadata but not reply status. The inbox badge ("needs reply") currently needs
`GET .../analysis` per row (N requests, each decrypting up to 300 messages). That is slow and needlessly touches message bodies.

### Proposed API
`GET /v1/messaging/inbox?limit=50&offset=0&shared_only=false` (authenticated; the user's own data; available during the kill switch, like the other own-data routes).

Response: list of
```
{ id, slug, platform, title, contact_id, shared_with_assistant, message_count, last_message_at,
  last_sender: "me"|"them"|null, needs_reply: bool, waiting_hours: number|null,
  last_message_intent: string|null, timestamps_reliable: bool, flagged_messages: int }
```
- **No message text** is returned. `last_message_intent` comes from the existing deterministic classifier applied to the single most recent
  *"them"* message when it is the last message (the same rule as `analysis`), decrypted in-process and discarded.
- Ordering: by `last_message_at` descending (nulls last). `limit` 1-100 (default 50), `offset` >= 0. Response header `X-Total-Count` for the badge.
- `GET /v1/messaging/conversations` is **unchanged** (compatibility).

### Implementation sketch
- One SQL query for the page of conversations, one `DISTINCT ON (conversation_id)` query for each conversation's latest unexpired message, and one aggregate for `flagged` counts: **a constant number of queries per page**, not per row. At most `limit` single-message decryptions.
- `timestamps_reliable`: today "labelled paste" imports have synthetic one-minute spacing and `analysis` guesses by pattern. B1 adds a boolean column `msg_conversations.timestamps_reliable` (migration 0011, default true) set at import (false for the `labelled` format), so the inbox never reports response times or `waiting_hours` for synthetic timestamps. Existing rows are backfilled with the same heuristic.
- Expired messages (`expires_at <= now`) are excluded exactly as in `analysis`.

### Tests (backend)
- User isolation (another user's conversations never appear; ids probed); unauthenticated 401; kill switch does not block it.
- Correctness equals `analysis` for the same conversation (property-style: needs_reply, last_sender, intent, waiting_hours) over several synthetic conversations, including expired, empty and unshared ones.
- Pagination bounds (limit/offset validation, total header); ordering.
- **Query-count bound** (e.g. at most 4 queries for a page of 50) using a SQLAlchemy event counter.
- No message text in the response or logs (scan); no embedding or memory side effects.
- Migration 0011 up/down and the existing migration test updated.
- Performance sanity: 50 conversations x 200 messages stays within an agreed bound.

### Risks
Bulk decryption of last messages (bounded by `limit`, in-process, no logging); the intent rules reused as-is; migration on existing data (default true + backfill).
**Estimate:** 2-3 sessions. **Needed before:** M8.4.

## B2 - Controlled OpenAPI (closes G9; needed in M8.0)

### Current state (finding)
`docs_url`, `redoc_url` and `openapi_url` are enabled whenever `ABU_ENV != production` and are **unauthenticated**. The compose default is `development`, so the schema and Swagger UI would be reachable by anyone who can reach the port (for example a forwarded Codespaces port). That contradicts the intended posture.

### Proposed behaviour
1. **Swagger UI and ReDoc are removed entirely** (`docs_url=None`, `redoc_url=None`); they load scripts from CDNs and add no value to the mobile work.
2. New setting `ABU_OPENAPI_MODE` = `off` (default, in **every** environment) or `authenticated`.
3. `authenticated`: the schema is served at `GET /v1/dev/openapi.json` **behind the normal bearer authentication**; the route is not registered at all in `off`.
4. **Startup refuses to run** with `ABU_ENV=production` and `ABU_OPENAPI_MODE != off` (fail closed, like the existing secret checks).
5. **Offline export (primary path for the mobile app):** `python -m services.admin openapi export --out mobile/openapi/openapi.json` builds the app object in-process (no server, no network, no credentials) and writes the schema. The committed snapshot is what `openapi-typescript` consumes, so the mobile build never needs a live endpoint.
6. A backend test regenerates the schema and fails if the committed snapshot is stale (contract drift gate).

### Tests (backend)
- Default: `/openapi.json`, `/docs`, `/redoc`, `/v1/dev/openapi.json` all 404.
- `authenticated`: 401 without a token, 200 with a valid token, schema contains no secrets or example credentials.
- `production` + `authenticated` -> app construction raises.
- The existing route-enumeration/public-route tests updated (the public set stays exactly `/health`, enroll, refresh).
- Snapshot drift test; export command works without a database.
- The hardening test that enumerates routes via `app.openapi()` keeps working (it calls the schema builder directly, not the HTTP route).

### Risks
Existing tests that relied on `docs_enabled`; developer convenience loss (mitigated by the offline snapshot). **Estimate:** 1-2 sessions. **Needed in:** M8.0.

## Development connectivity (decision 6)

You approved the **private** forwarded GitHub Codespaces URL with normal authentication, no credentials in the app, and no internal services exposed.

### What is firm
- Only the API container's port is forwarded; PostgreSQL and LiteLLM stay on their private Docker networks (as today); visibility stays **Private**.
- The app authenticates exactly like production: operator-issued enrollment code -> access/refresh tokens. No API keys or shared secrets are bundled.
- Backend setting: `ABU_TRUSTED_PROXY_HOPS=1` when running behind the Codespaces forwarder, so rate limits apply per real client and not to the proxy as a whole (to be verified in the M8.0 spike; otherwise one noisy client could lock everyone out).

### The constraint that needs your decision
A **Private** forwarded port requires the caller to be authenticated to GitHub. A browser does this with a cookie; a native app does not have one. Programmatic access to a private port normally means sending a GitHub token with the request (documented for forwarded ports; **to be verified in the M8.0 spike**, I cannot confirm it from here). That would mean a GitHub credential on the device, which conflicts with "keep credentials out of the mobile app". Options:

| Option | How it works | Credentials in the app? | Notes |
|---|---|---|---|
| **B (recommended default)** | Everything that needs the real backend runs **inside Codespaces**: contract tests, Playwright E2E against the web build, the web gallery. The phone (Expo Go) runs a **fixtures "demo mode"** with synthetic data and no network. | **None** | Zero extra exposure. Real-backend behaviour on a phone is covered later (M8.9 / when a stable HTTPS endpoint exists in Milestone 9) |
| A | Phone talks to the private port; the developer pastes a short-lived GitHub token into a **debug-only** screen at runtime (memory only, never bundled, persisted or logged) | A developer GitHub token, runtime only, debug builds only | Needs your explicit acceptance; scope the token as narrowly as GitHub allows; revoke after use |
| C | A private tailnet (for example WireGuard-based) between the Codespace and the phone | None in the app (network-level auth) | Adds a third-party account/tool, outside the current stack, needs approval |

Until you choose, M8.0 plans for **B** and treats A/C as optional additions. The spike (M8.0) records exactly how the Codespaces forwarder behaves with and without a token, so the choice is made on facts.

### Extra safeguards (all options)
- The app's backend-URL allowlist contains only the configured dev URL pattern; production URLs are rejected while the gates are BLOCKED.
- No real data in the dev backend: it is seeded only by the synthetic seed script; the mock model is the only model.
- The kill switch and all backend controls apply unchanged.
