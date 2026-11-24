# Milestone 8 plan: the Abu AI mobile application (refined, revision 2)

**Status: PLAN ONLY, awaiting approval of this revision and of the dependency register. No application code, assets,
dependencies or backend changes exist for Milestone 8.** Nothing is installed or implemented until you approve.

Document set: this plan - [PHASE_MILESTONES](PHASE_MILESTONES.md) (independently reviewable sub-milestones, test-report
format, continuous security/privacy checks) - [DEPENDENCY_REGISTER](DEPENDENCY_REGISTER.md) -
[BACKEND_PREREQUISITES](BACKEND_PREREQUISITES.md) (G5, G9, development connectivity) -
[APP_ARCHITECTURE](APP_ARCHITECTURE.md) - [SCREEN_SPECIFICATIONS](SCREEN_SPECIFICATIONS.md) -
[DESIGN_SYSTEM_PLAN](DESIGN_SYSTEM_PLAN.md) - [IOS_CAPABILITY_MATRIX](IOS_CAPABILITY_MATRIX.md) -
[TESTING_STRATEGY](TESTING_STRATEGY.md). Visual requirements: [../design/visual-identity.md](../design/visual-identity.md).

## 0. Decisions received (recorded)

| # | Decision | Effect on this plan |
|---|---|---|
| 1 | Plan approved with conditions: keep P0-P9; split into independently reviewable milestones with deliverables, acceptance criteria and test reports; security and privacy checks throughout | PHASE_MILESTONES (sub-milestones M8.0-M8.9, each ending in a review gate with a test report); continuous checks defined there and in TESTING_STRATEGY |
| 2 | Apple Developer Program: **deferred**; finish the core app and available testing first; revisit near P8 (physical-device native extensions / TestFlight) | M8.8 is gated and may ship as "prepared, unverified"; M8.9 hardening and the release candidate do not depend on it; trigger and fallback in section 6 |
| 3 | Icons approved: original Abu branding and domain icons (Finance, Messaging, Coach); Apple SF Symbols for generic system functions | DESIGN_SYSTEM_PLAN section 6 finalised |
| 4 | Dependencies: full candidate register for approval **before** installation | DEPENDENCY_REGISTER (names, exact versions, purpose, licence, maintenance, security, alternatives) |
| 5 | G5: implement a batch/list capability before the Messaging inbox. G8: deferred (main-app handoff; extensions never hold or rotate credentials). G9: OpenAPI for authenticated or controlled development use only, never public in production | BACKEND_PREREQUISITES B1 (inbox endpoint) and B2 (controlled OpenAPI); G8 stays deferred |
| 6 | Development backend: the **private** forwarded GitHub Codespaces URL, normal authentication, no credentials in the app, no internal services exposed | BACKEND_PREREQUISITES section 3, including a real constraint that needs your decision (private forwarded ports require GitHub authentication) |

Standing restrictions (unchanged): synthetic data and the mock model only; all four production security gates stay
**BLOCKED** until independently verified; no real personal data; no production model providers; no payment execution; no
automated message sending; no weakening of existing backend security controls.

## 1. Scope and non-negotiables

- Stack (decided): React Native, Expo, TypeScript (strict), Expo Router; Swift only where iOS requires it.
- The app carries a persistent "Synthetic data" indicator and refuses production backend URLs while the gates are BLOCKED.
- Product rules enforced client-side as well as server-side: nothing pays or transfers; **no message is ever sent by the app**
  (drafts are copied for the user to send); every agent memory proposal waits for the user's approval.
- Out of scope: Android parity, App Store release, production deployment (Milestone 9), real model providers, widgets and
  Live Activities, and any backend feature other than B1 and B2 below.

## 2. What the backend offers today (inspected, M1-M7)

About 100 routes (enumerated from the live OpenAPI schema; per-screen mapping is in SCREEN_SPECIFICATIONS):

