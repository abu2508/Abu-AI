# Milestone 8: independently reviewable sub-milestones

Each sub-milestone **M8.x** is a separate, reviewable unit with its own deliverables, acceptance criteria, test report, security
and privacy checklist result, and a **stop for approval** before the next one starts (same rule as Milestones 1-7). Estimates
are working sessions (focused effort, one developer with AI assistance) and are ranges, not commitments.

## A. Rules that apply to every sub-milestone

1. **Synthetic data and the mock model only.** The four production gates stay BLOCKED; the "Synthetic data" chip is present.
2. **Continuous security and privacy checks** (section C) run on every change and are re-reported at every gate.
3. **No new dependency** without the register process (DEPENDENCY_REGISTER section 6).
4. **Backend untouched** except the two approved prerequisite tasks (B1, B2), each reviewed separately with the full backend regression suite.
5. **Test report** (section B) is mandatory; "done" means the report is delivered and every acceptance item is marked pass, fail or
   *unverified (reason)*. Unverified is never reported as done.
6. A defect found later in an earlier sub-milestone reopens that report.

## B. Test report format (delivered at every gate; stored as `docs/mobile/reports/M8.x.md`)

1. Scope and commit(s); environment (OS, Node, Expo SDK, backend commit).
2. Acceptance checklist: each criterion -> evidence (test name, screenshot, log) -> pass / fail / unverified.
3. Results tables: static (types, lint), unit, component, contract (against the live Docker backend), visual (light/dark), accessibility, performance, security/privacy.
4. Coverage of endpoints used (contract tests) and of screens (component tests).
5. Dependency delta (added/updated/removed) with register entries and audit/licence scan output.
6. Security and privacy checklist (section C) with results and exceptions.
7. Known issues, unverified items, risk-register changes.
8. Request for sign-off.

## C. Continuous security and privacy checks

**On every pull request (CI, blocking):**
- Type-check strict, ESLint (including the custom rules: no `parseFloat`/`Number` on money, no raw hex colours in components, no `console.*`, no `fetch` outside `src/api`, no direct secret-store use outside `src/auth`, no `AsyncStorage` outside the preferences module).
- Unit/component tests, including: the redacting logger, token-never-in-storage/log tests, deep-link allowlist, money formatting properties, "no send/pay API call" enumeration test.
- Dependency checks: lockfile unchanged unless intended, `npm audit` (fail on high/critical), licence allowlist (fail on anything outside MIT/Apache-2.0/BSD/ISC/0BSD/BlueOak-1.0.0 without approval), install-script allowlist (only reviewed scripts).
- Secret scan of the repo and of the built bundle (the bundle may contain only a base URL).
- Synthetic-fixture scan: reject fixtures that look like real card/phone/email/account identifiers.
- Contract drift: the typed client matches the committed OpenAPI snapshot; the snapshot matches the backend (B2 test).

**At every sub-milestone gate (manual + automated, reported):**
- Storage scan: Keychain contains only the refresh token record; AsyncStorage contains only appearance/lock preferences; no files with private data on disk after exercising every screen; no persisted query cache.
- Network review: only the allowlisted backend URL contacted; HTTPS only in non-debug builds; no third-party hosts.
- Permission review: only permissions needed by shipped features, each with an accurate purpose string.
- Privacy surface review: screenshots/app-switcher blur, clipboard usage (explicit Copy only), notification text (generic, no amounts or names), logs.
- Backend-control review: confirm nothing client-side bypasses or duplicates the kill switch, approvals, limits or the draft-only rule.
- Gate status: the four production gates still BLOCKED and shown as such in About.
- Accessibility checklist for the screens introduced.

## D. Sub-milestones

