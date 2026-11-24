# Contract tests against the synthetic backend: prerequisites and safe invocation

Local only. **Not** part of GitHub Actions, **not** a Codespace guarantee, **not** a device test. Synthetic data and the mock model only; no real credential, provider key or personal data is involved.

## What it does
`mobile/scripts/contract-backend.sh` starts the real backend (unchanged) against a **throwaway PostgreSQL database**, seeds synthetic users and data, and writes `mobile/.contract.env` (git-ignored, mode 0600) with the URL and **single-use enrollment codes**. `mobile/tests/contract/*.contract.ts` then run the real mobile runtime over real HTTP.

## Prerequisites
- The local PostgreSQL container from `infrastructure/docker-compose(.dev).yml` listening on `127.0.0.1:5432` (in the sandbox: start `dockerd`, then `docker start abu-ai-db-1`).
- `/.env` at the repository root with the local Postgres admin credentials (read by the script, never printed, never sent anywhere).
- The Python venv at `.venv`, plus `node` and `curl`.

## Safe invocation (recommended)
```bash
# from the repository root, or: cd mobile && npm run contract:run
bash mobile/scripts/contract-backend.sh run            # up -> all contract suites -> down (down ALWAYS runs)
bash mobile/scripts/contract-backend.sh run --verbose  # extra args go to Jest
```
Manual control: `up`, `down`, `killswitch activate|deactivate` (used by the Home contract test). If you use `up` yourself, run `down` afterwards. **Do not run `npm run test:contract` twice against one `up`:** enrollment codes are single-use, so the second run fails with 401. Use `run`, or `down` then `up` before each run.

## Isolation guarantees (and how each is enforced)
| Guarantee | Mechanism |
|---|---|
| Clean state for every run | every `up` first tears down any previous instance, then creates a **new** database `abu_contract_<12 hex>`, runs migrations, seeds, starts a **new** API process |
| Fresh single-use codes | all enrollment codes are minted by `up`; nothing is cached between runs; JWT, token-hash and memory-KEK secrets are random per run |
| Reliable cleanup, including failure | `run` uses an `EXIT` trap (success, test failure, SIGINT/SIGTERM); `up` rolls back (kills the API, drops the database, removes the state file) on any failure after it began creating things; `down` is idempotent |
| Never a real or production database | the script ignores `ABU_DATABASE_URL`/any URL from `.env`; it builds its own URL from the admin credentials for `127.0.0.1` only, forces `ABU_ENV=test`, and will only `CREATE`/`DROP` names matching `^abu_contract_[0-9a-f]{12}$` (anything else is refused, the state file is kept and the script exits non-zero) |
| No signalling of unrelated processes | `down` only kills the PID recorded in the state file **if** its command line is this script's `uvicorn services.api.main:create_app` |
| No reuse of a foreign listener | `up` refuses to start if port 8765 already accepts connections |
| Deterministic suite order | `jest.contract.sequencer.js` runs `home.contract.ts` first and `auth.contract.ts` last: its deliberate burst of wrong codes exhausts the shared per-IP auth limiter and made the outcome depend on Jest's cached ordering (this was the cause of intermittent 429s) |
| Deterministic clock (no time-of-day flakiness) | the live backend is started through `mobile/scripts/contract-api.py`, which freezes `CoachService.now` in **that test process only** (the same technique as `tests/contracts/scenario.py::freeze_clock`, which the B3 golden was recorded with; no backend source changes, refuses to run outside `ABU_ENV=test`). The frozen instant is today's UTC date at 06:30 UTC (= 12:00 in the default Asia/Kolkata zone), taken from one `date` call that also feeds the seed script, and is written to `mobile/.contract.env` as `ABU_CONTRACT_NOW`. Previously the planner's free window started at the real "now", so a briefing later in the day listed `plan.unscheduled` items the golden had never contained and two Home tests failed by time of day |
| Nested `unscheduled[]` coverage | the seed adds a 480-minute task that never fits a free window, so every run has exactly one `unscheduled` item; its fields are checked against the backend's OpenAPI contract (`UnscheduledOut`, additionalProperties false) while `unknownFields()` stays strict (see `tests/contract/home.contract.ts`) |
| Limiter headroom | auth limit 60/min and IP limit 2000/min for the local run; per-user limit stays at the production default (300/min) |

## Verified behaviours (this follow-up, executed locally)
`run` three times in a row: 13/13 each, no state file, no `abu_contract_*` database, no API process left. Forced failure after the database and API existed (`NODE_OPTIONS` made the seed step fail): rolled back, exit 9, nothing left. Foreign server on port 8765: refused before anything was created. SIGTERM during `run`: exit 130, nothing left. Tampered state file naming `postgres` and PID 1: refused, PID 1 untouched, state kept for inspection.
