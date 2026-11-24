# Abu AI mobile: testing, accessibility and security strategy (plan)

All testing uses synthetic data and the mock model. Real finance, messaging, calendar and personal data are never used.

See also [PHASE_MILESTONES](PHASE_MILESTONES.md): the **test report format** delivered at every sub-milestone gate and the **continuous security and privacy checks** (sections B and C there) are part of this strategy.

## 1. Test pyramid

| Layer | Tooling (candidates) | What it covers | Runs in Codespaces/Linux? | Runs in CI |
|---|---|---|---|---|
| Static | `tsc --noEmit` (strict), ESLint (+ a11y rules), custom lint rules, dependency audit, secret scan | types, no `parseFloat` on money, no raw hex in components, no `console.log`, no `fetch` outside `src/api`, no `AsyncStorage` for secrets | yes | every PR |
| Unit | Jest (`jest-expo`) | money formatting (property tests), error parsing (both shapes), session state machine, single-flight refresh, idempotency keys, redacting logger, theme resolution, token contrast maths, deep-link allowlist | yes | every PR |
| Component | React Native Testing Library | every design-system component in light and dark; states (loading/empty/error/kill-switch); accessibility roles/labels | yes | every PR |
| API mocking | `msw` | screens against canned (synthetic) responses incl. error shapes | yes | every PR |
| **Contract** | Jest against the **live Docker backend** (seeded synthetic data) | every endpoint the app calls: request validation, zod schema vs real response, error shapes, money-as-string, auth/refresh/rotation, idempotent refresh retry, kill-switch 503 behaviour, rate-limit 429, size limits | yes (needs Docker) | nightly + before releases |
| Visual regression | `react-native-web` gallery + Playwright (available here) | components and key screens, Light and Dark, large text; diff against baselines | yes (web approximation) | every PR |
| End-to-end | Maestro flows (authored here) | enroll, home, add/confirm transaction, import statement, ingest alert, reply draft copy, task/habit flow, assistant request, kill switch, appearance switch, erase | needs simulator/device | on macOS runner / device before milestones |
| Native | JS fakes + manual device scripts | Share Extension, App Intents, EventKit, speech, notifications | fakes yes; real **device only** | manual, recorded in phase report |
| Accessibility audit | RNTL queries, ESLint a11y, manual VoiceOver/Dynamic Type/Reduce Motion pass | see section 3 | partly | manual per phase |
| Performance | Flashlight/profiler on device, React profiler, bundle-size budget | cold start, list scroll, orb frame rate, memory | partly | per phase |

## 2. Critical test cases (must exist before the phase that introduces them ends)

**Auth and session**
- Enroll success/failure (expired, reused, invalid code; 429 backoff).
- Silent refresh before expiry; concurrent callers share one refresh (single-flight).
- Lost refresh response: retry reuses the same refresh token **and** idempotency key; the backend accepts once.
- Refresh `401` => session expired => secrets wiped => enrollment shown; a second 401 on a call ends the session.
- Token never appears in logs, route params, deep links, error text or persisted storage (asserted by scanning storage mocks and log sinks).
- Logout and "revoke other device" behave; a revoked session is detected on the next call.

**Money and data integrity**
- No float arithmetic on amounts (lint + property tests: formatting round-trips for edge strings like `0.10`, `1000000.00`, negatives).
- Unreviewed transactions are never presented as counted in totals; review/confirm flows refresh summaries.
- Import: size and type checks before upload; 409/413/422 handling; file bytes not persisted.

**Safety invariants (enumerated, not assumed)**
- A test lists every API call the app can make and asserts there is **no** payment, transfer or message-send call (the backend has none; the client must not add one).
- Reply drafts are always labelled "Draft - not sent"; no action posts to any other app.
- Memory proposals appear only as pending; approval requires an explicit user action; there is no auto-approve code path.
- Kill switch: agent surfaces show the banner and block runs; own-data screens still work; the app cannot resume (the deliberate 403 is shown as "resume from the operator CLI").
- Production URL rejected while `productionGatesVerified` is false; the synthetic chip is always visible.

**Orchestrator UX**
- Idempotency-Key per submit; double-tap does not create two runs; replay shows `replayed`.
- Each response status renders correctly (`completed`, `clarification_needed`, `unsupported`, `failed` mapped from HTTP errors).
- 504 timeout retry with the same key; 429; 403; 409 conflict.

**Appearance**
- Resolution matrix (system light/dark x override system/light/dark); live OS change updates the tree without remount (component test with a mocked appearance emitter, then a device check).