### M8.0 - Foundations, spikes and controlled OpenAPI (4-6 sessions)
**Deliverables**
- Backend **B2** (controlled OpenAPI; offline snapshot export) with tests - see BACKEND_PREREQUISITES.
- `mobile/` scaffold on the approved SDK; strict TS; ESLint/Prettier; Jest; CI workflow; the custom lint rules in section C.
- Spikes (written findings, throwaway code not kept): (1) Expo Router + web + Jest wiring; (2) Skia/Reanimated render on web and in Expo Go; (3) **Codespaces connectivity** to the private forwarded port (verifies BACKEND_PREREQUISITES section 3); (4) secure-store behaviour and Keychain options.
- Tooling scripts: contrast checker (fails build), licence/audit scanners, fixture scanner, OpenAPI -> TypeScript types, synthetic seed script.
- Backend-URL allowlist + build profiles + the "Synthetic data" feature flag.
**Acceptance:** clean install from lockfile; CI green on an empty app; B2 tests green and full backend suite green; spike reports delivered; the app refuses a non-allowlisted URL (test); scanners wired and failing on seeded violations.
**Test report:** tooling run logs, spike findings, B2 backend report.
**Security/privacy:** supply-chain scans baseline; confirm no credentials in repo/bundle; OpenAPI not reachable unauthenticated.

### M8.1 - Core shell, API client, auth and session (6-8)
**Deliverables:** typed API client with zod validation and the unified `ApiError`; session manager (single-flight refresh, rotation with persisted idempotency key); secure storage adapter; redacting logger; Expo Router skeleton (onboarding, tabs placeholders, settings placeholders, lock gate); enrollment screen (typed code; QR scan deferred to when the camera dependency is approved/used); app lock and privacy blur; offline/kill-switch/error banners as shared states; contract test harness against the Docker backend.
**Acceptance:** enroll -> silent refresh -> logout against the live backend; lost-refresh-response retry uses the same token and key and is accepted once; concurrent calls share one refresh; refresh 401 wipes secrets and returns to enrollment; token never appears in storage/logs/URLs (tests); both error shapes parsed; rate-limit 429 honoured.
**Test report:** unit (state machine, single-flight, redaction), contract (auth flow incl. rotation and idempotent retry), storage-scan, concurrency tests.
**Security/privacy:** full section C pass; threat review of the refresh flow.

### M8.2 - Design system, appearance engine, Abu presence (8-10)
**Deliverables:** tokens (light/dark/increased contrast) with contrast CI gate; ThemeProvider with live System Default and local override; component library; original Bloom mark, icon variants, splash and onboarding visuals (vector sources committed); original domain glyph set (Finance, Messaging, Coach); SF Symbols wrapper for generic glyphs; `AbuOrb` with all states and Reduce Motion variants; chart primitives with accessible equivalents; web gallery with Playwright light/dark baselines.
**Acceptance:** every component renders in both themes and at large text; contrast gate green; live appearance switching proven by component test (mock emitter) and by the web gallery (media-query emulation); original-identity checklist (no Apple assets, no HUD/arc-reactor imagery); orb meets the frame budget in the web profiler and has a static fallback; **design review stop** with screenshots.
**Test report:** contrast report, gallery diffs, a11y component tests, orb performance notes.
**Security/privacy:** asset provenance record (all artwork original; font = system); no remote assets.

### M8.3 - Finance (10-12)
**Deliverables:** Overview, Transactions, Add (manual + voice-transcript typed fallback), Review queue, Import (CSV/XLSX/PDF), Alerts (paste), Accounts, Budgets, Loans, Subscriptions; charts; Home money/insights cards; empty/error/offline/kill-switch states.
**Acceptance:** every screen mapped to real endpoints with contract tests; unreviewed transactions never shown as counted; import size/type pre-checks and 409/413/422 handling; money formatting property tests; no float math (lint + tests); file bytes never persisted; a11y checklist (charts have text equivalents).
**Test report:** contract coverage for all finance endpoints used, visual baselines, import hostile-file cases (server limits), storage scan.
**Security/privacy:** document picker cache cleanup verified; amounts excluded from logs/notifications.

### M8.4 - Messaging (8-11, backend B1 first)
**Deliverables:** **B1 backend inbox endpoint** (separate reviewed change, full backend regression) then Inbox (single paged call), Conversation, Reply suggestions sheet (copy-only), Import (paste/file), Contacts.
**Acceptance:** B1 tests green (isolation, expiry, pagination, query-count bound, no message text in the response, consistency with `analysis`); inbox renders with one request per page; no send action exists and the enumeration test proves it; drafts always labelled "Draft - not sent"; sharing/retention controls; instruction-like messages shown as data.
**Test report:** B1 backend report, contract tests, visual, a11y, clipboard behaviour notes.
**Security/privacy:** message text only in memory and only on the conversation screen; blur/lock verified; clipboard policy.

