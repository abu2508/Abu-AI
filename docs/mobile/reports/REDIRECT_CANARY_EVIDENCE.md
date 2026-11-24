# Physical-device redirect canary: evidence (iPhone, Expo Go SDK 58)

Status: **evidence recorded for review. `NATIVE_AUTH_TRANSPORT_VERIFIED` is still `false`; nothing was changed because of this result.** All four production security gates remain BLOCKED. The canary used fake credentials only and never contacted the Abu AI backend.
Procedure: `docs/mobile/REDIRECT_CANARY.md`. Collected by the project owner on 2026-11-24; recorded by Claude from the screenshots and the console text the owner supplied. Claude did not run the canary.

## 1. Setup
| Item | Value |
|---|---|
| Repository commit tested | `0c1c04634c791ffa5535dd9678e898dcba1becd1` (branch `claude/charming-davinci-vqv0du`; includes the SDK 58 migration, commits `baa12db` to `408d956`) |
| Windows PC | Node `v22.23.5`, npm `10.9.9`, `win32 x64` (Windows version not supplied) |
| Network | private home Wi-Fi, profile Private; firewall rule limited to TCP 8801-8802, Private profile, local subnet; canary bound to the PC's private LAN address only (shown below as `<PC-LAN-IP>`) |
| Phone | iPhone 17, iOS **27.2** (device model number supplied; omitted here) |
| App runtime | Expo Go **58.0.5** (reports SDK 58.0.0), signed in to an Expo account as Expo Go requires; the app in demo mode (no backend) |
| Expo dev server | `npx expo start --lan` on port 8081 (not `--tunnel`), no trace helper attached |
| Transport under test | the app's real transport module (`expo/fetch`), sending a POST with fake bearer, enrollment and refresh markers (88 bytes of body, `Authorization` header present) |

## 2. What the phone reported (the app's evidence JSON, `generatedAt` 2026-11-24T15:59:41.972Z)
`platform: ios`, `osVersion: 27.2`, `expoSdk: 58.0.0`, `appOwnership: expo`, `transport.expoFetch: true`, `transport.rnFetchForced: false`, `overall: PASS`. For each of the four statuses: `appSaw.httpStatus` equals the status sent (301, 302, 307, 308), `appSaw.type: "default"`, `appSaw.error: null`, `secondOriginReceived: false`, `reachedFirstOrigin: true`, `verdict: PASS`. Screenshot text: "Result: PASS. The second origin received nothing for all four statuses and the app got the redirect."

## 3. What the canary server reported (Windows console, complete output from start-up)
```
[2026-11-24T15:59:42.106Z] A  POST /redir/301 (authorization:true body:88b) -> redirect to B
[2026-11-24T15:59:42.123Z] A  POST /redir/302 (authorization:true body:88b) -> redirect to B
[2026-11-24T15:59:42.135Z] A  POST /redir/307 (authorization:true body:88b) -> redirect to B
[2026-11-24T15:59:42.146Z] A  POST /redir/308 (authorization:true body:88b) -> redirect to B

=== RESULT: PASS ===
  301: PASS  (reachedA=true, bReceived=false)
  302: PASS  (reachedA=true, bReceived=false)
  307: PASS  (reachedA=true, bReceived=false)
  308: PASS  (reachedA=true, bReceived=false)
```
There is **no `B  RECEIVED` line**: the second origin (`<PC-LAN-IP>:8802`) received no request. The first origin received the fake credentials in all four requests (`authorization:true`, 88 bytes), so the test really sent credential-shaped data to a server that then answered with each redirect. The phone's and the PC's clocks differ by about 0.13 s (the phone's `generatedAt` precedes the server's first line by 134 ms), consistent with two devices.

## 4. Reviewer checklist (from `REDIRECT_CANARY.md`)
| # | Criterion | Result |
|---|---|---|
| 1 | `overall` is PASS, all four statuses PASS, none INCONCLUSIVE | **Met** |
| 2 | `secondOriginReceived` false everywhere; console has no `B RECEIVED` | **Met** (phone and server agree) |
| 3 | `transport.expoFetch` true, `rnFetchForced` false, `platform` ios | **Met** |
| 4 | Same result on the OS versions intended to be supported; re-run after any Expo SDK or `react-native` upgrade | **Partly met**: one device, one iOS version (27.2), one Expo Go build (58.0.5) |
| 5 | Only with explicit approval, a reviewed commit flips the gate | **Not done; awaiting review** |

