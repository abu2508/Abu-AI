# Plan (revision 3): record verified redirect behaviour per configuration, without authorising native authenticated requests

Implementation status (2026-11-24, after review of the rerun evidence `998e29f`): **commits 2 and 3 are implemented** (registry empty, boolean `false`, guard `check:native-gate`, checklist `NATIVE_AUTH_AUTHORISATION.md`); `expoGoVersion` was renamed `expoGoBuild` and a `null` `expoGoMarketingVersion` was added, as approved. Commit 4 and any change of the boolean are NOT approved. Original status line follows.

Status: **Revision 3 records your decisions E1-E4 and clarifications. Commit 1 is done and accepted. Commits 2 and 3 are approved but MUST NOT START until the iPhone canary re-run evidence has been reviewed. Commit 4 is conditionally approved (separate approval needed). Changing `NATIVE_AUTH_TRANSPORT_VERIFIED` is NOT approved.**
Branch `claude/charming-davinci-vqv0du`. Revision 1 was commit `c3cbd2232797398a96aef87f31c4c6d995ae760d`, revision 2 `f6e4c66eaaa40229b2f85bf9e93d401e3487d8c0`.
Standing constraints are unchanged: synthetic data and the mock model only; the four production security gates remain **BLOCKED**; no real credential, production connectivity or personal data; no backend, API, migration or dependency change; no M8.3, no B3b.

## 0. What changed in this revision, and why
Your correction: the iPhone canary shows that `expo/fetch` did not forward requests across four redirects. It does **not** show that the whole native authenticated transport is production-safe. Revision 1 was wrong to propose removing `NATIVE_AUTH_TRANSPORT_VERIFIED` and allowing enrollment, refresh and bearer requests whenever a configuration matched. In this revision:
1. There are **two independent things**: (A) a *redirect-verification registry* (evidence about one behaviour), and (B) *native authentication authorisation*, which stays the boolean `NATIVE_AUTH_TRANSPORT_VERIFIED = false`.
2. **(A) never produces (B).** Native credentialed requests are allowed only when **both** hold: the boolean is `true` **and** the configuration is redirect-verified. With the boolean `false`, every native configuration stays blocked, whatever the registry contains.
3. Flipping (B) needs its own list of evidence (section 6) and its own explicit approval, **outside** this plan. Nothing here flips it.
4. The registry entry (commit 4) is data about redirects only, and a test proves it cannot authorise anything.

## 1. Your decisions (recorded)
| | Decision |
|---|---|
| D1 | Approved: runtime facts in the canary evidence (**commit 1, done: `e4f633113a107a06d3a9c7adcc3a8c926f61689d`**) and an iPhone canary re-run with the same Expo Go 58.0.5 |
| D2 | iOS **major** version only |
| D3 | CI pins the **exact** `expo` and `react-native` package versions |
| D4 | iOS + Expo Go only; Android, development clients, standalone, TestFlight and App Store builds stay unsupported by this verification |
| D5 | CI **warning** after 180 days, not a failure |
| D6 | Staged commits; the final commit needs a separate approval |
| Correction | Redirect verification must not automatically authorise native authenticated requests; `NATIVE_AUTH_TRANSPORT_VERIFIED` stays `false` until the remaining requirements have evidence and approval |
**Approval boundary (your review of revision 2):**
| Item | Decision |
|---|---|
| Commit 1 (runtime facts in the canary evidence) | **Accepted** (`e4f6331`, GitHub Actions run 45454501357 passed) |
| E1: two independent mechanisms (redirect registry, boolean authorisation) | **Approved** |
| E2: requirements | **Approved with additions:** R1-R7 plus R8-R11 (section 6) |
| E3: About wording | **Approved with revised wording** (section 5.6); the Connection screen must reflect the same distinction |
| E4: commit 4 separately reviewed; boolean excluded | **Approved** |
| Commit 2 (evaluator, empty registry) and commit 3 (guard, checklist R1-R11) | **Approved, but not to be started until the new iPhone canary evidence (React Native runtime, execution environment, Expo Go version) has been reviewed**, because that evidence is the input to the registry design |
| Commit 4 (one registry entry) | **Conditional:** prepare only after commits 2 and 3 are implemented and reviewed and the new evidence is reviewed; needs your separate approval before the entry is added |
| Changing `NATIVE_AUTH_TRANSPORT_VERIFIED` | **Not approved;** needs a separate plan and explicit approval |

