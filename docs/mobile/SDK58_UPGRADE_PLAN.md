# Plan: upgrade the mobile app from Expo SDK 57 to SDK 58

Status: **PLAN ONLY (revision 2: section 3 expanded, verification criteria extended). Nothing has been changed. Waiting for your approval.**
Branch: `claude/charming-davinci-vqv0du` (base `bf0213af6c54d74b92bb3c83d694e8be2f7e4628`, M8.2 plus review follow-up, CI green).
Standing constraints are unchanged: ₹0–₹300 budget, mock model, synthetic data, four production gates BLOCKED, no real data, no secrets in the app, `NATIVE_AUTH_TRANSPORT_VERIFIED` stays `false`.

## 1. Why
The iPhone redirect canary cannot run. Expo Go from the App Store is for **SDK 58.0.0**; the app is on **SDK 57**. iOS cannot install an older Expo Go (the app's own error message says so), so Expo Go cannot open the project. The only way to get a physical-device answer with the current tooling, without a Mac, an Apple developer account or an EAS build, is to move the app to SDK 58. The device evidence must also describe the transport that will ship, so the upgrade has to come before the canary result means anything.

## 2. Scope
In scope: bump Expo-managed packages to the versions SDK 58 specifies; adapt code only where something breaks; update the tooling that pins SDK 57; re-run every gate; re-read the `expo/fetch` native redirect behaviour for SDK 58; update docs.
**Out of scope:** any feature or UI change; backend source, migrations or API contract; new packages; M8.3; B3b; flipping the native gate; production-gate work; enabling native authenticated connectivity; the canary run itself (a separate step you perform).

## 3. Package-by-package dependency table
Source of the target versions: `bundledNativeModules.json` inside `expo@58.0.26` (current `latest`), then resolved in a **scratch copy** (repository `package.json` and `package-lock.json` untouched) with `npm install --package-lock-only --ignore-scripts` on Node 22. All "target" values are exact lockfile versions of that preview resolution. The complete transitive diff is in `docs/mobile/SDK58_DEPENDENCY_DIFF.md`; the lockfile committed during implementation is the authority, and the registry may have moved by then (any difference will be reported).

"Change" uses plain semver labels. For Expo-managed packages a "MAJOR" label mostly means "SDK 57 -> SDK 58 line" (their major number tracks the SDK), not necessarily a breaking API change; the genuinely independent major bump is `react-native-gesture-handler` 2 -> 3 (section 4, risk 2).

### 3.1 Direct dependencies (38: 28 runtime, 10 dev; every entry is exact-pinned in `package.json`)
| # | Package | Kind | package.json now | Lockfile now | SDK 58 target (exact) | Change |
|---|---|---|---|---|---|---|
| 1 | `@expo/metro-runtime` | runtime | 57.0.19 | 57.0.19 | **58.0.17** | MAJOR |
| 2 | `@react-native-async-storage/async-storage` | runtime | 2.2.0 | 2.2.0 | **2.2.0** | unchanged |
| 3 | `@tanstack/react-query` | runtime | 5.108.4 | 5.108.4 | **5.108.4** | unchanged |
| 4 | `expo` | runtime | 57.0.34 | 57.0.34 | **58.0.26** | MAJOR |
| 5 | `expo-constants` | runtime | 57.0.25 | 57.0.25 | **58.0.16** | MAJOR |
| 6 | `expo-crypto` | runtime | 57.0.4 | 57.0.4 | **58.0.8** | MAJOR |
| 7 | `expo-device` | runtime | 57.0.2 | 57.0.2 | **58.0.5** | MAJOR |
| 8 | `expo-font` | runtime | 57.0.4 | 57.0.4 | **58.0.12** | MAJOR |
| 9 | `expo-linking` | runtime | 57.0.14 | 57.0.14 | **58.0.17** | MAJOR |
| 10 | `expo-network` | runtime | 57.0.3 | 57.0.3 | **58.0.5** | MAJOR |
| 11 | `expo-router` | runtime | 57.0.32 | 57.0.32 | **58.0.32** | MAJOR |
| 12 | `expo-secure-store` | runtime | 57.0.4 | 57.0.4 | **58.0.3** | MAJOR |
| 13 | `expo-splash-screen` | runtime | 57.0.12 | 57.0.12 | **58.0.14** | MAJOR |
| 14 | `expo-status-bar` | runtime | 57.0.1 | 57.0.1 | **58.0.5** | MAJOR |
| 15 | `expo-symbols` | runtime | 57.0.3 | 57.0.3 | **58.0.5** | MAJOR |
| 16 | `expo-system-ui` | runtime | 57.0.4 | 57.0.4 | **58.0.7** | MAJOR |
| 17 | `react` | runtime | 19.2.3 | 19.2.3 | **19.3.0** | minor |
| 18 | `react-dom` | runtime | 19.2.3 | 19.2.3 | **19.3.0** | minor |
| 19 | `react-native` | runtime | 0.86.3 | 0.86.3 | **0.88.1** | minor |
| 20 | `react-native-gesture-handler` | runtime | 2.32.0 | 2.32.0 | **3.3.0** | MAJOR |
| 21 | `react-native-reanimated` | runtime | 4.5.1 | 4.5.1 | **4.7.1** | minor |
| 22 | `react-native-safe-area-context` | runtime | 5.7.0 | 5.7.0 | **5.11.1** | minor |
| 23 | `react-native-screens` | runtime | 4.26.2 | 4.26.2 | **4.30.1** | minor |
| 24 | `react-native-svg` | runtime | 15.15.4 | 15.15.4 | **15.16.0** | minor |
| 25 | `react-native-web` | runtime | 0.21.4 | 0.21.4 | **0.21.4** | unchanged |
| 26 | `react-native-worklets` | runtime | 0.10.1 | 0.10.1 | **0.13.0** | minor |
| 27 | `zod` | runtime | 4.6.7 | 4.6.7 | **4.6.7** | unchanged |
| 28 | `zustand` | runtime | 5.1.0 | 5.1.0 | **5.1.0** | unchanged |
| 29 | `@testing-library/react-native` | dev | 14.1.0 | 14.1.0 | **14.1.0** | unchanged |
| 30 | `@types/react` | dev | 19.2.18 | 19.2.18 | **19.2.18** | unchanged |
| 31 | `eslint` | dev | 10.15.0 | 10.15.0 | **10.15.0** | unchanged |
| 32 | `eslint-plugin-react-hooks` | dev | 7.1.1 | 7.1.1 | **7.1.1** | unchanged |
| 33 | `jest` | dev | 30.5.2 | 30.5.2 | **30.5.2** | unchanged |
| 34 | `jest-expo` | dev | 57.0.6 | 57.0.6 | **58.0.15** | MAJOR |
| 35 | `openapi-typescript` | dev | 7.13.0 | 7.13.0 | **7.13.0** | unchanged |
| 36 | `prettier` | dev | 3.10.1 | 3.10.1 | **3.10.1** | unchanged |
| 37 | `typescript` | dev | 6.0.3 | 6.0.3 | **6.0.3** | unchanged |
| 38 | `typescript-eslint` | dev | 8.74.0 | 8.74.0 | **8.74.0** | unchanged |

No package is added to or removed from `package.json`. 24 packages change and 14 stay the same: the project-owned libraries (zod, zustand, react-query, testing-library, eslint, prettier, typescript, jest, openapi-typescript, `@types/react`) and the two Expo-table packages that do not move (`@react-native-async-storage/async-storage`, `react-native-web`).

### 3.2 Transitive dependencies that matter (not in `package.json`; resolved by npm)
| Package | Now | SDK 58 preview | Why it matters |
|---|---|---|---|
| `@expo/cli` | 57.0.35 | 58.1.21 | Expo CLI used by `expo start`/`export` |
| `babel-preset-expo` | 57.0.17 | 58.0.26 | Babel preset used by Metro and Jest |
| `@expo/metro-config` | 57.0.14 | 58.0.23 |  |
| `metro` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 | bundler |
| `metro-config` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |  |
| `metro-runtime` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |  |
| `hermes-compiler` | 250829098.0.17 | 260318099.0.5 |  |
| `@react-native/babel-preset` | 0.86.3 | 0.88.1 |  |
| `@react-native/codegen` | 0.86.3 | 0.88.1 |  |
| `@react-native/jest-preset` | 0.86.3 | 0.88.1 | peer of `jest-expo`; installed automatically as in SDK 57, not a new direct dependency |
| `@react-native/gradle-plugin` | 0.86.3 | 0.88.1 |  |
| `@react-native/normalize-colors` | 0.74.89, 0.86.3 | 0.74.89, 0.88.1 |  |
| `@react-native/virtualized-lists` | 0.86.3 | 0.88.1 |  |
| `expo-asset` | 57.0.22 | 58.0.21 |  |
| `expo-file-system` | 57.0.7 | 58.0.14 |  |
| `expo-keep-awake` | 57.0.2 | 58.0.4 |  |
| `expo-modules-core` | 57.0.26 | 58.1.0 | native module runtime |
| `expo-modules-autolinking` | 57.0.18 | 58.1.0 |  |
| `@expo/devtools` | 57.0.1 | 58.0.2 |  |
| `@expo/dom-webview` | 57.0.1 | 58.0.2 |  |
| `@expo/log-box` | 57.0.5 | 58.0.15 |  |
| `@expo/fingerprint` | 0.20.17 | 0.21.10 |  |
| `@expo/config` | 57.0.12 | 58.0.12 |  |
| `@expo/config-plugins` | 57.0.13 | 58.0.13 |  |
| `@expo/prebuild-config` | 57.0.21 | 58.0.20 |  |
| `scheduler` | 0.27.0, 0.28.0 | 0.28.0 |  |
| `react-test-renderer` | 19.2.3 | (removed) | removed |
| `test-renderer` | 1.3.1 | 1.3.0, 1.3.1 | used by @testing-library/react-native |
| `jest-watch-typeahead` | 2.2.1 | 3.0.1 | new direct dependency of jest-expo; source of +1 audit count |
| `jsdom` | 20.0.3 | 26.1.0 | jest-environment-jsdom |
| `@jest/core` | 29.7.0, 30.5.2 | 30.5.2 |  |
| `glob` | 10.5.0, 13.0.7, 14.0.6, 7.2.3 | 10.5.0, 13.0.7, 7.2.3 | existing advisory (dev tooling) |
| `@egjs/hammerjs` | 2.0.17 | (removed) | removed with gesture-handler 3 |
| `@expo/ui` | 57.0.26 | (removed) | removed |

### 3.3 Transitive summary and supply-chain effects (details in the diff file)
- By package name: **126 changed, 34 added, 73 removed**. Lockfile entries (nested duplicates counted per path): 1,407 now, 1,263 in the preview.
- **No new install-script package.** Packages with install scripts stay exactly `@parcel/watcher` and `fsevents` (`ignore-scripts=true` stays).
- **New transitive names to review before approval** (all MIT per the resolved lockfile except where noted; I checked the registry entries of `noxcturnal` and `2g` only, which share the maintainer `philpl` and have repositories `expo/noxcturnal` and `kitten/2g`; the other names are not individually verified): `noxcturnal` 0.1.14 plus eight platform binary packages `@noxcturnal/*` (native-backed JavaScript transform runtime, required by `@expo/metro-config` 58.0.23; repository `github.com/expo/noxcturnal`), `2g` 1.0.1 (structured event-log utility, required by `@expo/metro-config`, `@expo/metro-file-map`, `@expo/cli`; has a `bin`), `expo-modules-cli`, `expo-modules-macros` and four `@expo/modules-macros-*` binary packages, `@oxc-project/types`, `flow-parser`/`flow-estree`, `tldts`, `rrweb-cssom`, and the `@csstools/*` and `@asamuzakjp/css-color` family (from the jsdom 26 line; `@csstools/color-helpers` is MIT-0, which will need a **licence-gate review**: it is permissive, but not on the current allow-list unless an exception is added). The platform binary packages are optional dependencies that install only the matching OS/CPU build; none has an install script.
- **Removed with the upgrade:** `@egjs/hammerjs` and `@types/hammerjs` (gesture-handler 3), `@expo/ui`, `react-test-renderer`, `@radix-ui/*`, `vaul`, `chrome-launcher`, `url-parse`, `query-string`, `yaml` and others (see diff file D).
- **Audit:** `npm audit --package-lock-only` on the preview reports **22 moderate, 0 high, 0 critical** (now: 20 moderate). The same two underlying advisories (`glob` GHSA-3g3m-2fjr-6pw4 and `minimatch` GHSA-g3hv-75wg-m249, both Node-side dev tooling) are counted in two more packages because `jest-expo` 58 now depends on `jest-watch-typeahead`, which reaches `jest`. **No new advisory and no new vulnerable package**, but the gate counts affected packages, so as written the gate would fail at 22 > ratchet 20. See decision 3 in section 10.
- **Resolution finding:** with the existing lockfile, `npm install` with the new pins stops with `ERESOLVE`: `jest-expo@58.0.15` peers `@react-native/jest-preset@^0.88.1` while the lockfile holds 0.86.3 (a peer auto-installed under SDK 57, not a direct dependency). Re-resolving that one entry succeeded in the scratch copy and `@react-native/jest-preset` stays an automatically installed peer, **not a new direct dependency**. If implementation hits the same conflict, I will resolve it the same way and report it; I will not add `--force`/`--legacy-peer-deps` or a new direct dependency without your approval.

## 4. Known risks (what I expect to break, and how I will check)
1. **React Native 0.86 -> 0.88** (two minors): possible API removals or changed defaults. Check by typecheck, lint, the full Jest suite and the web export.
2. **gesture-handler 2 -> 3 (major):** the app does not import it directly (`grep` over `src`, `app`, `babel.config.js`, `app.json` is empty); it is reached through expo-router. Check navigation/accessibility tests and the web E2E.
3. **reanimated 4.5 -> 4.7 and worklets 0.10 -> 0.13:** babel/worklets plugin pairing. Check that Jest, the iOS bundle export and the web export all build.
4. **`expo/fetch` native redirect behaviour:** my M8.2 conclusion (iOS delegate refuses redirects, Android `followRedirects(false)`) came from SDK 57 source. I will re-read the SDK 58 native sources and update the notes, but **source reading still is not evidence**: only the device canary can show it.
5. **Docs that name SDK 57:** `docs/mobile/DEPENDENCY_REGISTER.md`, `DEPENDENCY_AUDIT.md` and any "Expo 57" mention are updated. `scripts/check-sdk-alignment.mjs` reads versions from the installed `expo` package, so it needs no code change; I will confirm that rather than assume it.
6. **Supply-chain ratchets:** the preview shows the audit count rising 20 -> 22 (section 3.3) and one new licence value (`MIT-0`). Neither gate is changed without your approval (decision 3 and 4). No new install-script package appears in the preview; if one appears at implementation time it is reported, not accepted (`ignore-scripts=true` stays).
7. **Screenshots and the E2E harness:** web renders may shift pixel-level; I will regenerate and check them, not hand-edit.

## 5. Method (only after approval)
1. Work on the current branch in small commits: (a) dependency pins + lockfile, (b) code fixes forced by the bump, (c) tooling/docs, (d) re-verification evidence.
2. Install with the existing flags (`ignore-scripts=true`); use `npm ci`-reproducible lockfile; use `npx expo install --check`-equivalent validation through the repo's `check:sdk` script, updated to SDK 58.
3. Fix only what fails, with the minimal change. If a fix would alter behaviour or UI, stop and ask.
4. Keep every M8.1/M8.2 security control: transport module, lint/CI bypass checks, native gate false, Keychain-only refresh token, allowlist, redirect controls, no persistence of question text.

## 6. Verification (reported separately, exactly as for M8.2)
1. **Clean-checkout `npm ci` (new, mandatory).** On the final upgrade commit, in an empty directory and Node 22.x (the version CI uses): `git clone --branch claude/charming-davinci-vqv0du <repo> clean && cd clean/mobile && npm ci`. Pass criteria: exit code 0; `npm ci` reports no lockfile/`package.json` mismatch; `git status --short` is empty afterwards (the lockfile is not rewritten); the installed tree matches section 3.1 exactly (`npm ls` of every direct package equals its target version, and `npm ls --all` shows no `invalid` or `extraneous` entry). Then `npm run check:all` runs in that clean tree, not only in my working tree. The GitHub Actions `mobile` workflow performs the same `npm ci` on its own clean checkout and is cited separately. Your Windows PC (`git pull` and `npm ci` on Node 22) is a third confirmation that I ask you to run.
2. **SDK compatibility check (new, mandatory).**
   - `npm run check:sdk` (already in `check:all`): every runtime dependency matches `node_modules/expo/bundledNativeModules.json` for SDK 58 (exact for unranged entries, same major.minor for `~` entries), and no SDK package resolves to another major line in the lockfile.
   - `npx expo install --check` using the project's own Expo CLI: it must report that dependencies are compatible with SDK 58 (or list only deliberate, documented exceptions).
   - `npx expo-doctor` is **report-only** (it is fetched by `npx`, not added as a dependency; if you prefer I skip it, say so). Its findings are listed in the report and do not replace the two checks above.
   - The compatibility results are reported next to the Expo Go version actually used (SDK 58.0.0 as seen on your iPhone).
3. Local: `tsc`, ESLint, Prettier, `npm run check:all` (445+ tests, coverage thresholds unchanged), API-type consistency, licence/install-script/audit/transport/secret gates; iOS (Hermes) and web exports build.
4. Contract tests vs the synthetic backend through `contract-backend.sh run` (13/13 expected).
5. Playwright web E2E (34 checks, Light and Dark, no foreign hosts) and regenerated screenshots.
6. Native gate tests: enrollment, refresh and authenticated requests still blocked on native; gate constant still `false`.
7. GitHub Actions `mobile` workflow on a clean checkout.
8. **Device:** after the upgrade, Expo Go (SDK 58) must open the app in demo mode on your iPhone. That is a user step; success there is **not** redirect evidence.
9. Report: `docs/mobile/reports/SDK58_UPGRADE.md` with commit SHAs, the package diff actually installed, changed files, test/coverage numbers, CI link, gate results, known limitations. Then **stop**.

## 7. What happens after the upgrade
- You re-run the redirect canary on the iPhone per `docs/mobile/REDIRECT_CANARY.md` (already tested up to the point of opening the app: Windows setup, Node 22, firewall rule, canary start and LAN address worked). The canary instructions get one change: Expo Go must be the SDK 58 build, which is what the App Store provides now.
- The gate flips only in a later, separate commit after you and I review PASS evidence on all four statuses and you explicitly approve. Nothing in this plan does that.
- If the canary shows FAIL or INCONCLUSIVE, the native gate stays closed and we decide the next step (for example, a thin custom transport or a development build).

## 8. Risks I am not able to remove
- A device result for SDK 58 does not retroactively describe SDK 57, and a later SDK bump invalidates it (the canary must be re-run after any Expo SDK or `react-native` upgrade, as already documented).
- Expo Go is a development client; its network stack may differ from a production build. The canary evidence is for Expo Go on the tested iOS version only, and the report will say so.
- If SDK 58 has regressions on a package we do not exercise in tests, they can show up on the device first.

## 9. Rollback
All work is in separate commits on the branch. If the upgrade cannot be made green within a reasonable change set, I stop, report what failed, and the branch can return to `bf0213a` (SDK 57, CI green) by reverting or by not merging the upgrade commits. The M8.2 state stays intact.

## 10. Decisions I need from you
1. **Approve upgrading to SDK 58** at exactly the versions in section 3.1 (Expo `58.0.26`), as version bumps of existing dependencies only. No package is added to `package.json`.
2. **Accept the gesture-handler 2 -> 3 major bump** (indirect; the app does not import it) or tell me to stop if it breaks anything.
3. **Audit ratchet.** The preview counts 22 moderate vs ratchet 20, with no new advisory (section 3.3). Options: (a) approve raising `audit-baseline.json` `maxModerate` from 20 to 22 with a documented reason in `DEPENDENCY_AUDIT.md` (same two dev-tooling advisories, counted in two more packages); (b) tell me to stop and find another resolution (for example overrides), with the risk that SDK 58 compatibility is broken. Default if you do not choose: I stop at the gate and report, and I do not change the ratchet.
4. **Licence gate.** Approve adding `@csstools/color-helpers` (MIT-0, via jsdom 26 -> `@asamuzakjp/css-color`) as a documented exception if the preview still contains it at implementation time. Default: I stop and report.
5. **New transitive names** listed in section 3.3 (`noxcturnal` and its binaries, `2g`, `expo-modules-cli`, `expo-modules-macros`, and the others): acknowledge before implementation, or tell me which ones you want investigated further first. They are required by `expo@58.0.26`, MIT, with no install scripts.
6. **Confirm the sequencing:** upgrade, review, then you run the canary; no flipping the gate in this task.
7. Optional: I can write a tiny throwaway SDK 58 probe app for an early look at redirect behaviour, but I recommend against it as a substitute (it would not be the shipped code).