### M8.5 - Daily Coach (8-10)
**Deliverables:** Today (plan timeline), Tasks, Habits, Goals, Calendar (agenda, add, ICS import), Reminders (+ local notification scheduling), Check-in, Evening review, Progress, Coach settings.
**Acceptance:** contract tests for all coach endpoints used; timezone correctness (user's coach timezone) tested around midnight; streak/goal states rendered from server values only; local notifications contain generic text (no titles/amounts); notification permission requested just-in-time.
**Test report:** contract, timezone, notification scheduling (unit + manual in Expo Go/dev build where available), visual, a11y.
**Security/privacy:** notification content audit; ICS file handling (memory only).

### M8.6 - Assistant ("Ask Abu") (6-8)
**Deliverables:** assistant modal, structured result cards for every workflow, idempotent submit, run history, memory-proposal approval inline, honest `thinking` orb state, all error states (503/429/504/403/409), conversation picker.
**Acceptance:** every workflow's `data` shape has a typed card and a fallback; double-tap creates one run; replay handled; 504 retry with same key; no fabricated progress text; proposals never auto-approved; mock-model chip on all agent output.
**Test report:** contract tests per workflow, state-matrix component tests, visual, a11y.
**Security/privacy:** assistant output not persisted; `reply_expired` handled.

### M8.7 - Settings, privacy and governance (4-6)
**Deliverables:** Appearance, Security, Devices & sessions, Memory manager + proposals + access log + threads, Governance (status, emergency stop), Agents, Activity (executions, audit), Data (erase), About (gates BLOCKED).
**Acceptance:** erase requires the typed confirmation and clears everything server-side (contract test) and locally; emergency stop works and resume is clearly operator-only; session revoke verified; About shows all four gates BLOCKED.
**Test report:** contract, destructive-action tests, visual, a11y.
**Security/privacy:** destructive flows reviewed; no sensitive data in Activity screens beyond what the API returns (metadata only).

### M8.8 - Native iOS (8-12) - gated by the Apple Developer decision
**8.8a (no Apple account needed): code and fakes.** Swift/config-plugin code for the Share Extension handoff, App Intents/Shortcut action and an on-device speech module (transcript only); EventKit read wrapped to produce an ICS payload; JS fakes and unit tests; handoff inbox (App Group, encrypted with a Keychain-held key) designed per the deferred-G8 architecture: **extensions never hold or rotate credentials**.
**8.8b (needs membership + physical iPhone + EAS or Mac): device verification.** Each capability verified on a device with a recorded iOS version.
**Acceptance:** 8.8a: fakes and unit tests green, build configuration validated by `expo prebuild --no-install`-style dry checks where possible, code reviewed against the capability matrix; 8.8b: each item verified on device or reported **unverified**.
**Test report:** per capability: implemented / device-verified / unverified; permission strings; privacy manifest.
**Security/privacy:** extension code reviewed for credential access (must be none); inbox encryption verified; real calendar data never used (a dedicated synthetic calendar only).

### M8.9 - Hardening and release candidate (6-8)
**Deliverables:** full accessibility audit; performance pass; security review against section C and the checklist in TESTING_STRATEGY; end-to-end flows (Maestro authored; run where a simulator/device exists); documentation; release-candidate build profile (production URL still blocked).
**Acceptance:** all overall acceptance criteria (MILESTONE_8_PLAN section 8) pass or are explicitly unverified; no open high-severity defect; final report.
**Test report:** consolidated results of all gates plus the end-to-end run.
**Security/privacy:** final threat review and dependency re-audit.

## E. Gate order and what can run in parallel

M8.0 -> M8.1 -> M8.2 are strictly sequential. M8.3, M8.4 (after B1), M8.5 may be reordered by you; M8.6 needs M8.3-M8.5 workflows available;
M8.7 can run any time after M8.1 but is scheduled late; M8.8 starts only after the Apple decision is revisited; M8.9 last.