**Privacy**
- No persisted query cache; AsyncStorage contains only the appearance/lock preferences (asserted).
- App-switcher blur fires on `inactive`; lock engages after the configured delay.
- Clipboard use limited to explicit Copy; no pasteboard reads without user action.

## 3. Accessibility requirements and checks

Requirements (all screens): WCAG 2.2 AA contrast in both themes plus Increase Contrast; 44x44 pt targets; Dynamic Type through
accessibility sizes with no clipped critical figures; VoiceOver labels/values/hints, logical order, headings, rotor-friendly
sections; Reduce Motion, Reduce Transparency, Bold Text, Differentiate Without Colour; charts have text equivalents; focus
management after sheets and errors; no timeouts that hide content without user control; external keyboard and Switch Control focus order.

Checks: automated (ESLint a11y rules, RNTL role/label queries, contrast script as a CI gate); per-phase manual audit with a
written checklist (VoiceOver on device, largest Dynamic Type, Reduce Motion on, Increase Contrast on, dark and light);
accessibility defects are phase-blocking at "serious" severity.

## 4. Security considerations and how they are verified

| Concern | Control | Verification |
|---|---|---|
| Token theft | Keychain `ThisDeviceOnly`; access token in memory only; no iCloud sync | unit tests on storage adapter; device check that the item is not backed up |
| Token leakage | redacting logger; no analytics/crash SDKs; no tokens in URLs/deep links | log-sink tests; lint ban on `console`; dependency review |
| Rotation race | single-flight refresher; extensions never refresh | concurrency unit tests; architecture review |
| MITM | HTTPS-only release builds (ATS), allowlisted base URL; no cleartext except debug dev client | build-profile test; manual proxy check in preview build |
| Data at rest on device | no persisted cache, no file copies, import files read to memory only | storage-scan test; manual file-system inspection on a debug build |
| Screen exposure | app-switcher blur, optional biometric lock, no sensitive notification previews (generic text) | device checklist |
| Malicious files | client size/type pre-check; server parses with limits; file never executed | contract tests with oversize/corrupt fixtures |
| Deep-link abuse | allowlist, no mutations, params validated | unit tests |
| Supply chain | lockfile pinned, `npm audit` in CI, minimal dependency set, licence check, no postinstall surprises | CI gate; each new dependency reviewed |
| Secrets in bundle | none; only a base URL | bundle scan in CI |
| Backend controls | the app does not bypass or duplicate them (kill switch, approvals, limits stay server-side) | contract tests; "no weakening" review in every phase |
| Premature real data | synthetic chip, URL allowlist, gates BLOCKED text; fixtures are synthetic | review checklist; CI check that fixtures contain no real-looking identifiers |

## 4b. Where tests run given the approved development setup

- Backend-dependent tests (contract, Playwright E2E on the web build) run **inside Codespaces** against the Docker backend (no network exposure needed).
- The phone, if used, runs **demo mode** (fixtures, no credentials) via Expo Go; real-backend-on-phone checks are deferred (see BACKEND_PREREQUISITES).
- A test asserts demo mode cannot be compiled into a production profile and that the production-URL block holds while the gates are BLOCKED.
- Supply-chain gates in CI: `npm audit`, licence allowlist, install-script allowlist, lockfile integrity, bundle secret scan.
- OpenAPI drift: the committed snapshot must match the backend (backend test, task B2) and the generated types must be up to date.
- B1/B2 are validated by their own backend test suites plus the unchanged full backend regression run.

## 5. Quality gates per phase

A phase is done only when: type-check, lint, unit and component tests pass; the web gallery diff is clean (or baselines are
re-approved); contract tests for the endpoints it uses pass against the live backend; the accessibility checklist for its screens
is recorded; no new dependency was added without review; and the phase report lists unresolved risks. Native items report
"verified on device" explicitly.

## 6. Test data

Synthetic only: a seed script (using the existing backend CLI/API) creates a demo user with accounts, transactions, a budget,
a loan, subscriptions, an imported synthetic chat, tasks, habits, goals and events. Fixtures carry obviously fake names and
numbers. A CI check rejects fixtures matching real-looking card, phone, email or account patterns.

## 7. Acceptance criteria (testing)

All items in MILESTONE_8_PLAN section 8 have at least one automated test or a documented device check; contract tests cover 100% of
endpoints the app calls; no test uses the network except the local backend; the visual and accessibility baselines exist for both
themes; the backend regression suite is unchanged and green.
