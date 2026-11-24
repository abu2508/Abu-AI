# Backend prerequisite reports: B2 (controlled OpenAPI) and B1 (messaging inbox)

Branch `claude/charming-davinci-vqv0du`. Synthetic data and the mock model only; the four production gates remain BLOCKED.

## B2 - Controlled OpenAPI  (commit 90f8401)
- Full backend regression at that commit: **788 passed, 1 skipped** (the skip is the live-LiteLLM test, which needs the Docker gateway; it passed 16/16 live at Milestone 7). Includes the unchanged authentication, API, hardening and migration suites.
- Migrations: none in B2.
- Disabled by default: `/openapi.json`, `/docs`, `/redoc`, `/v1/dev/openapi.json` all return 404 in development, test and production with no setting (parametrised test, plus a direct run with `ABU_ENV=development` and no `ABU_OPENAPI_MODE`).
- Production refusal: `ABU_ENV=production ABU_OPENAPI_MODE=authenticated` fails at startup with `ABU_OPENAPI_MODE must be 'off' when ABU_ENV=production` (settings validation; tested and demonstrated).
- Authentication: with `ABU_OPENAPI_MODE=authenticated` (dev/test only) `GET /v1/dev/openapi.json` returns 401 without a token or with a bad token, 200 with a valid session, 401 again after logout; the public paths stay 404.
- Offline export: `python -m services.admin openapi export|check` runs with no environment, database or network; re-export is byte-identical; a test fails if `mobile/openapi/openapi.json` is stale; the schema contains no secrets and not the dev route.
- Also found and fixed: before B2 the schema and Swagger UI were served unauthenticated whenever `ABU_ENV != production`.

## B1 - Messaging inbox  (commit 8b70970)
- Full backend regression at that commit: **803 passed, 1 skipped** (same skip). Includes the 14 new inbox tests and the 0011 migration test.
- API: `GET /v1/messaging/inbox?limit&offset&shared_only`, limit 1-100, `X-Total-Count`; no message text in responses; exactly the documented 13 fields.
- Privacy rule (server-side): `last_message_intent` and `flagged_messages` are non-null only for conversations explicitly shared with Abu AI. For unshared conversations they are null and the message bodies are never loaded or decrypted (tested with a decrypt spy and by inspecting the executed SQL).
- Permission change test: share -> intent present; PATCH unshare -> the very next inbox call returns null, no body decrypted, the agent analysis path and the orchestrator tool both return `not_shared`; PATCH share -> intent returns immediately. A per-user test shows one user's change never affects another.
- Bounded queries: the statement count is identical for 3 and for 30 conversations (at most 12 including authentication); body decryptions are only for shared conversations whose last message is from the other party; no per-row analysis.
- Migration 0011 (`msg_conversations.timestamps_reliable`, default true): backfill marks conversations whose messages are exactly one minute apart (3+ messages) as unreliable; tested up, backfill result and downgrade; the full stepwise migration test passes.
- Mutation check: removing the shared guard makes 5 tests fail (restored).
- Other: isolation, auth, paging validation, ordering and total, expiry, kill switch does not block the owner's inbox, no memory/embedding/execution side effects. OpenAPI snapshot regenerated and its drift test passes. ruff and mypy clean.
