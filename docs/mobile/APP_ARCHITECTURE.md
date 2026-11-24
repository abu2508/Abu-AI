# Abu AI mobile: application architecture (plan)

Planning document. Nothing here is implemented or installed.

## 1. Principles

1. **The backend is the source of truth and the policy engine.** The app is a thin, careful client: it renders what the
   backend computed, never recomputes finance, never decides permissions, never sends messages.
2. **No private data at rest on the device** beyond the Keychain secrets. No persisted query cache, no on-disk copies of
   imported files, no analytics or crash SDKs.
3. **Fail closed and visible.** Kill switch, spend limits, expired sessions and parse errors are first-class UI states.
4. **Logic in TypeScript, native only when iOS forces it** (Share Extension, App Intents, Keychain access group).
5. **One design system**, token-driven, theme-aware, tested in both appearances.

## 2. Repository layout

A new top-level `mobile/` directory (the Python backend is untouched):

```
mobile/
  app/                         Expo Router routes (file-based; thin: they render feature screens)
    _layout.tsx                root: providers (theme, query, session), splash gate, lock gate
    (onboarding)/              welcome, enroll, security-setup, appearance
    (tabs)/                    _layout.tsx (tab bar + Abu orb)
      home/                    index
      finance/                 index, transactions/, review, import, ingest, budgets, loans/, subscriptions, accounts/
      messages/                index, [id], import, contacts/
      coach/                   index (today), tasks, habits, goals, calendar, reminders, checkin, review
    assistant.tsx              modal: "Ask Abu"
    settings/                  index, appearance, security, sessions, memory/, governance, agents, activity/, data, about
  src/
    api/
      client.ts                fetch wrapper: base URL allowlist, auth header, timeouts, retry policy, error mapping
      errors.ts                ApiError (code, status, message, retryable, kind), parses both error shapes
      schemas/                 zod schemas per endpoint group (runtime validation of every response)
      endpoints/               finance.ts, messaging.ts, coach.ts, orchestrator.ts, memory.ts, auth.ts, governance.ts
      queryKeys.ts
    auth/
      sessionManager.ts        single-flight refresh, rotation + idempotency key, state machine
      secureStore.ts           Keychain wrapper (ThisDeviceOnly), typed keys
      lock.ts                  biometric/app lock + background blur
    design/                    tokens/, themes/, ThemeProvider, typography, motion, icons/, components/, charts/, orb/
    features/<domain>/         screens, components, hooks (useXxxQuery/useXxxMutation), view-models
    lib/                       money.ts (string/BigInt paise, formatting only), dates.ts, base64.ts, clipboard.ts, logger.ts (redacting)
    native/                    TS bridges + JS fakes: shareInbox, appIntents, speech (interfaces; Swift behind them)
    config/                    env.ts (build profile), backendAllowlist.ts, featureFlags.ts (gates, synthetic banner)
    testing/                   synthetic fixtures, msw handlers, render helpers, a11y helpers
  modules/                     Expo local native modules (Swift): added in P8 only
  targets/                     Apple extension targets via config plugin (Share Extension, App Intents): P8 only
  e2e/                         Maestro flows
  scripts/                     contrast-check, token export, (later) openapi snapshot -> types
  app.config.ts  eas.json  tsconfig.json  eslint.config  jest.config
```

Rules: routes contain no business logic; features never import from each other (shared code goes to `lib/`,
`design/` or `api/`); only `src/api` calls `fetch`; only `src/auth` touches secrets; only `src/native` touches native modules.

## 3. Candidate dependencies (none installed; each reviewed at P0 for licence, maintenance and permissions)

| Concern | Candidate | Why / note |
|---|---|---|
| Framework | `expo`, `expo-router`, `react-native` (SDK pinned at P0) | decided |
| Language | TypeScript strict + `noUncheckedIndexedAccess` | decided |
| Server state | `@tanstack/react-query` (in-memory only, no persister) | caching, retries, invalidation |
| UI state | `zustand` | tiny; appearance, lock, ephemeral UI |
| Validation | `zod` | runtime-validate every response; types inferred |
| Secrets | `expo-secure-store` (Keychain) | refresh token; `WHEN_UNLOCKED_THIS_DEVICE_ONLY` |
| Biometrics | `expo-local-authentication` | optional app lock |
| Animation | `react-native-reanimated`, `react-native-gesture-handler` | orb, transitions (worklets) |
| Graphics/charts | `@shopify/react-native-skia` | orb glow, area/ring/bars; accessible alternatives separate |
| Blur/material | `expo-blur` | tab bar, sheets, background privacy blur |
| Haptics | `expo-haptics` | restrained feedback |
| Files | `expo-document-picker` (+ `expo-file-system` read only) | statement/ICS/chat import, no persistence |
| Camera (optional) | `expo-camera` | scan the enrollment QR |
| Notifications | `expo-notifications` | local reminders (G1: no remote push) |
| Calendar | `expo-calendar` | EventKit read (P8, device-tested) |
| Speech | native module over `SFSpeechRecognizer` on-device (P8) | transcript only is sent |
| Testing | `jest-expo`, `@testing-library/react-native`, `msw`, Maestro, Playwright (web gallery) | see TESTING_STRATEGY |
| Lint | `eslint` + `eslint-plugin-react-native-a11y`, `typescript-eslint` | a11y rules in CI |

Explicitly **not** included: analytics, ads, crash-reporting SDKs, social login, any AI SDK, any push vendor.

## 4. Navigation structure (Expo Router)

