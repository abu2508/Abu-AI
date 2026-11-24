# Milestone 8 plan: the Abu AI mobile application

**Status: PLAN ONLY. No application code, assets, dependencies or backend changes exist for Milestone 8.**
Implementation starts only after explicit approval of this plan (and of the open decisions in section 9).

Companion documents: [APP_ARCHITECTURE](APP_ARCHITECTURE.md), [SCREEN_SPECIFICATIONS](SCREEN_SPECIFICATIONS.md),
[DESIGN_SYSTEM_PLAN](DESIGN_SYSTEM_PLAN.md), [IOS_CAPABILITY_MATRIX](IOS_CAPABILITY_MATRIX.md),
[TESTING_STRATEGY](TESTING_STRATEGY.md). Visual requirements: [../design/visual-identity.md](../design/visual-identity.md).

## 1. Scope and non-negotiables

- Stack (decided): React Native, Expo, TypeScript (strict), Expo Router; Swift only where iOS requires it.
- **Synthetic data and the mock model only.** No real finance, messaging, calendar or personal data. No external
  model provider. No backend security control is weakened.
- **The four production gates stay BLOCKED** until independently verified: (1) disk/volume encryption,
  (2) independent keyring backup + rehearsed recovery, (3) plaintext checkpoint metadata, (4) production embedder.
  The app carries a persistent "synthetic data only" indicator and refuses production backend URLs until the
  operator flips a build flag after verification.
- Hard product rules carried from the backend: nothing pays or transfers; **no message is ever sent by the app**
  (drafts are copied for the user to send); every agent memory proposal waits for the user's approval.
- Out of scope for M8: Android parity work, App Store release, production deployment/Caddy (Milestone 9), real
  model providers, any new backend feature (see section 4: gaps are *listed*, not built).

## 2. What the backend actually offers (inspected, M1-M7)

All routes below exist today (enumerated from the live OpenAPI schema; see SCREEN_SPECIFICATIONS for per-screen mapping).

| Area | Endpoints (all `/v1` unless noted; all need a bearer token except where marked) |
|---|---|
| Health | `GET /health` (public liveness), `GET /health/ready` |
| Auth | `POST /auth/enroll` (public; single-use operator code), `POST /auth/refresh` (public; optional `Idempotency-Key`), `POST /auth/logout`, `GET /auth/sessions`, `DELETE /auth/sessions/{id}`, `GET /me` |
| Governance | `GET /governance/status` (kill switch + spending), `POST /governance/kill-switch` (**activate only**), `GET /executions`, `GET /executions/{id}`, `GET /audit`; `DELETE kill-switch`, `PUT spending/models/providers`, `POST agents/{id}/enable` are deliberate **403s** (operator CLI only) |
| Agents | `GET /agents`, `GET /agents/{id}`, `POST /agents/{id}/disable` |
| Orchestrator | `POST /orchestrator/run` (optional `conversation_id`, `Idempotency-Key`), `GET /orchestrator/runs`, `GET /orchestrator/runs/{id}`, `GET /orchestrator/workflows` |
| Memory | list/create/get/update/delete, `GET /memory/proposals`, `POST /memory/{id}/approve|reject`, `GET /memory/access-log`, threads list/delete, `POST /memory/erase` (typed confirmation) |
| Finance | accounts (create/list/patch), transactions (manual, voice transcript, list, confirm/reject/category/delete, `confirm-pending`), `ingest` (SMS/email/shortcut/share text), `import` (CSV/XLSX/PDF as base64, <= ~3 MB), review queue, budgets (put/list/delete), loans (+ schedule), subscriptions (+ pause/cancel/resume), summary, cashflow, cards, insights, upcoming, categories |
| Messaging | contacts CRUD, conversations (import, list, get, patch, delete), messages (list, append), analysis, suggestions (draft-only). **No send route exists.** |
| Coach | goals, tasks (+ complete/snooze), habits (+ log), events (+ `calendar/import` ICS), reminders (+ `due`, ack/dismiss), check-ins, settings (timezone, work hours), plan, briefing, review, progress, generic delete |

Facts the client must be built around:
- Access token TTL **10 min**; refresh token **30 days**, **rotating**, reuse detection kills the session; session
  max 90 days. The refresh endpoint accepts an `Idempotency-Key` so a lost response can be retried with the *same*
  token safely.
