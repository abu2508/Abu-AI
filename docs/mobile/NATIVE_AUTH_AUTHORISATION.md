# Native authenticated requests: authorisation checklist (R1-R11)

Status: **every requirement is NOT MET (R7 is BLOCKED). `NATIVE_AUTH_TRANSPORT_VERIFIED` is `false` and stays `false`.** Nothing here is approved.
This file is read by `npm run check:native-gate` (`mobile/scripts/check-native-gate.mjs`). Source: `docs/mobile/NATIVE_GATE_PLAN.md`, section 6, as approved with the additions R8-R11.

## What this checklist is for
Two independent things exist in the app:
1. **Redirect verification** (`mobile/src/config/redirectVerifications.json`): evidence that the transport did not forward a credentialed request across a redirect, for one exact configuration. It is **redirect-only** and contributes to R1, R5 and R8 as an input. It satisfies **none** of R1-R11 and can never authorise authentication.
2. **Native authentication authorisation**: the boolean `NATIVE_AUTH_TRANSPORT_VERIFIED`. Native enrollment, refresh and authenticated requests are allowed only when the boolean is `true` **and** the configuration is redirect-verified. The boolean may become `true` only in a reviewed commit, after **every** row below is `MET` with an approval reference, under a separate plan that the owner has explicitly approved. The guard fails the build if the boolean is `true` while any row is not `MET` or has no approval reference.

The Expo Go redirect canary result must **never** be transferred to a standalone, development-client, TestFlight or App Store build (R11).

## Rules for editing this file
- Status is exactly `MET`, `NOT MET` or `BLOCKED`.
- A row may become `MET` only with a link to its evidence and an approval reference from the owner (a review comment, commit or decision record), in the last two columns. An empty approval column, `-` or `none` does not count.
- Do not mark a row `MET` because a unit test passes: Jest and Node tests are not device evidence.

| ID | Requirement | Status | Evidence | Approval |
|---|---|---|---|---|
| R1 | HTTPS with the actual approved backend: certificate validation, App Transport Security, no cleartext exceptions, behaviour on certificate failure (a device run against an approved HTTPS endpoint; Option B deferred this) | NOT MET | - | - |
| R2 | Complete enrollment on a physical device: code entry, token receipt, Keychain write, `/v1/me` (against a synthetic backend, with the transport evidence) | NOT MET | - | - |
| R3 | Complete refresh on a physical device: rotation, lost-response recovery with the same Idempotency-Key, concurrent refresh, interruption (app killed or backgrounded mid-refresh), with the M8.1 persisted-state checks | NOT MET | - | - |
| R4 | Each build type verified separately: development client, standalone, TestFlight and App Store builds, each with its own redirect and authentication evidence | NOT MET | - | - |
| R5 | Failure scenarios on a device: offline, timeouts, 401/403/429/5xx, TLS failure, captive portal or proxy, redirect on GET and other methods, malformed bodies, kill switch (a written device test run with results) | NOT MET | - | - |
| R6 | Credential handling on a device: Keychain-only storage, no logging or screenshot exposure, erase on logout and reset, behaviour when secure storage is unavailable (device checks plus a review) | NOT MET | - | - |
| R7 | The four production security gates G1-G4 and operator approvals for any real backend or data (separate approvals; nothing in this work changes them) | BLOCKED | - | - |
| R8 | Redirect coverage beyond POST: GET, POST, PUT, PATCH and DELETE as the app uses or could use them; 301, 302, 303, 307, 308 with and without a body, to another origin, scheme or port, chains and loops, `Location` edge cases, a server that redirects only after earlier requests (an extended canary run with results per method and scenario; today only POST with four statuses has evidence) | NOT MET | - | - |
| R9 | Transport enforcement: every authenticated request goes through the approved `expo/fetch` transport; no alternate `fetch`, `XMLHttpRequest`, WebSocket, native module or library bypasses it (the bans of `check:transport` extended to every network path including dependencies, plus a runtime check that fails on an unapproved network call) | NOT MET | - | - |
| R10 | Authentication lifecycle on the actual device: logout, token deletion (Keychain and memory), refresh failure, session revocation by the server and by another device, expiry, reset (device runs matching the M8.1 persisted-state tests) | NOT MET | - | - |
| R11 | Build provenance: the exact native binary, runtime and build configuration under test are identified and recorded in each evidence document (for Expo Go: the Expo Go marketing version and build, the runtime and the repository commit; for any other build: its own binary, commit and configuration). Expo Go evidence is never transferred to a standalone, development-client, TestFlight or App Store build | NOT MET | - | - |

## What the guard also enforces for a redirect entry (for the commit that adds one)
An entry may be added only in a separately approved commit. The evidence document it names (under `docs/mobile/reports/`) must contain: the entry id and its pinned values, `PASS` for 301, 302, 307 and 308, and a build-provenance record made of these lines (so that the missing metadata cannot be skipped):
```
Provenance environment: expo-go
Provenance repository commit: <40 hex characters>
Provenance Expo Go marketing version: <value from Expo Go's Settings screen>
Provenance Expo Go build: <value reported by the app>
```
The entry is also checked against the installed `expo` and `react-native` versions (exact) and the SDK major; a mismatch fails the build with "redirect verification stale". An entry older than 180 days prints a warning.