| Area | Endpoints (`/v1` unless noted; bearer token required except where marked) |
|---|---|
| Health | `GET /health` (public), `GET /health/ready` |
| Auth | `POST /auth/enroll` (public; single-use operator code), `POST /auth/refresh` (public; optional `Idempotency-Key`), `POST /auth/logout`, `GET /auth/sessions`, `DELETE /auth/sessions/{id}`, `GET /me` |
| Governance | `GET /governance/status`, `POST /governance/kill-switch` (**activate only**), `GET /executions[/{id}]`, `GET /audit`; deliberate 403s: deactivate kill switch, spending/models/providers changes, enabling agents (operator CLI only) |
| Agents | `GET /agents[/{id}]`, `POST /agents/{id}/disable` |
| Orchestrator | `POST /orchestrator/run` (optional `conversation_id`, `Idempotency-Key`), `GET /orchestrator/runs[/{id}]`, `GET /orchestrator/workflows` |
| Memory | CRUD, proposals + approve/reject, access log, threads, erase (typed confirmation) |
| Finance | accounts, transactions (manual, voice transcript, review, confirm/reject/category/delete, bulk confirm), `ingest`, `import` (CSV/XLSX/PDF base64), budgets, loans (+ schedule), subscriptions, summary, cashflow, cards, insights, upcoming, categories |
| Messaging | contacts, conversations (import, list, get, patch, delete), messages (list, append), analysis, suggestions (draft-only). **No send route exists.** |
| Coach | goals, tasks, habits (+ log), events (+ ICS import), reminders (+ `due`), check-ins, settings, plan, briefing, review, progress |

Facts the client is built around: access token 10 min; refresh token 30 days, **rotating** with reuse detection (a lost
response is retried safely with the same `Idempotency-Key`); 10/min per IP on auth routes and 300/min per user; default body
limit 64 KB (import routes larger); money is a string; two error shapes (`{"error":{...}}` and `{"detail":...}`);
the orchestrator call is synchronous (up to ~30 s, 504 on timeout); the kill switch stops agents and tools but not the
owner's own-data routes; enrollment is an operator-issued, single-use, 10-minute code.

**New finding from this review (existing backend behaviour):** Swagger UI and `openapi.json` are currently served
**unauthenticated** whenever `ABU_ENV != production` (the compose default is `development`). Through a forwarded Codespaces port
that would expose the API surface to anyone allowed to reach the port. B2 replaces this with the controlled scheme you approved.

## 3. Gap status after your decisions

| # | Gap | Status |
|---|---|---|
| G1 | No push-token registration / APNs sender | Open: local notifications only in M8 (remote push needs backend work and the paid Apple account) |
| G2 | No orchestrator progress events | Open: honest client-side "thinking" only |
| G3 | No aggregate dashboard endpoint | Open: independent queries per Home card; revisit with measurements |
| G4 | No self-service enrollment | Open (by design for a single-user system): typed/scanned operator code |
| **G5** | Messaging inbox needs per-row analysis | **Approved for implementation: backend task B1 (batch inbox endpoint) before the inbox screen** |
| G6 | No edit/delete for several records | Open: screens degrade as documented |
| G7 | No data export | Open: shown as unavailable |
| **G8** | Scoped ingest-only credential for extensions | **Deferred.** Main-app handoff architecture stays; extensions never hold or rotate credentials |
| **G9** | OpenAPI availability | **Approved: backend task B2 (controlled OpenAPI)**; never public in production |
| G10 | Lists lack cursors | Open: offset paging, capped sizes (B1 adds paging for the inbox) |
| G11 | No server-side appearance preference | Open: local preference |
| G12 | No device binding | Open: Keychain `ThisDeviceOnly` + optional biometric lock; future work |

## 4. Sub-milestones (summary; full detail in PHASE_MILESTONES)

Each sub-milestone is independently reviewable: it ends with a demo, a written **test report**, an acceptance checklist, a
security/privacy checklist result and a **stop for your approval** before the next begins.

| ID | Name | Maps to | Est. (working sessions) | Backend prerequisite |
|---|---|---|---|---|
| M8.0 | Foundations, spikes and controlled OpenAPI | P0 | 4-6 | B2 |
| M8.1 | Core shell, API client, auth and session | P1 | 6-8 | - |
| M8.2 | Design system, appearance engine, Abu presence | P2 | 8-10 | - |
| M8.3 | Finance | P3 | 10-12 | - |
| M8.4 | Messaging (includes backend B1 first) | P4 | 8-11 | B1 |
| M8.5 | Daily Coach | P5 | 8-10 | - |
| M8.6 | Assistant ("Ask Abu") | P6 | 6-8 | - |
| M8.7 | Settings, privacy and governance | P7 | 4-6 | - |
| M8.8 | Native iOS (gated by the Apple decision) | P8 | 8-12 | - |
| M8.9 | Hardening and release candidate | P9 | 6-8 | - |

Total about 68-95 working sessions including the two backend tasks (the earlier 65-90 plus about 3-5 for B1/B2). Estimates are
focused effort for one developer with AI assistance and are ranges, not commitments.

## 5. Dependencies and external requirements

- Backend reachable over HTTPS from wherever the app runs. See BACKEND_PREREQUISITES section 3 for the Codespaces reality.
- An operator to issue enrollment codes (`admin enroll`).
- Dependencies are listed in DEPENDENCY_REGISTER; **none is installed until you approve the register**, and every addition after
  approval goes through the same review.