- Rate limits: 10/min per IP on auth routes, 300/min per user. Default body limit 64 KB; import routes larger.
- Money is a **string**; the client must never use floats for amounts.
- Errors come in two shapes: `{"error":{"code","message"}}` (orchestrator, finance, messaging, coach) and FastAPI
  `{"detail": ...}` (auth, memory, governance, agents). Both must be parsed.
- The orchestrator call is **synchronous** (seconds; up to ~30 s workflow timeout, 504 on timeout).
- While the kill switch is on, agent runs return 503 but the user's own data routes keep working.
- Enrollment is an **operator-issued, single-use, 10-minute code** (CLI). There is no self-service signup.

## 3. Mapping principle

Every screen and feature is mapped to an existing endpoint in SCREEN_SPECIFICATIONS. Where nothing exists the
screen is marked **GAP** and is either degraded to what the backend supports or deferred. Nothing is invented.

## 4. Missing APIs (listed, not built; each needs a separate approved backend task)

| # | Gap | Effect on the app | M8 handling |
|---|---|---|---|
| G1 | No push-token registration, no APNs sender | No remote push | Local notifications scheduled from polled `/coach/reminders/due`; foreground/background-fetch only |
| G2 | No streaming or progress events for `/orchestrator/run` | Cannot show real step-by-step progress | "Thinking" states are client-side, driven by the request lifecycle; no fake step labels |
| G3 | No aggregate dashboard endpoint | Home needs ~6 parallel calls | Parallel queries with per-card skeletons and failure isolation; revisit if slow |
| G4 | No self-service enrollment or QR generation | First run needs the operator's CLI code | Enrollment screen takes a typed or scanned code (QR content = the code string) |
| G5 | No "needs reply" summary on `GET /messaging/conversations` | Inbox badge needs one `analysis` call per conversation | Lazy per-visible-row analysis with a cache; cap concurrency |
| G6 | No transaction edit (amount/date/description), no account delete, no loan or subscription edit/delete, no event update, no reminder delete | Edit flows limited | Show delete-and-recreate where the backend allows; mark others read-only |
| G7 | No data-export endpoint | "Export my data" cannot ship | Not offered in M8 (listed in Settings as unavailable) |
| G8 | No narrowly scoped ingest-only device credential | Share Extension / Shortcut would need a full-power token | See IOS_CAPABILITY_MATRIX: extensions do **not** hold the refresh token; hand off to the main app until a scoped credential is approved |
| G9 | OpenAPI is not served outside development | Typed client generation | Commit a snapshot generated from the dev app in a *future approved* tooling step; until then hand-written zod schemas validated by contract tests |
| G10 | Lists lack cursors (limit/offset only; some none) | Long histories need paging | Offset paging where available; cap list sizes |
| G11 | No server-side appearance/profile preferences | Appearance override is device-local | Store override locally (non-sensitive) |
| G12 | No app-attest / device-binding | Refresh token is a bearer secret | Mitigate on device: Keychain `ThisDeviceOnly`, optional biometric app lock; track as future work |

## 5. Implementation phases (estimates are working sessions of focused effort, one developer with AI assistance;
## ranges, not commitments)

| Phase | Content | Est. |
|---|---|---|
| P0 Foundations | `mobile/` scaffold, strict TS, lint, jest, CI, token/contrast scripts, env + backend-URL allowlist, synthetic fixtures | 3-4 |
| P1 Core shell | API client, error model, auth/session manager, secure storage, Expo Router skeleton, theme provider (live scheme switching), onboarding + enrollment + lock | 6-8 |
| P2 Design system | Tokens, components, Abu orb + motion, charts primitives, web gallery with visual regression | 8-10 |
| P3 Finance | Overview, transactions, review queue, import, ingest paste, budgets, loans, subscriptions, accounts | 10-12 |
| P4 Messaging | Inbox, conversation, import, analysis, reply drafts (copy only), contacts | 6-8 |
| P5 Coach | Today, tasks, habits, goals, calendar (ICS), reminders (+ local notifications), check-ins, review | 8-10 |
| P6 Assistant | "Ask Abu" surface, structured result cards, runs history, idempotency, memory proposal approval | 6-8 |
| P7 Settings, privacy, governance | Appearance, sessions, kill switch, agents, executions/audit, memory manager, erase | 4-6 |
| P8 Native iOS | Share Extension handoff, App Intents/Shortcuts, EventKit read, speech, notification polish (needs macOS/EAS) | 8-12 |
| P9 Hardening | Accessibility audit, performance, security review, E2E on device/simulator, release candidate | 6-8 |

