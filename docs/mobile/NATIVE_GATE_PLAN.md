# Plan: a scoped, version-pinned native transport verification (replacing the single boolean gate)

Status: **PLAN ONLY. Nothing has been changed. Waiting for your review and explicit approval.**
Branch `claude/charming-davinci-vqv0du`, base `54c743dbefacdba285d3eeec1ba0cb5997d6a682`.
Evidence: `docs/mobile/reports/REDIRECT_CANARY_EVIDENCE.md` (iPhone 17, iOS 27.2, Expo Go 58.0.5, SDK 58.0.0, `expo/fetch`, all four redirect statuses PASS, second origin received nothing).
Standing constraints are unchanged: synthetic data and the mock model only; the four production security gates remain **BLOCKED**; no real credential, production connectivity or personal data; no backend, API, migration or dependency change; no M8.3, no B3b.

## 1. Goal and non-goals
**Goal.** Replace "one boolean for every native device" with a table of *verified configurations*. Native authenticated requests (enrollment, refresh, bearer calls) are allowed **only** on a configuration that exactly matches an entry backed by device evidence. Today's evidence supports exactly one configuration: **iOS, in Expo Go, Expo SDK 58.0.0, a specific React Native runtime**.
**Not in scope (all remain blocked):** Android; any iOS build that is not Expo Go (development client, standalone/TestFlight/App Store, bare); other Expo SDKs or React Native runtimes; HTTPS to a real backend (never tested); connecting to any real backend (the operator-approved allowlist and the deferred real phone-to-backend testing are unchanged); the four production gates G1-G4; M8.3; B3b; backend changes. The Expo Go result is **not** treated as proof for release builds. The separate `dotslash` supply-chain review is a different task and is not planned here.

## 2. How the gate works today (verified by reading the code)
- `src/config/transportGate.ts`: `NATIVE_AUTH_TRANSPORT_VERIFIED = false as boolean`; `credentialsAllowed(platform, verified)` is `platform === 'web' || verified`; `NATIVE_GATE_STATUS()` feeds the About screen.
- `src/auth/runtime.ts`: `transportAllowed()` = `credentialsAllowed(platform, nativeVerified)` and an explicit `expo/fetch` transport; every request passes `send`, which rejects with `ApiError('blocked_transport')`; `nativeBlocked` drives the screens (`hooks.tsx`, `useLive.ts`, `ConnectionScreens.tsx`).
- `scripts/check-transport.mjs` fails the build unless the constant is `false`; `check:transport` also bans bypasses and `EXPO_PUBLIC_USE_RN_FETCH`.
- Tests that pin the boolean: `tests/auth/transport.test.ts` (gate tests, the `check:transport` negative fixtures that contain the literal text), `tests/auth/runtime.test.ts` (around line 474), `tests/support/harness.ts` (the `platform` and `nativeVerified` seams), `tests/components/navigation.test.tsx` (About shows five BLOCKED rows).
- The canary screen (`CanaryScreen.tsx`) reports `platform`, `osVersion`, `expoSdk`, `appOwnership` (deprecated) and the transport flags. It does **not** report the React Native runtime version or the execution environment.

## 3. What the evidence pins, and the gap
Expo Go runs the **app's JavaScript** from this repository and **native code and the React Native runtime** from the Expo Go app. The tested native side is Expo Go 58.0.5. The DevTools header in the owner's earlier screenshot read "React Native: 0.88.0" (the Expo Go runtime), while this repository's `react-native` package is 0.88.1. So the facts that matter live in two places: build-time (the JS packages) and run-time (the native runtime).
**Gap:** the canary JSON recorded SDK 58.0.0 and Expo Go ownership, but not the runtime React Native version, the execution environment or the Expo Go version, and the DevTools header is an indirect source. An exact pin should come from evidence in the same report. Step 1 of the commit sequence closes this (section 8, decision D1).

## 4. Design
### 4.1 Verification record (data, not code)
A JSON file `mobile/src/config/nativeVerifications.json` (a list; **empty today**), imported by the app and read by the CI guard:
```json
{
  "id": "ios-expo-go-sdk58-2026-11-24",
  "platform": "ios",
  "environment": "expo-go",
  "expoSdk": "58.0.0",
  "reactNativeRuntime": "0.88.0",
  "iosMajor": 27,
  "buildPins": { "expo": "58.0.26", "react-native": "0.88.1" },
  "transport": "expo/fetch, redirect manual",
  "evidence": "docs/mobile/reports/REDIRECT_CANARY_EVIDENCE.md",
  "verifiedOn": "2026-11-24"
}
```
(values for illustration; the real ones come from the re-run evidence, section 8). Keeping it as data lets the guard and the evidence document be checked against each other.