## 2. Goal and non-goals
**Goal.** Record, per exact native configuration, that the redirect canary passed, and make that record machine-checkable, version-pinned and self-invalidating on upgrades. Today's evidence supports one configuration: **iOS, in Expo Go, Expo SDK 58.0.0, one React Native runtime**.
**Not in scope (all remain blocked):** authorising any native authenticated request; Android; any build that is not Expo Go; other SDKs or runtimes; HTTPS to a real backend; the complete enrollment and refresh flows on a device; release-build behaviour; connecting to any real backend (the allowlist and the deferred real phone-to-backend testing are unchanged); the four production gates G1-G4; M8.3; B3b; backend changes. The separate `dotslash` supply-chain review is a different task.

## 3. How the gate works today (verified by reading the code)
- `src/config/transportGate.ts`: `NATIVE_AUTH_TRANSPORT_VERIFIED = false as boolean`; `credentialsAllowed(platform, verified)` = `platform === 'web' || verified`; `NATIVE_GATE_STATUS()` feeds the About screen.
- `src/auth/runtime.ts`: `transportAllowed()` uses `credentialsAllowed` and an explicit `expo/fetch` transport; every request passes `send`, which rejects with `ApiError('blocked_transport')`; `nativeBlocked` drives the screens (`hooks.tsx`, `useLive.ts`, `ConnectionScreens.tsx`).
- `scripts/check-transport.mjs` fails unless the constant is `false` and bans bypasses and `EXPO_PUBLIC_USE_RN_FETCH`.
- Tests that pin the boolean: `tests/auth/transport.test.ts`, `tests/auth/runtime.test.ts`, `tests/support/harness.ts` (`platform`/`nativeVerified` seams), `tests/components/navigation.test.tsx` (About shows five BLOCKED rows).
**All of this stays.** The boolean, its `check:transport` rule and its tests are kept as they are; the new work is added **next to** them.

