# B3 report: documentation-only backend response contracts

Status: implemented and verified. **Stopped; M8.2 not started; F1 fix not started.** Synthetic data and the mock model only; the four production security gates remain BLOCKED. Plan: `docs/mobile/B3_PLAN.md` (commit `402df02`, with an Errata section added during implementation).

## 1. Commits (in order)
| Commit | What | Backend source touched? |
|---|---|---|
| `5184c2c4366cb2324104fa59d2b7f9252c6faee3` | Golden baseline recorded **before any B3 change** (test code + `tests/contracts/golden/golden.json`) | no |
| `8423780f4f637429939a5cfe6b00ff99c4265f8b` | Baseline extended with the `finance.assistant.mock` workflow; the 60 existing entries unchanged except placeholder numbering. **Last commit with the pre-B3 backend.** | no |
| `851213110a8e45f33ec8c2e7b22301ebc99772f0` | **Implementation commit** (contracts, wiring, tests, workflow, regenerated snapshot and mobile types, plan errata) | decorators and imports only (below) |
The golden file was never regenerated after implementation; the post-B3 comparison reads the file committed in `5184c2c`/`8423780`.

## 2. Backend test counts and baseline comparison
| Run | Commit | Result |
|---|---|---|
| Clean baseline (separate `git worktree`, pre-B3 backend incl. golden test) | `8423780` | **802 passed, 3 skipped**, 1 warning (existing JWT key-length warning), 400 s |
| Full suite after B3 | `8512131` working tree | **881 passed, 3 skipped**, 1 warning, 454 s |
The +79 are exactly the new tests: 60 contract-model unit tests, 11 error-table unit tests, 8 live conformance tests. No existing test changed. Static checks: `ruff check` clean, `ruff format --check` 211 files clean, `mypy` 191 source files clean (baseline: 196 files formatted before the new files, 176 sources). The last small test-typing edits were re-run (90 tests across the new files, golden, OpenAPI) after the full run.