### 4.2 Runtime facts and the evaluator
- `getRuntimeFacts()` reads: `Platform.OS`; the execution environment from `Constants.executionEnvironment` (`storeClient` -> `expo-go`; every other value, including unknown, -> not verifiable); `Constants.expoConfig.sdkVersion`; the native React Native version from `Platform.constants.reactNativeVersion`; the iOS major version from `Platform.Version`.
- `evaluateNativeTransport(facts, table)` is a **pure function** returning `{ allowed, reason, entryId? }`. **Fail closed:** any missing, malformed or unexpected fact, an empty table, or any mismatch gives `allowed: false`. Web is allowed as today (browsers never follow a `redirect: "manual"` request).
- Reasons (shown to the user, never logged with data): `verified`, `no_verification` (empty table), `platform_not_verified` (Android and anything else), `environment_not_verified` (development client, standalone, bare, unknown), `sdk_mismatch`, `react_native_mismatch`, `os_major_mismatch`, `facts_unavailable`.
- `credentialsAllowed(facts?, table?)` keeps the two injection seams that the tests already use (replacing the boolean `nativeVerified`). The boolean constant `NATIVE_AUTH_TRANSPORT_VERIFIED` is **removed**, so there is no single switch to flip by accident; the table is the only way in.

### 4.3 Matrix (the behaviour the tests will pin)
| Platform | Environment | SDK | RN runtime | iOS major | Result |
|---|---|---|---|---|---|
| ios | Expo Go | = entry | = entry | = entry | **allowed** (only when an entry exists) |
| ios | Expo Go | different | any | any | blocked (`sdk_mismatch`) |
| ios | Expo Go | = entry | different (for example 0.88.1) | any | blocked (`react_native_mismatch`) |
| ios | Expo Go | = entry | = entry | different | blocked (`os_major_mismatch`) |
| ios | development client / standalone / bare / unknown | any | any | any | blocked (`environment_not_verified`) |
| android | any | any | any | n/a | blocked (`platform_not_verified`) |
| any | any | missing/null | any | any | blocked (`facts_unavailable`) |
| web | n/a | n/a | n/a | n/a | allowed (unchanged) |
| any native | any | any | any | any | blocked while the table is empty (`no_verification`) |

### 4.4 Invalidation on upgrades (automatic)
- **At build time** the CI guard `check:native-gate` compares each entry with the installed `expo` and `react-native` package versions and the Expo SDK major of `package.json`. Any difference fails the build with "verification stale: re-run the canary and update the entry". An SDK or React Native upgrade therefore cannot merge with a stale verification.
- **At run time** an Expo Go update that changes the SDK or runtime version no longer matches, so the device falls back to blocked/demo mode by itself.

### 4.5 UI
About shows the current device's status with the reason, for example "Blocked: Android is not verified", "Blocked: this Expo Go build differs from the tested one", or "Verified for iOS in Expo Go, SDK 58.0.0, React Native 0.88.0 (2026-11-24). Android, development and release builds are not verified." The connection and live-data screens use the same reason text instead of a generic banner. The About test count (five BLOCKED rows) is unchanged under Jest, because the Jest runtime never matches an entry.

## 5. The CI guard `check:native-gate` (no dependencies; wired into `check:all` and the workflow)
Fails when the table (a) has an entry whose `platform` is not `ios` or whose `environment` is not `expo-go` (new environments need their own reviewed evidence and a guard change); (b) has malformed fields, duplicate ids or an invalid or future `verifiedOn`; (c) references an evidence file that does not exist or does not contain the entry `id` and the pinned values; (d) is stale against the installed `expo` and `react-native` versions or the SDK major; (e) is non-empty while the evidence document lacks a PASS for all of 301, 302, 307 and 308. `check:transport` keeps its bypass bans; its "constant must be false" rule is replaced by "the boolean constant must not exist". **Negative tests** (fixtures, as for `check:overrides`) cover each failure above plus a passing control.