## 5. What this evidence does and does not show
- **Shows:** on this iPhone, in Expo Go SDK 58, the app's `expo/fetch` transport did not follow cross-origin redirects of type 301, 302, 307 or 308 for a POST carrying an `Authorization` header and a body, so none of it reached the second origin.
- **Does not show:** behaviour on Android (not tested); other iOS versions or devices; production or standalone builds (Expo Go is a development client; the native fetch module is the same code, but this was not tested); HTTPS to a real backend; a malicious server that redirects after earlier requests; redirects within one origin or to different schemes. It is valid only for the combination above and must be repeated after any Expo SDK, React Native or `expo/fetch` change.
- **Does not clear** any production security gate (G1-G4) and does not authorise real data or production connectivity.

## 6. Recommendation (not a change)
See the hand-off message of the commit that added this file. In short: the evidence supports a **separate, scoped** reviewed commit, after your explicit approval, that allows native authenticated requests **on iOS only**, ties the verification to the tested SDK so that an SDK upgrade re-blocks it automatically, and keeps Android blocked until it has its own evidence.

## 7. Rerun with runtime facts (2026-11-24, after commit `e4f6331`)
Requested by the owner so the registry design has the exact runtime values. Recorded by Claude from three phone screenshots only. Claude did not run the canary. **`NATIVE_AUTH_TRANSPORT_VERIFIED` is still `false`; nothing was changed because of this result.**

