# Plan (revision 2): record verified redirect behaviour per configuration, without authorising native authenticated requests

Status: **PLAN ONLY (revision 2). Commit 1 is done; commits 2-4 are NOT approved. Waiting for your review of this revision.**
Branch `claude/charming-davinci-vqv0du`. Revision 1 was commit `c3cbd2232797398a96aef87f31c4c6d995ae760d`.
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
Approval boundary now: **commit 1 and the canary re-run only.** Commits 2, 3 and 4 below wait for your approval of this revision.

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
Expo Go runs the **app JavaScript** from this repository and the **native runtime** from the Expo Go app (tested: 58.0.5, React Native runtime 0.88.0 per the DevTools header; this repository's `react-native` package is 0.88.1). Commit 1 made the canary record `reactNativeRuntime`, `executionEnvironment` and `expoGoVersion`; the re-run (section 8, step 2) supplies the exact values in the same report, so no pin comes from a screenshot.

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

### 5.6 UI wording
About keeps its existing "Native authenticated transport" row (**BLOCKED**) and gets a separate informational row "Redirect verification": for the tested configuration "Verified for iOS in Expo Go, SDK 58.0.0, React Native <runtime> (2026-11-24): redirect behaviour only", otherwise "Not verified: <reason>". Both rows state that Android, development, standalone and release builds are not covered, and that **a verified redirect behaviour does not authorise authenticated requests**. The connection and live-data screens keep showing the authentication block and its reason (`authorisation_not_granted` first). The existing About test (five BLOCKED rows) is unchanged: the new row never reads "BLOCKED".

## 6. What must be true before the boolean can ever change (evidence, outside this plan)
A separate document, `docs/mobile/NATIVE_AUTH_AUTHORISATION.md`, created in commit 3, lists these requirements with a status and an evidence link; **all are NOT MET today**. Changing the boolean needs every item MET and your explicit approval of a separate plan.
| # | Requirement | Evidence needed | Status |
|---|---|---|---|
| R1 | **HTTPS with the actual approved backend**: certificate validation, App Transport Security, no cleartext exceptions, behaviour on certificate failure | a device run against an approved HTTPS endpoint (the real phone-to-backend testing that Option B deferred) | NOT MET |
| R2 | **Complete enrollment on a physical device**: code entry, token receipt, Keychain write, `/v1/me` | device run against a synthetic backend, with the transport evidence | NOT MET |
| R3 | **Complete refresh on a physical device**: rotation, lost-response recovery with the same Idempotency-Key, concurrent refresh, interruption (app killed or backgrounded mid-refresh) | device runs, with the persisted-state checks of M8.1 | NOT MET |
| R4 | **Each build type separately**: development client, standalone, TestFlight and App Store builds, each with its own redirect and authentication evidence | one verification per environment | NOT MET (all unsupported) |
| R5 | **Failure scenarios on a device**: offline, timeouts, 401/403/429/5xx, TLS failure, captive portal or proxy, redirect on GET and other methods, malformed bodies, kill switch | a written device test run with results | NOT MET |
| R6 | **Credential handling on a device**: Keychain-only storage, no logging or screenshot exposure, erase on logout and reset, behaviour when secure storage is unavailable | device checks plus a review | NOT MET |
| R7 | **The four production gates G1-G4 and operator approvals** for any real backend or data | separate approvals; nothing in this plan changes them | BLOCKED |
Redirect verification (this plan) contributes to R1-R6 as one input; it satisfies none of them on its own.

## 7. CI guard `check:native-gate` (no dependencies; in `check:all` and the workflow; commit 3)
Fails when: (a) an entry has `platform` other than `ios`, `environment` other than `expo-go`, or `scope` other than `redirect-behaviour-only`; (b) fields are malformed, ids duplicated or `verifiedOn` is invalid or in the future; (c) the evidence file is missing or lacks the entry id and the pinned values; (d) the entry is stale against the installed `expo` and `react-native` versions or the SDK major; (e) the evidence document lacks PASS for all of 301, 302, 307 and 308; (f) **`NATIVE_AUTH_TRANSPORT_VERIFIED` is `true` while any requirement in `NATIVE_AUTH_AUTHORISATION.md` is not MET with an approval reference**; (g) `credentialsAllowed` does not contain the conjunction with the boolean (a static check that nobody reintroduces a registry-only path). **Negative tests** (fixtures) cover each failure plus a passing control, including "boolean true with an unmet requirement fails".

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
| 1 | **DONE `e4f6331`:** canary evidence records `reactNativeRuntime`, `executionEnvironment`, `expoGoVersion` | unchanged (closed) |
| - | **Your step (approved):** re-run the iPhone canary on the same Expo Go 58.0.5 with the new build, and send the updated evidence | unchanged |
| 2 | `refactor(mobile): redirect-verification evaluator; authentication stays blocked`: runtime facts, evaluator, **empty** `redirectVerifications.json`, the two-key `credentialsAllowed`, runtime seams, UI rows, tests of section 8. **The boolean stays `false` and is mandatory.** | closed everywhere |
| 3 | `ci(mobile): add check:native-gate and the authorisation checklist`: the guard with negative tests, `NATIVE_AUTH_AUTHORISATION.md` (all NOT MET) | closed everywhere |
| 4 | **Needs your separate approval:** add **one registry entry** (redirect behaviour only), the evidence update and docs, and the flipped phase-invariant test. It does **not** touch the boolean and, by tests 2 and 3, cannot authorise anything. | still closed for authentication |
| later | Flipping `NATIVE_AUTH_TRANSPORT_VERIFIED` is **not part of this plan**: it needs R1-R7 evidence and a separate plan and approval. | - |
After each commit: `check:all`, a clean-checkout `npm ci`, the real GitHub Actions run, no dependency or lockfile change; contract and E2E run after commit 2 (demo mode, unaffected).

## 10. Risks and limits
1. Run-time facts are read by the same JavaScript they describe: the registry records evidence and guards against mistakes (wrong build, silent upgrade); it does not defend against a compromised app.
2. Strict matching has a cost: an Expo Go update or an `expo` or `react-native` patch bump breaks the match until the canary is re-run and the entry updated.
3. The canary covered one device, one iOS version, plain HTTP over a private network and a cooperative test server. Not claimed: HTTPS, release builds, Android, a hostile server that redirects later, the full enrollment and refresh flows.
4. `Constants.executionEnvironment` and `Platform.constants.reactNativeVersion` must be readable in Expo Go SDK 58; the re-run proves that before anything depends on it. If a value is unreadable, work stops and returns to you.
5. A reader could still mistake the About "Redirect verification" row for authentication readiness; the wording in 5.6 and the separate BLOCKED row are there to prevent that, and I would like your wording review before commit 2.

## 11. Decisions I need from you now
- **E1:** approve this revision (two keys; the boolean is retained and mandatory; the registry is redirect-only) so commits 2 and 3 can proceed.
- **E2:** confirm the R1-R7 requirement list in section 6, or edit it; it is what the future boolean change will be measured against.
- **E3:** approve the About wording in 5.6, or give me preferred text.
- **E4:** confirm that commit 4 (a registry entry) may be prepared only after commits 2-3 are reviewed, and that flipping the boolean is out of scope here.