## 6. Tests (explicit, before any entry exists)
1. Evaluator matrix: every row of 4.3, at least 25 cases, including all `null`/`undefined`/garbage facts, version strings with prerelease tags, `Platform.Version` as a number and as a string.
2. Runtime tests: with each unsupported facts set, enrollment, refresh and authenticated requests reject with `blocked_transport` **and no request reaches the network**; with a supported set (test table) they pass the existing http controls (origin allowlist, `redirect: "manual"`, redirect discard).
3. `getRuntimeFacts()` with mocked `Platform` and `Constants` for Expo Go, development client, standalone and bare.
4. UI tests: About and Connection show the right reason for each state; demo mode still makes zero network calls.
5. Phase invariant: **while the table is empty, every native fact set is blocked.** The commit that adds the entry changes exactly this test, which makes the opening an explicit, reviewable line.
6. Guard negative tests (section 5).

## 7. Documentation and auditability
`docs/mobile/REDIRECT_CANARY.md` gets the re-verification procedure (when to re-run, how to update the entry); `reports/REDIRECT_CANARY_EVIDENCE.md` gets the re-run evidence with the new fields; `DEPENDENCY_AUDIT.md` and the SDK 58 report cross-reference the table; the plan for each change is this document. The entry, its evidence file, its supported configuration and its limitations (section 1 and the evidence document, section 5) are in one place.

## 8. Commit sequence (separate, reviewable; the gate stays **closed** until the last commit)
| # | Commit | Opens the gate? |
|---|---|---|
| 1 | `feat(mobile): canary report records runtime facts`: adds `reactNativeRuntime`, `executionEnvironment` and the Expo Go version (`Constants.expoVersion`) to the canary's evidence JSON, with tests. No gate change. | No |
| - | **Your step:** re-run the iPhone canary with the same Expo Go 58.0.5 so the evidence contains the pins; I record it. About 10 minutes, same procedure. | No |
| 2 | `refactor(mobile): per-configuration native transport gate`: runtime facts, evaluator, `nativeVerifications.json` = `[]`, runtime seams, UI reasons, tests of section 6, `check:transport` update. **Every native configuration stays blocked.** | No |
| 3 | `ci(mobile): add check:native-gate with negative tests`, wired into `check:all` and the workflow. | No |
| 4 | **Only after your separate explicit approval:** `feat(mobile): verify native transport for iOS in Expo Go SDK 58`: **one JSON entry**, the flipped phase-invariant test, evidence and docs. Nothing else. | **Yes, for that one configuration** |
After each commit: `check:all`, a clean-checkout `npm ci`, the real GitHub Actions run, contract and E2E (unaffected, demo mode), no dependency or lockfile change. After commit 4 you can confirm on the iPhone that About reads "Verified for ..."; nothing connects to any backend.

## 9. Risks and limits
1. **Run-time facts are not tamper-proof.** They are read by the same JavaScript they protect, so the gate guards against mistakes (wrong build, wrong platform, silent upgrade), not against a compromised app.
2. **Strict matching has a cost:** an Expo Go update that changes the SDK or runtime blocks authentication until the canary is re-run (about 10 minutes) and the entry updated. That is the intent.
3. **Pins on `expo` and `react-native` packages** mean even a patch bump fails CI until the entry is updated after a re-run; it is deliberate friction, not a defect.
4. **iOS version:** the evidence is one iOS version (27.2). Pinning the major only (decision D2) allows minor iOS updates without re-running; an iOS major update blocks.
5. **Not tested and not claimed:** HTTPS, release builds, Android, hostile servers that redirect later, other devices.
6. **The runtime facts API must work in Expo Go SDK 58** (`Constants.executionEnvironment`, `Platform.constants.reactNativeVersion`). Commit 1 and the re-run verify it on the device before anything depends on it; if a value is unreadable, the plan stops and comes back to you.

## 10. Decisions I need from you
- **D1 (recommended: yes):** approve commit 1 and a **re-run of the iPhone canary** to capture the runtime pins. The alternative, using the DevTools header screenshot as the source for "0.88.0", is indirect and not recommended.
- **D2:** pin the iOS **major** only (recommended) or the exact iOS version (27.2).
- **D3:** pin exact `expo` and `react-native` package versions in CI (recommended, as they are already exact in `package.json`) or only the SDK major.
- **D4:** confirm the scope: iOS and Expo Go only; development client, standalone and Android stay blocked until each has its own evidence.
- **D5:** an expiry on verifications (for example CI warns after 180 days) or none. I recommend a CI **warning**, not a failure.
- **D6:** confirm the staged order above, in particular that the entry is a **separate final commit** that needs your explicit approval.