### 7.1 What the phone reported (`generatedAt` 2026-11-24T18:20:25.723Z)
| Field | Value |
|---|---|
| `platform` / `osVersion` | `ios` / `27.2` (iPhone 17) |
| `expoSdk` | `58.0.0` |
| `reactNativeRuntime` | `0.88.0` |
| `executionEnvironment` | `storeClient` (Expo Go) |
| `appOwnership` | `expo` |
| `expoGoVersion` | `1017894` (looks like a build identifier, not the marketing version; see 7.3) |
| `transport` | `expoFetch: true`, `rnFetchForced: false` |
| `overall` | **PASS** |
For each of 301, 302, 307 and 308: `appSaw.httpStatus` equals the status, `appSaw.type: "default"`, `appSaw.error: null`, `secondOriginReceived: false`, `reachedFirstOrigin: true`, `verdict: PASS`. The canary address entered was the correct first-origin port (8801 on the PC's private LAN address, shown elsewhere as `<PC-LAN-IP>`), unlike the earlier INCONCLUSIVE attempt that used the Metro port.

### 7.2 What the canary server reported for this run (Windows console, owner-supplied text)
```
[2026-11-24T18:20:25.978Z] A  POST /redir/301 (authorization:true body:88b) -> redirect to B
[2026-11-24T18:20:25.997Z] A  POST /redir/302 (authorization:true body:88b) -> redirect to B
[2026-11-24T18:20:26.012Z] A  POST /redir/307 (authorization:true body:88b) -> redirect to B
[2026-11-24T18:20:26.027Z] A  POST /redir/308 (authorization:true body:88b) -> redirect to B

=== RESULT: PASS ===
  301: PASS  (reachedA=true, bReceived=false)
  302: PASS  (reachedA=true, bReceived=false)
  307: PASS  (reachedA=true, bReceived=false)
  308: PASS  (reachedA=true, bReceived=false)
```
There is **no `B  RECEIVED` line**: the second origin received nothing, and the first origin received the fake credentials (`authorization:true`, 88 bytes) for all four statuses. Phone and server agree. The phone's `generatedAt` (18:20:25.723Z) precedes the server's first line by about 255 ms, consistent with two devices whose clocks differ slightly. The console output was supplied as text from the start of the canary's output; the start-up banner is omitted above except that it showed the canary bound to the PC's private LAN address.

### 7.2a Provenance items supplied after the rerun (owner-supplied text; see section 8)
The repository commit, Node version, Expo Go marketing version and the firewall cleanup were supplied later by the owner and are recorded in section 8.

### 7.3 Notes
- The new fields are confirmed readable in Expo Go SDK 58: SDK 58.0.0, React Native runtime 0.88.0, `storeClient`, iOS 27.2.
- Owner approved renaming `expoGoVersion` to `expoGoBuild` (the value above is a build identifier, not 58.0.5) and keeping a separate `expoGoMarketingVersion`, recorded by hand from Expo Go's Settings screen and never derived from the build value. The app now reports `expoGoMarketingVersion: null`. The run above used the old field name, `expoGoVersion`.
- Scope is unchanged from sections 5 and 6: redirect behaviour only, one device, Expo Go only. It does not authorise authenticated requests and does not clear any production security gate (G1-G4).

## 8. Build provenance of the rerun in section 7 (R11) and the registry entry (applied in commit 4)
Recorded from output and text the owner supplied. Claude did not run any of it.

```
Provenance environment: expo-go
Provenance repository commit: ca9cff0b90eecc348ccf486b71337c516557bfeb
Provenance Expo Go marketing version: 58.0.5
Provenance Expo Go build: 1017894
```
| Item | Value | Source |
|---|---|---|
| Repository commit of the Windows checkout | `ca9cff0b90eecc348ccf486b71337c516557bfeb` (contains commit 1, `e4f6331`, which adds the runtime facts) | owner's `git log -1 --format=%H` in `~\Abu-AI\mobile`. The checkout was not updated between the run and this output; the `mobile` code is identical in later commits up to `998e29f` |
| Node on the PC | `v22.23.5` | owner's `node --version` |
| Expo Go marketing version | `58.0.5` | read by the owner from Expo Go's Settings tab; recorded by hand, never derived from the build value |
| Expo Go build identifier | `1017894` | reported by the app (`expoGoBuild`; the rerun's JSON used the old name `expoGoVersion`) |
| Native runtime | React Native `0.88.0`, Expo SDK `58.0.0`, `executionEnvironment` `storeClient`, iOS `27.2` (iPhone 17) | the app's JSON in section 7.1 |
| Repository packages | `expo` `58.0.26`, `react-native` `0.88.1` (the app JavaScript; differs from the native runtime `0.88.0`, which belongs to Expo Go) | `package.json` pins |
| Firewall cleanup | The temporary rule "Abu AI redirect canary (temporary)" (inbound, Private profile) was still present when checked; the owner removed it, and `Get-NetFirewallRule -DisplayName "*canary*"` then printed nothing, as did `Get-NetTCPConnection -LocalPort 8801,8802,8081 -State Listen` | owner's PowerShell output |
| Playwright E2E (separate, unrelated to the canary) | Reported by Claude after commits 2 and 3: only the last 30 lines of its output were retained, all PASS; the complete run is **not** independently confirmed | this evidence record |

Registry entry (the owner approved commit 4; it is applied unchanged in `mobile/src/config/redirectVerifications.json`, and `NATIVE_AUTH_TRANSPORT_VERIFIED` stays `false`, so it authorises nothing):
```json
{
  "id": "ios-expo-go-sdk58-2026-11-24",
  "scope": "redirect-behaviour-only",
  "platform": "ios",
  "environment": "expo-go",
  "expoSdk": "58.0.0",
  "reactNativeRuntime": "0.88.0",
  "iosMajor": 27,
  "buildPins": { "expo": "58.0.26", "react-native": "0.88.1" },
  "canary": "301, 302, 307, 308: PASS, second origin received nothing",
  "evidence": "docs/mobile/reports/REDIRECT_CANARY_EVIDENCE.md",
  "verifiedOn": "2026-11-24"
}
```
What the entry would mean: for iOS 27.x in Expo Go with SDK 58.0.0 and native React Native 0.88.0, the redirect canary passed for POST with 301, 302, 307 and 308 on one iPhone over plain HTTP on a private network. It says **nothing** about authentication, Android, other builds, HTTPS, other methods or other redirect scenarios (R1, R4, R5, R8 stay NOT MET), and with `NATIVE_AUTH_TRANSPORT_VERIFIED` false it changes no behaviour. The entry matches the evidence of **section 7 (the rerun)**, `generatedAt` 2026-11-24T18:20:25.723Z; section 2 (the first run, SDK 58.0.0 too) did not record the runtime facts.
