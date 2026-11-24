# Redirect canary: how to run it (Windows machine + iPhone with Expo Go)

**Purpose.** Show, on your physical iPhone, whether the app's network transport follows a cross-origin redirect and re-sends the request (header **and** body) to a second origin. Until this evidence is reviewed and you explicitly approve, `NATIVE_AUTH_TRANSPORT_VERIFIED` stays `false` and the iPhone sends no enrollment code, refresh token or bearer token.

**What it is not.** It never contacts the Abu AI backend. It carries **fake** credentials only (`CANARY-FAKE-BEARER-TOKEN-0000`, `CANARY-FAKE-ENROLL-0000`, `CANARY-FAKE-REFRESH-0000`). It runs on your **private home/office Wi-Fi**, never through Codespaces, a tunnel, a VPN/tailnet, port forwarding or any public port.

## What runs where
| Piece | Where | Notes |
|---|---|---|
| `scripts/redirect-canary.js` (two HTTP origins on ports 8801 and 8802, same private IP) | your Windows machine | binds **only** to a private IPv4 (10/8, 172.16/12, 192.168/16); refuses `0.0.0.0`, public addresses and non-private peers |
| Expo dev server (`npx expo start --lan`) | your Windows machine | LAN mode, **not** `--tunnel` |
| Expo Go + the app's Settings > "Transport canary (debug)" screen | iPhone, same Wi-Fi | sends the fake requests through the app's real transport module (`src/api/transport.ts`, `expo/fetch`) |

The screen exists only in debug builds (Expo Go dev). Origin **A** answers `POST /redir/301`, `/redir/302`, `/redir/307`, `/redir/308` with a redirect to origin **B**. Origin **B** records anything it receives. A safe transport shows the redirect response and **B receives nothing**.

## Steps (Windows, PowerShell)
1. **Prerequisites.** Node 22 (`node -v`), the repository checked out, then:
   ```powershell
   cd <repo>\mobile
   npm ci
   ```
2. **Make sure the Wi-Fi profile is Private** (not Public) and note your LAN address:
   ```powershell
   Get-NetConnectionProfile            # NetworkCategory must say Private
   Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -match '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)' }
   ```
   If the category is `Public`, change it in Settings > Network & internet > Wi-Fi > your network > Network profile type > **Private network**.
3. **Open the two ports for the PRIVATE profile only** (Administrator PowerShell; temporary rule, removed in step 9):
   ```powershell
   New-NetFirewallRule -DisplayName "Abu AI redirect canary (temporary)" -Direction Inbound -Protocol TCP -LocalPort 8801,8802 -Profile Private -RemoteAddress LocalSubnet -Action Allow
   ```
   If Windows shows a firewall prompt for Node.js, tick **Private networks only** and untick Public.
4. **Start the canary** (leave the window open):
   ```powershell
   node scripts/redirect-canary.js
   ```
   It prints the address to enter on the phone, for example `http://192.168.1.20:8801`. If it says no private address was found, you are not on a private LAN: stop here. You can also pass `--host <your private IP>`.
5. **Start Expo in LAN mode** in a second PowerShell window:
   ```powershell
   cd <repo>\mobile
   npx expo start --lan
   ```
6. **On the iPhone** (same Wi-Fi): open the Camera, scan the Expo QR code, open in **Expo Go**. The app starts in synthetic **demo mode** (no backend, nothing connected).
7. In the app: tap the gear (Settings) > **Transport canary (debug)** > type the canary address from step 4 > **Run canary (301, 302, 307, 308)**.
8. **Read the result** on the phone and in the Windows console:
   - **PASS**: for all four statuses the app received the redirect and the second origin received **nothing** (`bReceived=false`). Console prints `=== RESULT: PASS ===`.
   - **FAIL**: the second origin received a request (the console shows `B RECEIVED ...` with `authorization:present` and/or leaked markers). The transport follows redirects and is **not safe**.
   - **INCONCLUSIVE**: the canary was not reached, or the app did not see a redirect (for example iOS App Transport Security blocking cleartext HTTP to a LAN address, a firewall, a different Wi-Fi). **This is not a pass.** Fix the setup and run again.
   Take screenshots of (a) the phone's result card including the evidence JSON and (b) the Windows console.
9. **Clean up**: `Ctrl+C` in both windows, then in Administrator PowerShell:
   ```powershell
   Remove-NetFirewallRule -DisplayName "Abu AI redirect canary (temporary)"
   ```

## Evidence to submit (for review; the gate stays false until you approve)
- Device model, iOS version, Expo Go version, date and time.
- The phone screenshot (result + evidence JSON: `overall`, per-status `appSaw`, `secondOriginReceived`, `reachedFirstOrigin`, `transport.expoFetch` must be `true`, `rnFetchForced` must be `false`).
- The Windows console output (`=== RESULT: ... ===` and any `B RECEIVED` lines).
- Confirmation that the Wi-Fi profile was Private and the firewall rule was removed afterwards.

## Reviewer checklist
1. `overall` is `PASS` and all four statuses (301, 302, 307, 308) are `PASS`; none is `INCONCLUSIVE`.
2. `secondOriginReceived` is `false` everywhere; the console has no `B RECEIVED` line.
3. `transport.expoFetch` is `true`, `rnFetchForced` is `false`; `platform` is `ios`.
4. The same result on the OS version(s) you intend to support; re-run after any Expo SDK or `react-native` upgrade.
5. Only then, and only with an explicit approval, a reviewed commit flips `NATIVE_AUTH_TRANSPORT_VERIFIED` to `true` (and updates the tests that pin it).

## Safety rules (also enforced in code)
- The server refuses to bind to anything but a private IPv4 and rejects non-private peers. It prints a reminder not to forward ports.
- Fake credentials only; the app screen never reads a real token; it does not use the Abu AI runtime or the backend allowlist.
- The screen is not present in release builds. The canary is temporary: remove the firewall rule when finished.
- This does **not** clear any of the four production security gates.