```
Root stack
 |- (onboarding)  [shown until a valid session exists]
 |    welcome -> enroll (code + device name) -> security-setup (app lock) -> appearance -> done
 |- (tabs)        [authenticated]
 |    Home | Finance | Messages | Coach          + persistent Abu orb (centre, opens assistant modal)
 |- assistant     modal sheet (Ask Abu)
 |- settings/*    pushed from Home avatar (not a tab)
 |- lock          full-screen gate when locked
```

- Tab bar: four domain tabs plus the Abu orb, translucent material, domain accent on the active tab.
- Deep links: scheme `abuai://` with an allowlist (`abuai://ask`, `abuai://finance/review`, `abuai://messages/import`, ...). Unknown paths are ignored. No deep link performs a mutation.
- Back-swipe and large titles follow platform conventions; modals for create/edit flows, pushes for drill-down.

## 5. State and data flow

```
Screen -> hook (useQuery/useMutation) -> src/api/endpoints/* -> client.ts -> backend
                                              |                    ^
                                          zod parse            sessionManager (token)
```
- **Query defaults:** `gcTime` short, no persistence, `staleTime` per data type (finance 60 s, reminders 15 s), refetch on foreground.
- **Mutations** invalidate precise keys (e.g. confirming a transaction invalidates review, summary, insights, budgets).
- **Idempotency:** orchestrator runs and any retried mutation that the backend supports send a generated `Idempotency-Key`
  (persisted in memory until the request settles). Refresh uses its own key (see 6).
- **Optimistic updates** only for reversible UI-only changes (checking a task); never for finance writes.
- **Offline:** read screens show an offline banner and in-memory stale data if present; writes are blocked with a clear
  message (no offline write queue in M8: it would require on-device persistence of private data).
- **Money:** strings in, strings out. `lib/money.ts` formats using `Intl.NumberFormat` on a string-derived integer paise
  (BigInt); there is no `parseFloat` on amounts (lint rule + tests).
- **Time:** the backend returns ISO timestamps; the user's coach timezone (`/coach/settings`) is used for "today".

## 6. Authentication and session management

State machine: `unenrolled -> enrolling -> active <-> refreshing -> expired -> (re-enroll)`, plus `locked` (UI only).

- **Enroll:** `POST /auth/enroll` {code, device name, `platform: "ios"`} -> access token (kept in memory), refresh token
  (Keychain), `session_id`.
- **Access token:** memory only; used until ~30 s before `expires_in` (10 min), then refreshed.
- **Refresh (single-flight):** one in-flight refresh shared by all callers. Before sending, the manager generates an
  `Idempotency-Key`, persists *(old refresh token, key)* in the Keychain item, then calls `/auth/refresh`. If the
  response is lost (timeout/crash) the next attempt reuses the **same token and key**, which the backend accepts once
  without tripping reuse detection. On success the new refresh token replaces the old atomically. On `401` from refresh
  the session is `expired`: wipe Keychain, return to enrollment.
- **401 on an API call:** one refresh attempt, one retry; a second 401 ends the session.
- **Extensions never refresh** (rotation race): see IOS_CAPABILITY_MATRIX and gap G8.
- **Logout:** `POST /auth/logout`, then wipe secrets; "sign out other devices" uses `DELETE /auth/sessions/{id}`.
- **Storage:** Keychain `WHEN_UNLOCKED_THIS_DEVICE_ONLY`; nothing synced to iCloud; no refresh token in AsyncStorage,
  logs, deep links or route params.
- **App lock (optional):** biometric/passcode gate on launch and after N minutes in background; privacy blur in the app
  switcher always.
- **Transport:** HTTPS only in release builds (ATS default); the base URL must match `backendAllowlist` (build-profile
  controlled); debug dev-client may allow a local-network HTTP URL. No TLS pinning in M8 (certificate rotation is a
  Milestone 9 concern; revisit then).
- **Rate-limit and error handling:** 429 honours `Retry-After`; 5xx retried once for idempotent GETs; mutations are not
  blindly retried.

## 7. Error model

`ApiError { kind, status, code, message, retryable }` with kinds: `auth_expired`, `kill_switch`, `spending_blocked`,
`permission_denied`, `validation`, `conflict` (409), `not_found`, `rate_limited`, `timeout`, `server`, `network`.
Parses both `{error:{code,message}}` and `{detail}`. Screens show kind-specific copy from a single table (no raw server
text for 5xx). Messages never include user content.

## 8. Mapping of the native extension boundary

`src/native/*` exposes TypeScript interfaces (`ShareInbox.takePending()`, `AppIntents.registerHandlers()`,
`Speech.transcribeOnDevice()`), each with a pure-JS fake used by tests and by Expo Go/web. The Swift side (P8) only:
receives text or a transcript, stores it in an **App Group inbox encrypted with a Keychain-held key**, and signals the
main app, which performs the authenticated API call. This keeps tokens out of extensions and keeps the extension code tiny.

## 9. Configuration and gates

- Build profiles: `dev` (dev client, synthetic backend URL), `preview` (internal), `production` (blocked in M8).
- `featureFlags.productionGatesVerified = false` is compiled in; while false, a persistent **"Synthetic data only"** chip
  shows in the header and Settings > About lists the four gates as BLOCKED; production backend URLs are rejected.
- No secrets in the bundle: the app contains only a base URL. All credentials are per-device tokens issued at enrollment.

## 10. Observability (privacy-preserving)

A local redacting logger (levels, no bodies, no tokens, no amounts, no message text; a unit test feeds secrets through
it). No remote logging. A debug-only "diagnostics" screen shows request status counts and the last error kinds.