Total roughly 65-90 working sessions. P0-P7 are largely doable without a Mac; P8-P9 need macOS or EAS cloud builds plus a device.
Each phase ends with a demo build, tests green and a stop-and-report, matching the project's milestone rule.

## 6. Dependencies

- Backend reachable over **HTTPS** from the phone (dev: a forwarded HTTPS URL; production: Milestone 9). Local-network
  HTTP is allowed only in debug dev-client builds.
- An operator to issue enrollment codes (`admin enroll`).
- EAS cloud builds (or a Mac with Xcode) for any native work; a physical iPhone for Share Extension, App Intents and
  EventKit verification.
- **Apple Developer Program (paid, yearly)** for device installs of extension-bearing builds and TestFlight. This is
  a new cost **outside** the AI-spend budget and needs explicit approval. Simulator-only work needs a Mac, not the programme.
- Expo SDK version chosen and pinned at P0 after compatibility review (not decided here).
- Dependencies are *candidates* listed in APP_ARCHITECTURE; none is installed until the plan is approved, and each
  new dependency is reviewed for maintenance, licence and permissions.

## 7. Risks

| Risk | Severity | Mitigation |
|---|---|---|
| Refresh-token rotation races between app and extensions kill the session | High | Only the main app refreshes; one single-flight refresher; extensions use short-lived access token or hand off; see G8 |
| Share Extension/App Intents untestable without macOS/device | High | Isolate behind a thin native bridge with JS fakes; schedule P8 on EAS + device; keep logic in JS |
| Expo Go cannot run custom native code | Medium | Use development builds (EAS) from P8; P0-P7 run in Expo Go/web where possible |
| iOS gives no access to other apps' notifications | Medium | Shortcuts "message received" automations and the share sheet only; stated plainly in onboarding |
| Pure-JS PDF/Excel from user files | Medium | Files are read in memory, base64'd and sent; parsing is server-side with limits; never persisted on device |
| Visual identity drifts toward Jarvis/Apple assets | Medium | Original mark and motion rules in DESIGN_SYSTEM_PLAN; design review gate in P2 |
| Home screen slowness from many parallel calls (G3) | Medium | Independent card queries, skeletons, stale-while-revalidate in memory only |
| Plaintext private data cached on device | High | No persisted query cache; app-switcher blur; optional lock; tests assert nothing sensitive hits storage |
| Dependency supply chain | Medium | Pinned lockfile, `npm audit`, minimal SDK set, no analytics/crash SDKs without approval |
| Apple fee / account blocks device testing | Medium | Approval request above; fall back to simulator-only on a Mac |
| Scope creep into new backend work | Medium | Gaps G1-G12 are listed and deferred; backend changes need their own approval |

## 8. Acceptance criteria for Milestone 8 (overall)

1. Every screen in SCREEN_SPECIFICATIONS exists, maps to real endpoints, and handles loading, empty, error, offline and
   kill-switch states.
2. Light, Dark and System Default work, switch live (no restart), and meet WCAG AA contrast (automated check).
3. Auth: enrolment, silent refresh (single-flight), logout, session list/revoke, session-expired recovery; tokens only
   in the Keychain; no token in logs, state dumps or crash text.
4. No amount is ever computed with floats; all money shown from strings; property tests cover formatting.
5. The app can never send a message or move money (no code path; a test enumerates API calls).
6. Memory proposals from agents are shown for approval and never auto-approved.
7. The persistent "synthetic data only" indicator is present; production URLs are blocked while gates are BLOCKED.
8. VoiceOver, Dynamic Type up to the largest accessibility size, Reduce Motion and Increase Contrast verified.
9. Test pyramid in TESTING_STRATEGY is green in CI; contract tests pass against the live Docker backend.
10. Native iOS features (P8) verified on a physical device or flagged as unverified with the reason.
11. No backend change; the backend regression suite is untouched and still passes.

## 9. Decisions needed before implementation

1. Approve the plan and phases (or reorder them).
2. Approve the Apple Developer Program cost and EAS usage (or choose simulator-only/Mac).
3. Approve the icon strategy: original Abu icon set for brand and domain glyphs, Apple SF Symbols only for generic
   system glyphs (see DESIGN_SYSTEM_PLAN section 6).
4. Approve the dependency candidates list (APP_ARCHITECTURE section 8) or request alternatives.
5. Decide whether the small backend gaps worth closing early (G5 needs-reply flag, G8 scoped ingest credential,
   G9 OpenAPI snapshot) become a separate, approved backend task before P3/P8.
6. Confirm the development backend reachability approach (HTTPS forwarded URL during development).
