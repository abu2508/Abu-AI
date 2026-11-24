# M8.1 authentication and secure API foundation: design as built

Synthetic data and the mock model only; the four production gates stay BLOCKED; no backend change.

## Trust boundaries
- **Where the app may connect.** Only to an *operator-approved development backend*: entries in the build configuration (`ABU_DEV_BACKENDS` -> `app.config.js` -> `extra.abuDevBackends`), each `kind: "development"`, an exact origin (no path/query/credentials), HTTPS, or plain HTTP to loopback in a debug build only; IP literals refused. A hostname that merely looks like a Codespaces URL proves nothing and is **not** accepted. The user can only *pick* an entry; there is no URL field, so a pasted URL can never become a destination. The shipped build approves nothing (demo only). Production entries are refused while the gates are blocked.
- **Every request** re-checks the approved list, accepts only `/v1/[A-Za-z0-9/_-]*` paths, sends `credentials: omit`, `redirect: manual`, `cache: no-store`, and discards any 3xx, opaque redirect or response whose final URL is on another origin. Limitation (honest): React Native's fetch may follow a redirect internally before the check; the backend never redirects, and the response is discarded, but an `Authorization` header could already have reached the redirect target. Mitigation is that the approved backends are operator-owned and never redirect; verify on device (carry-forward).
- **Demo mode** is the default: nothing is requested at launch (restore reads the Keychain only), and connecting needs an explicit press. A "Dev backend · <label>" chip sits beside the "Synthetic data" chip while connected.

## Secrets
| Secret | Where | Never |
|---|---|---|
| refresh token | iOS Keychain, `WHEN_UNLOCKED_THIS_DEVICE_ONLY`, ONE record (`abu.auth.v1`) | Zustand, React state, AsyncStorage, logs, query keys, URLs |
| access token | closure inside `createTokenVault` (memory only) | disk, store, devtools state, logs |
| enrollment code | the text field's state until Connect; cleared in `finally` | storage, logs, cache |
| Idempotency-Key | the Keychain record (`pending`), 128-bit CSPRNG | reused across different tokens; regenerated while a result is unresolved |

Web has no Keychain: a production web export has **no** store (fails closed with a recovery screen); only the dev server uses an in-memory map (screenshots). Nothing is written to localStorage.

## Refresh protocol (`refreshCoordinator.ts`)
1. Concurrent callers share one in-flight promise. A 401 triggers at most one refresh (skipped if another caller already replaced the token) and one replay; a second 401 ends the session.
2. Before sending, `{key, fingerprint(old token)}` is written with the old token (one atomic Keychain write). If that write fails, **nothing is sent**.
3. While a retry record exists the same key and token are replayed: after a timeout, a lost response, an app kill. A new key is never minted while a result may be unresolved. A record whose fingerprint does not match the stored token fails closed.
4. The rotated token and the removal of the retry record are **one** write, so "token stored, record not cleared" and the reverse cannot be observed. If that write fails the access token is not installed, the old token + retry record stay on disk, the app goes to `recovery_required`, and "Try again" replays the same key to obtain the same pair from the server.
5. 401/403 on refresh = session dead: wipe, `expired`. Network/5xx: exponential backoff with jitter, max 3 attempts, 20 s budget. 429: honour `Retry-After` (capped 60 s) if within budget. Offline, contract and other 4xx: stop at once, keep the record.
6. A wipe bumps an epoch; an in-flight refresh from an older epoch discards its result and, if it raced the save, clears the record again.

## Logout, expiry and user isolation
`logout()` tries the server for at most 5 s, then **always** clears memory, Keychain and the query cache, whatever the network did, and tells the user honestly whether the server session may still be active. `wipeLocal()` also runs at the start of every enrollment, on expiry and on reset; query keys are scoped `['u', userId, ...]`. A failed Keychain delete lands in `recovery_required` with "Reset this device". Remote revocation is detected on the next authenticated request (401 -> refresh 401 -> `expired`). Offline mutations fail fast with `OfflineError`; nothing is queued, and reconnecting replays nothing.

## Statuses
`unenrolled | restoring | authenticated | expired | recovery_required` (Zustand, status and reason only). Offline is a separate overlay (`useOnline`).