- Expo SDK pinned at M8.0 (recommended: SDK 57, see the register for the reasoning).
- Apple Developer Program: **deferred**. Needed only for physical-device extensions and TestFlight (M8.8). Revisit trigger:
  the start of M8.8, or earlier if you want device testing sooner.
- EAS cloud builds: not needed until M8.8; development builds are needed only for custom native code.

## 6. How the Apple deferral is handled

- M8.0-M8.7 and M8.9 (non-native parts) are buildable and testable without any Apple account: Expo web, Jest, Playwright, contract
  tests and (optionally) Expo Go on a phone.
- M8.8 splits: **8.8a** write the Swift/config-plugin code and JS fakes (can be done from Linux; compiled only by EAS later);
  **8.8b** device verification, which requires the membership and a physical iPhone.
- If the membership is still deferred when M8.8 arrives, the features are delivered as **"prepared, unverified"**, excluded from the
  release candidate's acceptance, and listed as unverified in the report. Reminders still work through local notifications in a
  development build or Expo Go where permitted.
- The share-sheet / Shortcuts ingestion path remains available to the user manually in the meantime: the in-app "Alerts" paste
  screen calls the same `POST /finance/ingest`.

## 7. Risks (updated)

| Risk | Sev. | Mitigation |
|---|---|---|
| Private Codespaces ports require GitHub authentication, which a native app cannot supply without holding a GitHub credential | High | BACKEND_PREREQUISITES section 3: default to Codespaces-side testing and a fixtures "demo mode" for the phone; the runtime-token option is available only if you explicitly accept it |
| Refresh-token rotation races | High | Only the main app refreshes; single-flight refresher; extensions do not hold credentials (G8 deferred) |
| Native features untestable without macOS/device/membership | High | JS fakes + unit tests now; device verification deferred and reported as unverified |
| Supply chain (npm) | Med-High | Pinned lockfile, exact SDK-aligned versions, `npm audit` + licence allowlist in CI, install-script review (Skia has one), no analytics/crash SDKs |
| Plaintext private data cached on device | High | No persisted query cache; app-switcher blur; optional lock; storage-scan tests |
| Inbox endpoint decrypts last messages in bulk | Med | B1 bounds pages (<= 50), one query pattern, no text returned, tests for isolation/expiry |
| OpenAPI exposure | Med | B2: off by default, authenticated or offline export only, refuses to start enabled in production |
| Visual drift toward Jarvis/Apple assets | Med | Original mark and motion rules; design review gate at M8.2 |
| Home slowness (G3) | Med | Independent queries, skeletons, measured at M8.3 |
| Skia/Reanimated bundle size and complexity | Med | Budgeted at M8.2; orb has a lightweight fallback |
| Two new backend tasks risk regressions | Med | Full backend regression + new tests per task; separate review gates |
| Scope creep | Med | Gaps stay listed; new backend work needs its own approval |

## 8. Acceptance criteria for Milestone 8 (overall)

1. Every screen in SCREEN_SPECIFICATIONS exists, maps to real endpoints and handles loading, empty, error, offline and
   kill-switch states.
2. Light, Dark and System Default work, switch live without restart and meet WCAG AA contrast (automated gate).
3. Auth: enrollment, single-flight silent refresh, logout, session list/revoke, expired-session recovery; tokens only in the
   Keychain; no token in logs, state dumps, URLs or crash text.
4. No amount is computed with floats; money is displayed from strings; property tests cover formatting.
5. The app has no code path that sends a message or moves money; a test enumerates every API call.
6. Agent memory proposals are shown for approval and never auto-approved.
7. The "Synthetic data" indicator is always visible; production URLs are blocked while the gates are BLOCKED.
8. VoiceOver, Dynamic Type (largest accessibility size), Reduce Motion and Increase Contrast verified.
9. The test pyramid in TESTING_STRATEGY is green in CI; contract tests pass against the live Docker backend.
10. B1 and B2 shipped with their own tests and the full backend suite green; no existing backend control weakened.
11. Native iOS items are either verified on a physical device or explicitly listed as unverified with the reason.
12. Every sub-milestone has an approved test report and a completed security/privacy checklist.

## 9. Open items needing your decision (small)

1. **Expo SDK 57 vs 58** (register recommends 57; reasoning there).
2. **Dev connectivity** option A, B (recommended) or C (BACKEND_PREREQUISITES section 3).
3. **B1 response shape**: confirm the proposed inbox fields (BACKEND_PREREQUISITES B1), notably that `last_message_intent` is returned for
   every conversation (including ones not shared with Abu) because it is the owner's own view.
4. Approve the **dependency register** (or mark packages to swap).