## 3. Golden and contract results
- **Golden comparison (byte for byte): PASS.** 62 recorded responses (routes for a populated and an empty user, 18 workflow families for both users, messaging and idempotency cases, replay and conflict, 401/404/422/503 envelopes, kill switch with domain reads still 200) are identical before and after B3, including status codes and the headers recorded. Recording was verified deterministic (two recordings byte-identical) before commit.
- **Live conformance, all 18 tool results: PASS.** Each tool is invoked through the registered handler (independent of the orchestrator's summary step), and every workflow's `data` is validated against `ToolResults`. The two sets are asserted equal to `agents.tools.SPECS`.
- **Error conformance: PASS.** Real 401 (missing and garbled token), 403/404/422 domain envelopes, orchestrator `invalid_request` 422, `idempotency_conflict` 409, kill-switch 503, domain 503 `detail`, per-user and per-IP 429 with `Retry-After`, `malformed_output` and `provider_error` (both HTTP **502**, run-failed envelope; resolves F11). The exception -> (status, code) table of `_classify_error` is pinned by unit test. Every emitted `error.code` is in the documented set.
- **Unexpected shapes:** extra field, missing field, float money, non-decimal string, reshaped tuple, unknown tool key, bad enum and float `params` are all rejected by the strict models (the drift alarm), and nullable/optional/empty variants are accepted.
- **Fixture privacy scan: PASS** (no email, mobile-number-like, card-like digit run, URL, JWT or backend token shape in `tests/contracts/`); every invented name is marked "(demo)".

## 4. OpenAPI schema changes (verified by diffing the old and new snapshot)
Paths unchanged (same set). Operations changed: only `GET /v1/finance/{summary,insights,upcoming}`, `GET /v1/coach/briefing` (200 now has a schema; 401/429/503 documented) and `POST /v1/orchestrator/run` (422 now `oneOf` validation error or error envelope; 401/403/409/429/500/502/503/504 documented). Components: **52 added, 0 removed, 1 modified** (`OrchestratorResponse.data`: `dict` -> `$ref ToolResults | null`, published only; runtime unchanged). All other top-level and component sections are identical. Mechanism: `responses={200: {"model": ...}}` on four decorators plus a one-time OpenAPI post-processor (`services/api/contracts/openapi.py`, installed in `create_app`) for the orchestrator `data` type and the error tables.

## 5. Generated mobile type changes
`mobile/src/api/generated/schema.d.ts`: +1016 / -10 lines; 52 new schemas; the only existing schema changed is `OrchestratorResponse` (`data?: components["schemas"]["ToolResults"] | null`). `npm run api:types:check` passes, `tsc --noEmit` passes, and `npm run check:all` passes locally (249 tests, licence/audit/secret gates unchanged).

## 6. GitHub Actions
Both runs are on implementation commit `851213110a8e45f33ec8c2e7b22301ebc99772f0`, clean checkouts on `ubuntu-latest`, conclusion **success** (post-job cleanup included):
- `backend-openapi` (new): https://github.com/abu2508/Abu-AI/actions/runs/45337298446. `uv sync --frozen --no-dev` and `python -m services.admin openapi check` passed (17 s), so the workflow setup works.
- `mobile`: https://github.com/abu2508/Abu-AI/actions/runs/45337298181. All gates passed, including `api:types:check` against the regenerated snapshot (I did not pull the job log text; results are the run conclusion plus the identical local `check:all`).
The new workflow's detection of a stale snapshot was demonstrated locally (section 9); a deliberately stale push to GitHub was not made.

## 7. F1 status (important correction)
**F1 as written in the plan is not reproducible end to end.** I reproduced the `TypeError` only by calling `agents.summaries.summarise` with raw `datetime` objects. In the application every tool handler is wrapped in `jsonable` (`build_registry`), so `summarise` always receives ISO strings. Evidence: the baseline golden contains a real "Plan my day" run with three scheduled blocks that **completes** with the summary `12:00-13:00 Write demo report (demo)` etc.; the conformance suite pins it (`test_the_coach_plan_workflow_with_scheduled_blocks_completes`), and the raw `coach.plan` output (3 blocks) was validated independently through the registry handler. I did not fix or change anything for F1. The plan's D6 (separate F1 fix) therefore looks unnecessary, and the F5 `Z`-versus-`+00:00` concern also disappears (both paths emit `isoformat()`). Whether any other defect exists in the coach-plan summary path is not shown by these tests; I recommend closing F1 unless you want a deeper review.

## 8. Scope compliance
`git diff 8423780..HEAD` for backend source: `services/api/coach.py` (+1 import, +1 decorator argument), `services/api/finance.py` (+1 import, +3 decorator arguments), `services/api/main.py` (+1 import, +1 `install(app)` call after the routers), plus the new `services/api/contracts/` package, which is imported by nothing else (asserted by a test). Handler bodies, signatures (`-> Any`), serialisation, statuses, error codes, authentication, authorisation, migrations and dependencies: unchanged. No new dependency. About `response_model`: the handlers' `-> Any` annotation already makes FastAPI report `response_model == typing.Any` (no validation or filtering) both before and after B3; B3 sets no concrete model, and a guard test asserts `None` or `Any`. Literally `None` is not what these routes had.

## 9. New workflow: backend OpenAPI snapshot
`.github/workflows/backend-openapi.yml`: runs `python -m services.admin openapi check` (offline: no database, no secrets, no model calls) on pushes/PRs touching `services/**`, `core/**`, `domain/**`, `agents/**`, `database/**`, `mobile/openapi/**`, `pyproject.toml`, `uv.lock` or the workflow, and on manual dispatch. Detection demonstrated locally: before exporting the snapshot the check exited 1 ("missing or stale"); a temporary edit of one contract description (backend-only change) made it exit 1; reverting made it exit 0.

## 10. Remaining risks and limitations
1. Contracts for finance/coach/tool routes are validated only by the synthetic scenario; unusual real-world branches (for example a loan or goal `status` value not seen) are typed as open strings by design (D5).
2. `extra="forbid"` means any legitimate additive backend change fails the conformance tests until the contract is updated (intended).
3. Only the four routes and `POST /v1/orchestrator/run` carry the new error tables; other routes are untouched.
4. The golden scenario freezes the clock by patching service clocks in the test process only; real-clock behaviour is covered by the existing suite.
5. The workflow installs with `uv sync --frozen --no-dev`; its first run is the real test of that setup (result in section 6).
6. `messaging.analyse.topics` are words from the other party's messages (existing behaviour, shared conversations only); the contract adds no exposure.

## 11. Review outcome (recorded 2026-11-24)
B3 formally approved (implementation commit `851213110a8e45f33ec8c2e7b22301ebc99772f0`). F1 closed: the datetime-slicing defect was reproduced only outside the real execution path, and the scheduled-block workflow succeeds through the registry and orchestrator; no separate fix is required. The `response_model` discrepancy is accepted; the acceptance criterion is **"no concrete enforced response model"** (the `-> Any` annotations make FastAPI report `typing.Any`, and no response validation or filtering was introduced).