## 4. What the evidence pins
Expo Go runs the **app JavaScript** from this repository and the **native runtime** from the Expo Go app (tested: 58.0.5, React Native runtime 0.88.0 per the DevTools header; this repository's `react-native` package is 0.88.1). Commit 1 made the canary record `reactNativeRuntime`, `executionEnvironment` and `expoGoVersion` (renamed `expoGoBuild` in commit 2); the re-run (section 8, step 2) supplies the exact values in the same report, so no pin comes from a screenshot.

## 5. Design
### 5.1 Redirect-verification registry (data only)
`mobile/src/config/redirectVerifications.json` (a list, **empty until commit 4**):
```json
{
  "id": "ios-expo-go-sdk58-2026-11-24",
  "scope": "redirect-behaviour-only",
  "platform": "ios",
  "environment": "expo-go",
  "expoSdk": "58.0.0",
  "reactNativeRuntime": "<from the re-run evidence>",
  "iosMajor": 27,
  "buildPins": { "expo": "58.0.26", "react-native": "0.88.1" },
  "canary": "301, 302, 307, 308: PASS, second origin received nothing",
  "evidence": "docs/mobile/reports/REDIRECT_CANARY_EVIDENCE.md",
  "verifiedOn": "2026-11-24"
}
```
The file name, the `scope` field and every function name say "redirect"; nothing in it mentions authentication.

### 5.2 Runtime facts and the redirect evaluator
- `getRuntimeFacts()` reads `Platform.OS`, `Constants.executionEnvironment` (`storeClient` is Expo Go; every other value, including unknown, is not verifiable), `Constants.expoConfig.sdkVersion`, `Platform.constants.reactNativeVersion` and the iOS major from `Platform.Version`.
- `evaluateRedirectVerification(facts, table)` is a pure function returning `{ redirectVerified, reason, entryId? }`, **failing closed** (missing or malformed facts, an empty table or any mismatch give `redirectVerified: false`). Reasons: `verified`, `no_verification`, `platform_not_verified`, `environment_not_verified`, `sdk_mismatch`, `react_native_mismatch`, `os_major_mismatch`, `facts_unavailable`.

### 5.3 The combined decision: the boolean is mandatory
```
credentialsAllowed(facts, table) =
    platform is web
 OR ( NATIVE_AUTH_TRANSPORT_VERIFIED === true  AND  evaluateRedirectVerification(facts, table).redirectVerified )
```
- **Two keys.** The registry can never open the gate by itself. With the boolean `false` (today, and after every commit in this plan) every native configuration is blocked, including a configuration that matches an entry. A third reason, `authorisation_not_granted`, is reported ahead of any registry result.
- The runtime seams (`platform`, `nativeVerified`) stay and gain `facts` and `registry` seams for tests.
- `check:transport` keeps its rule that the boolean is `false`; the new guard (section 7) adds the coupling to the authorisation requirements.

### 5.4 Matrix (behaviour the tests pin)
| Boolean | Platform and environment | SDK / RN runtime / iOS major | Registry entry | Result |
|---|---|---|---|---|
| `false` | any native, **including an exact match of an entry** | any | present or empty | **blocked** (`authorisation_not_granted`) |
| `true` (tests only) | ios, Expo Go | all equal the entry | present | allowed |
| `true` (tests only) | ios, Expo Go | SDK, runtime or iOS major differs | present | blocked (`sdk_mismatch`, `react_native_mismatch`, `os_major_mismatch`) |
| `true` (tests only) | ios, development client / standalone / bare / unknown | any | present | blocked (`environment_not_verified`) |
| `true` (tests only) | android | any | present | blocked (`platform_not_verified`) |
| `true` (tests only) | any native | missing or garbage facts | present | blocked (`facts_unavailable`) |
| `true` (tests only) | any native | any | empty | blocked (`no_verification`) |
| any | web | n/a | n/a | allowed (unchanged) |
The production constant is `false`, so only the first row can occur in a real build.

### 5.5 Automatic invalidation of the registry on upgrades
At build time `check:native-gate` compares every entry with the installed `expo` and `react-native` package versions (exact, D3) and the SDK major, and fails the build with "redirect verification stale: re-run the canary and update the entry". At run time an Expo Go update that changes the SDK or runtime stops matching. A **warning** (not a failure, D5) is printed when an entry is older than 180 days.

### 5.6 UI wording (approved with your revised text)
Two separate rows on the About screen, never merged:
1. **Native authenticated transport**, status **BLOCKED**, text: "Authenticated requests are disabled. Redirect verification alone does not authorise access."
2. **Redirect verification**, status **Verified** only when an exact configuration matches an entry, text: "Redirect behaviour verified for iOS in Expo Go, SDK 58.0.0, React Native [runtime]. This verifies redirect handling only; it does not authorise authenticated requests." (the version values are read from the matching entry). When no entry matches the status reads **Not verified** with the reason (my wording for this case, not yet reviewed by you: "Redirect behaviour is not verified for this build: <reason>. Authenticated requests are disabled.").
The **Connection screen** shows the same two statements: the authentication block with its reason (`authorisation_not_granted` first), and, where relevant, the redirect-verification state with the sentence that it does not authorise authenticated requests. The existing About test (five BLOCKED rows) is unchanged because row 2 never reads "BLOCKED".

## 6. What must be true before the boolean can ever change (evidence, outside this plan)
A separate document, `docs/mobile/NATIVE_AUTH_AUTHORISATION.md`, created in commit 3, lists these requirements (R1-R11) with a status and an evidence link; **all are NOT MET today**, and each stays NOT MET until independently evidenced and approved. Changing the boolean needs every item MET and your explicit approval of a separate plan.
| # | Requirement | Evidence needed | Status |
|---|---|---|---|
| R1 | **HTTPS with the actual approved backend**: certificate validation, App Transport Security, no cleartext exceptions, behaviour on certificate failure | a device run against an approved HTTPS endpoint (the real phone-to-backend testing that Option B deferred) | NOT MET |
| R2 | **Complete enrollment on a physical device**: code entry, token receipt, Keychain write, `/v1/me` | device run against a synthetic backend, with the transport evidence | NOT MET |
| R3 | **Complete refresh on a physical device**: rotation, lost-response recovery with the same Idempotency-Key, concurrent refresh, interruption (app killed or backgrounded mid-refresh) | device runs, with the persisted-state checks of M8.1 | NOT MET |
| R4 | **Each build type separately**: development client, standalone, TestFlight and App Store builds, each with its own redirect and authentication evidence | one verification per environment | NOT MET (all unsupported) |
| R5 | **Failure scenarios on a device**: offline, timeouts, 401/403/429/5xx, TLS failure, captive portal or proxy, redirect on GET and other methods, malformed bodies, kill switch | a written device test run with results | NOT MET |
| R6 | **Credential handling on a device**: Keychain-only storage, no logging or screenshot exposure, erase on logout and reset, behaviour when secure storage is unavailable | device checks plus a review | NOT MET |
| R7 | **The four production gates G1-G4 and operator approvals** for any real backend or data | separate approvals; nothing in this plan changes them | BLOCKED |
| R8 | **Redirect coverage beyond POST:** the relevant HTTP methods (at least GET, POST, PUT, PATCH and DELETE as the app uses or could use them) and redirect scenarios (301, 302, 303, 307, 308, with and without a body, to another origin, scheme or port, chains and loops, `Location` edge cases, a server that redirects only after earlier requests) | an extended canary run with results per method and scenario; today only POST with four statuses has evidence | NOT MET |
| R9 | **Transport enforcement:** every authenticated network request goes through the approved `expo/fetch` transport; no alternate `fetch`, `XMLHttpRequest`, WebSocket, native module or library bypasses it | the existing lint and `check:transport` bans extended to every network path (including dependencies that make requests), plus a runtime check or test that fails on an unapproved network call | NOT MET |
| R10 | **Authentication lifecycle on the actual device:** logout, token deletion (Keychain and memory), refresh failure, session revocation by the server and by another device, expiry, reset | device runs with the results, matching the M8.1 persisted-state tests | NOT MET |
| R11 | **Build provenance:** the exact native binary, runtime and build configuration under test are identified and recorded (for Expo Go: the Expo Go version and its runtime; for any other build: its own binary, commit and configuration). **Expo Go evidence is never transferred to a standalone, development-client, TestFlight or App Store build.** | a provenance record in each evidence document, checked by the guard | NOT MET |
Redirect verification (this plan) contributes to R1, R5 and R8 as one input; it satisfies none of R1-R11 on its own.

## 7. CI guard `check:native-gate` (no dependencies; in `check:all` and the workflow; commit 3)
Fails when: (a) an entry has `platform` other than `ios`, `environment` other than `expo-go`, or `scope` other than `redirect-behaviour-only`; (b) fields are malformed, ids duplicated or `verifiedOn` is invalid or in the future; (c) the evidence file is missing or lacks the entry id and the pinned values; (d) the entry is stale against the installed `expo` and `react-native` versions or the SDK major; (e) the evidence document lacks PASS for all of 301, 302, 307 and 308; (f) **`NATIVE_AUTH_TRANSPORT_VERIFIED` is `true` while any requirement R1-R11 in `NATIVE_AUTH_AUTHORISATION.md` is not MET with an approval reference**; (f2) an evidence document lacks the build-provenance record (R11) or claims evidence for an environment other than the one in the entry; (g) `credentialsAllowed` does not contain the conjunction with the boolean (a static check that nobody reintroduces a registry-only path). **Negative tests** (fixtures) cover each failure plus a passing control, including "boolean true with an unmet requirement fails".

## 8. Tests
1. Evaluator matrix of section 5.4, at least 25 cases, including garbage and missing facts, prerelease tags, numeric and string `Platform.Version`.
2. **Authorisation separation (the key tests):** with a registry that contains an exact-match entry **and the boolean `false`**: enrollment, refresh and an authenticated request each reject with `blocked_transport` and **no request reaches the network**; the same with every environment and platform. Only with the boolean `true` **and** a match does a request reach the http controls (origin allowlist, `redirect: "manual"`, redirect discard).
3. A test that the committed production boolean is `false` and that the committed registry cannot change `credentialsAllowed` for any facts.
4. `getRuntimeFacts()` with mocked `Platform` and `Constants` for Expo Go, development client, standalone and bare.
5. UI tests for the two About rows and the Connection screen reasons; demo mode still makes zero network calls.
6. Phase invariant: **while the registry is empty, every native fact set is blocked**; commit 4 changes only this test and the data file.
7. Guard negative tests (section 7).
Existing tests of the boolean (`transport.test.ts`, `runtime.test.ts`, `harness.ts`, the About count) keep passing unchanged except where seams are extended.

## 9. Commit sequence
| # | Commit | State of the gate |
|---|---|---|
| 1 | **DONE `e4f6331`:** canary evidence records `reactNativeRuntime`, `executionEnvironment`, `expoGoVersion` (later renamed `expoGoBuild`) | unchanged (closed) |
| - | **Your step (approved, in progress):** re-run the iPhone canary on the same Expo Go 58.0.5 with the new build and send the updated evidence. **Commits 2 and 3 do not start until you have reviewed it.** | unchanged |
| 2 | `refactor(mobile): redirect-verification evaluator; authentication stays blocked`: runtime facts, evaluator, **empty** `redirectVerifications.json`, the two-key `credentialsAllowed`, runtime seams, UI rows, tests of section 8. **The boolean stays `false` and is mandatory.** | closed everywhere |
| 3 | `ci(mobile): add check:native-gate and the authorisation checklist`: the guard with negative tests, `NATIVE_AUTH_AUTHORISATION.md` (R1-R11, all NOT MET) | closed everywhere |
| 4 | **Conditional; needs your separate approval after commits 2 and 3 and the new evidence are reviewed:** add **one registry entry** (redirect behaviour only), the evidence update and docs, and the flipped phase-invariant test. It does **not** touch the boolean and, by tests 2 and 3, cannot authorise anything. | still closed for authentication |
| later | Flipping `NATIVE_AUTH_TRANSPORT_VERIFIED` is **not part of this plan**: it needs R1-R7 evidence and a separate plan and approval. | - |
After each commit: `check:all`, a clean-checkout `npm ci`, the real GitHub Actions run, no dependency or lockfile change; contract and E2E run after commit 2 (demo mode, unaffected).

## 10. Risks and limits
1. Run-time facts are read by the same JavaScript they describe: the registry records evidence and guards against mistakes (wrong build, silent upgrade); it does not defend against a compromised app.
2. Strict matching has a cost: an Expo Go update or an `expo` or `react-native` patch bump breaks the match until the canary is re-run and the entry updated.
3. The canary covered one device, one iOS version, plain HTTP over a private network and a cooperative test server. Not claimed: HTTPS, release builds, Android, a hostile server that redirects later, the full enrollment and refresh flows.
4. `Constants.executionEnvironment` and `Platform.constants.reactNativeVersion` must be readable in Expo Go SDK 58; the re-run proves that before anything depends on it. If a value is unreadable, work stops and returns to you.
5. A reader could still mistake the About "Redirect verification" row for authentication readiness; the wording in 5.6 and the separate BLOCKED row are there to prevent that, and I would like your wording review before commit 2.

## 11. Decisions recorded (your answers to E1-E4)
- **E1** approved (two independent mechanisms; the boolean is retained and mandatory; the registry is redirect-only).
- **E2** approved with additions R8-R11 (section 6); all of R1-R11 stay NOT MET.
- **E3** approved with the revised wording (section 5.6), including the Connection screen.
- **E4** approved (commit 4 separately reviewed; the boolean is excluded).
**Still open, for the evidence review:** the exact `reactNativeRuntime` value, the execution environment and the Expo Go version from the re-run, which become the registry entry's pins; and your review of the one sentence marked "not yet reviewed" in section 5.6.
