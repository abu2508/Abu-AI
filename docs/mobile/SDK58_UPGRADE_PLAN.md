# Plan: upgrade the mobile app from Expo SDK 57 to SDK 58

Status: **PLAN ONLY (revision 3: audit, licence and supply-chain investigations added, section 11). Nothing in the repository has been changed. Waiting for your approval of the plan and its security decisions.**
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
- **New transitive names:** `noxcturnal` plus eight platform binary packages `@noxcturnal/*`, `2g`, `expo-modules-cli`, `expo-modules-macros` plus four `@expo/modules-macros-*` binary packages, `@oxc-project/types`, `flow-parser`/`flow-estree`, `tldts`, `rrweb-cssom`, and the `@csstools/*` and `@asamuzakjp/css-color` family (jsdom 26 test-environment chain). **Focused supply-chain review: section 11.3. Licence: section 11.2 (revision 2 wrongly said `MIT-0` needed an exception; it is already on the gate's allow-list).** All are MIT per the resolved lockfile except `@csstools/color-helpers` (MIT-0). None has an install script.
- **Removed with the upgrade:** `@egjs/hammerjs` and `@types/hammerjs` (gesture-handler 3), `@expo/ui`, `react-test-renderer`, `@radix-ui/*`, `vaul`, `chrome-launcher`, `url-parse`, `query-string`, `yaml` and others (see diff file D).
- **Audit:** the unmodified preview reports **22 moderate, 0 high, 0 critical** (now 20). Exact advisories, dependency paths, the candidate overrides and their compatibility test results are in section 11.1. A compatible resolution that brings the count to 0 exists; the ratchet stays at 20 and is not touched.
- **Resolution finding:** with the existing lockfile, `npm install` with the new pins stops with `ERESOLVE`: `jest-expo@58.0.15` peers `@react-native/jest-preset@^0.88.1` while the lockfile holds 0.86.3 (a peer auto-installed under SDK 57, not a direct dependency). Re-resolving that one entry succeeded in the scratch copy and `@react-native/jest-preset` stays an automatically installed peer, **not a new direct dependency**. If implementation hits the same conflict, I will resolve it the same way and report it; I will not add `--force`/`--legacy-peer-deps` or a new direct dependency without your approval.

## 4. Known risks (what I expect to break, and how I will check)
1. **React Native 0.86 -> 0.88** (two minors): possible API removals or changed defaults. Check by typecheck, lint, the full Jest suite and the web export.
2. **gesture-handler 2 -> 3 (major):** the app does not import it directly (`grep` over `src`, `app`, `babel.config.js`, `app.json` is empty); it is reached through expo-router. Check navigation/accessibility tests and the web E2E.
3. **reanimated 4.5 -> 4.7 and worklets 0.10 -> 0.13:** babel/worklets plugin pairing. Check that Jest, the iOS bundle export and the web export all build.
4. **`expo/fetch` native redirect behaviour:** my M8.2 conclusion (iOS delegate refuses redirects, Android `followRedirects(false)`) came from SDK 57 source. I will re-read the SDK 58 native sources and update the notes, but **source reading still is not evidence**: only the device canary can show it.
5. **Docs that name SDK 57:** `docs/mobile/DEPENDENCY_REGISTER.md`, `DEPENDENCY_AUDIT.md` and any "Expo 57" mention are updated. `scripts/check-sdk-alignment.mjs` reads versions from the installed `expo` package, so it needs no code change; I will confirm that rather than assume it.
6. **Supply-chain ratchets:** the preview shows the audit count rising 20 -> 22 (section 3.3) and one new licence value (`MIT-0`). Neither gate is changed without your approval (decision 3 and 4). No new install-script package appears in the preview; if one appears at implementation time it is reported, not accepted (`ignore-scripts=true` stays).
7. **Compile and test breaks found in the scratch run (section 11.4):** `tsc` fails with 45 errors on the SDK 58 tree because the project relied on `@types/node` being loaded implicitly (it is not declared; SDK 58 no longer pulls it in through a type import), and the custom tab bar (`app/(tabs)/_layout.tsx`, `src/design/components/TabBar.tsx`) breaks because SDK 58 no longer passes `navigation` to custom tab bars (`navigateToTab` replaces it): 3 Jest tests fail on an otherwise unmodified project (442/445 pass; one diagnosed as the tab bar, two not yet diagnosed).
8. **Screenshots and the E2E harness:** web renders may shift pixel-level; I will regenerate and check them, not hand-edit.

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
9. **Overrides (if approved):** `npm ls --all` shows no `invalid`/`extraneous`/`missing`; `npm audit` reports the count recorded in section 11.1; the committed `package-lock.json` is exactly what npm generated (no hand edits: `npm ci` succeeds and `git diff --exit-code package-lock.json` is clean after it); every override is documented in `DEPENDENCY_AUDIT.md` with its reason and a removal condition.
10. **Supply chain (new packages):** `npm audit signatures` on the clean checkout; if the whole-tree run fails for an unrelated package (it did in my sandbox: missing attestation key for `@react-native/dev-middleware`), run it on the section 11.3 packages in an isolated project and report both results; confirm `experiments.noxcturnalTransformWorker` is not set in `app.json`/`app.config.js`; confirm the lockfile integrity of the 16 section 11.3 packages equals the values recorded in section 11.3.
11. Report: `docs/mobile/reports/SDK58_UPGRADE.md` with commit SHAs, the package diff actually installed, changed files, test/coverage numbers, CI link, gate results, known limitations. Then **stop**.

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

## 10. Decisions I need from you (revision 3)
Nothing below has been done. `audit-baseline.json` (`maxModerate` 20), the licence allow-list and the exceptions list are untouched and stay untouched unless you say otherwise.
1. **Approve the upgrade to SDK 58** at exactly the versions in section 3.1 (Expo `58.0.26`), version bumps of existing dependencies only.
2. **Accept the gesture-handler 2 -> 3 major bump** (indirect; the app does not import it).
3. **Audit: choose how to hold the ratchet at 20** (section 11.1). Recommended: approve the five scoped `overrides` of section 11.1.B (`glob` -> 14.0.6 and `minimatch` -> 11.0.9 for the vulnerable dev-tooling consumers only). Result in scratch: 0 advisories, identical test and coverage results to the unmodified preview. Rejected by me: option A (pin `jest-watch-typeahead` to 2.2.1: it only hides the count, advisories stay, and it contradicts `jest-expo`'s declared range). If you decline B, no safe alternative remains, and **the upgrade is blocked by the audit gate**.
4. **Licence:** no exception is needed (section 11.2). Approve recording `MIT-0` as an accepted licence in `DEPENDENCY_AUDIT.md` section 3 (documentation only, no code change) so the written policy matches the gate that already allows it.
5. **New transitive packages** (section 11.3): decide whether to accept them as a consequence of `expo@58.0.26`. My assessment is conditional acceptance with the safeguards listed there; the main residual points are that `noxcturnal` and its eight binaries have **no provenance attestation** and a **single maintainer**, and that its use is **opt-in** (not loaded in an iOS export) in the project's configuration. I do not call it fully assured.
6. **`@types/node` (section 11.4):** the project needs Node type definitions to typecheck on SDK 58. Choose (a) add `@types/node` as an explicit devDependency at the version already in the tree (`26.6.6`, MIT): this is a **new direct dependency** and needs your approval; or (b) a `tsconfig.json` change that points TypeScript at the already-installed types (no new dependency, but it relies on the transitive copy staying installed). Recommended: (a). Default if you do not choose: I stop at the typecheck and report.
7. **Tab bar change (section 11.4):** approve the minimal code change needed to follow SDK 58's custom tab bar API, with no visual or behaviour change, covered by the existing navigation tests.
8. **Confirm the sequencing:** upgrade, review, then you run the canary; no flipping the gate in this task.
9. Optional: a throwaway SDK 58 probe app for an early redirect look (not recommended as a substitute).

## 11. Pre-implementation investigations (revision 3)
Method: documentation and scratch-copy work only, in a directory outside the repository (`package.json`, `package-lock.json`, `audit-baseline.json`, the licence allow-list and application code were not modified). The preview trees were installed with `--ignore-scripts` on Node 22.23.5, and the project's own files (`app`, `src`, `tests`, scripts, configs) were copied in to run its tests. Clearly labelled **verified** (observed in a command I ran) or **unverified**.

### 11.1 Audit ratchet (keep `maxModerate` at 20)
**Exact advisories (the only two root causes; both moderate, both in Node-side dev/CLI tooling):**
- `glob`: GHSA-3g3m-2fjr-6pw4 "AbortSignal cancellation bypass allows post-timeout filesystem traversal" (CWE-400); affected `>=9.0.0 <=14.0.1`; fixed in 14.0.2+.
- `minimatch`: GHSA-g3hv-75wg-m249 "potential denial of service via globparts amplification" (CWE-405); affected `>=7.0.0 <11.0.1`; fixed in 11.0.1+.

**Affected installed copies (identical set to SDK 57 today): `glob` 10.5.0 and 13.0.7; `minimatch` 9.0.9 and 10.2.6.** Dependency paths (shortest from the root; every one runs through Jest or the Expo CLI):
| Vulnerable copy | Path |
|---|---|
| `glob@10.5.0` | `test-exclude@7.0.2` < `babel-plugin-istanbul@8.0.0` < `@jest/transform` / `babel-jest@30.5.2` < `jest@30.5.2` and `jest-expo@58.0.15` |
| `glob@13.0.7` | `@jest/reporters`, `jest-config`, `jest-runtime` (all 30.5.2, each requires `glob ^13.0.6`) < `@jest/core` < `jest@30.5.2` |
| `minimatch@9.0.9` | `glob@10.5.0` (requires `^9.0.4`) < the `test-exclude` path above |
| `minimatch@10.2.6` | `glob@13.0.7`, `test-exclude@7.0.2` (`^10.2.2`), and `@expo/fingerprint@0.21.10` (`^10.2.6`) < `expo@58.0.26` |
Root: our own pinned devDependency `jest` 30.5.2 (the latest 30.x on the registry; upstream has not shipped a fix inside the ranges Jest requires) and `expo`'s `@expo/fingerprint`.

**Why the count goes 20 -> 22 (verified):** the vulnerable packages are the same. `jest-expo@58` newly depends on `jest-watch-typeahead@^3`, whose peer `jest ^30` matches our Jest, so `npm audit` also counts `jest-watch-typeahead` and `jest-expo` as affected through the `jest` chain. (In SDK 57 `jest-watch-typeahead@2.2.1` peers `jest ^27-29`, so it was not counted.)

**Option A: override `jest-watch-typeahead` to 2.2.1. Rejected.** Count returns to 20, tests identical to baseline, but `2.2.1` declares support only for Jest 27-29 while the project runs Jest 30 and `jest-expo@58` declares `^3.0.1`: the count drops only because `npm audit` ignores an out-of-range peer. The advisories remain. That is hiding a count, not fixing it.

**Option B: scoped overrides that move only the vulnerable consumers to patched releases. Recommended candidate.**
```json
"overrides": {
  "decode-uri-component": "0.5.1",
  "openapi-typescript": { "typescript": "$typescript" },
  "test-exclude@7.0.2": { "glob": "14.0.6", "minimatch": "11.0.9" },
  "@jest/reporters": { "glob": "14.0.6" },
  "jest-config": { "glob": "14.0.6" },
  "jest-runtime": { "glob": "14.0.6" },
  "@expo/fingerprint": { "minimatch": "11.0.9" }
}
```
(the first two are the project's existing overrides; the other five are the proposal). Verified results on the SDK 58 preview plus the project's files, scratch copy:
- `npm audit`: **found 0 vulnerabilities** (`--package-lock-only` and a real install both).
- `npm ci --ignore-scripts` succeeds; `npm ls --all`: no `invalid`, `extraneous` or `missing`.
- Jest with coverage: **442 pass, 3 fail, identical to the same project on the no-override preview** (same 3 tests, section 11.4); coverage figures identical (96.88 / 91.32 / 94.16 / 97.78). Coverage runs `babel-plugin-istanbul` -> `test-exclude` -> `glob`/`minimatch`, the exact path being overridden.
- ESLint: exit 0 (ESLint 10 itself requires `minimatch ^11` already). `expo export --platform web`: succeeds. `expo export --platform ios` (Hermes): succeeds, 3.7 MB bundle.
- Not tested: contract tests, Playwright E2E, `jest --watch`, GitHub Actions. These are in the verification list for the real run.
**Compatibility risks of B:**
1. It crosses major versions for dev tooling: `glob` 10 and 13 -> 14, `minimatch` 9 and 10 -> 11, outside the ranges the consumers declare. Evidence so far is tests passing, not upstream support.
2. Node engines: `glob@14` needs Node `18 || 20 || >=22`, `minimatch@11` needs `20 || >=22`. CI and the local setup use Node 22; Node 18 would no longer work for `npm run test` on `minimatch`.
3. Overrides must be maintained: remove them when Jest/`test-exclude`/`@expo/fingerprint` ship releases that depend on patched versions; each SDK upgrade re-checks them (documented removal condition in `DEPENDENCY_AUDIT.md`).
4. I deliberately scoped the selector to `test-exclude@7.0.2`: my first, unscoped `test-exclude` override also forced `glob@14` onto `test-exclude@6.0.0`, which requires `glob ^7` and `minimatch ^3` (not affected by the advisories) and would have been a genuine incompatibility. `test-exclude@6.0.0` keeps `glob@7.2.3` and `minimatch@3.1.5`.
5. **Lockfile generation:** with the existing lockfile, `npm install` did not re-resolve all nested `glob`/`minimatch` entries by itself, and my hand-edited lockfile left `test-exclude@6.0.0` pointing at `glob@14`. I discarded that result. The tested resolution was generated entirely by npm from `package.json` with the overrides (no hand edits). It differs from the in-place preview in 7 package names, all in the removed `glob@10` subtree (`path-scurry`, `string-width`, `wrap-ansi`, `ansi-styles`, `emoji-regex`, plus `glob`, `minimatch`); 9 packages (`@isaacs/cliui`, `jackspeak`, `foreground-child`, `package-json-from-dist`, `@pkgjs/parseargs`, `eastasianwidth`, `string-width-cjs`, `strip-ansi-cjs`, `wrap-ansi-cjs`) drop out. Implementation rule: the committed lockfile must be npm-generated, never hand-edited; if an in-place update cannot reach the tested state, regenerate the SDK 58 lock with npm, review the diff, and report it.
**Conclusion:** not blocked. A safe compatible resolution exists (B) at the cost of five scoped overrides. If you do not approve overrides, the only remaining ways to keep the ratchet at 20 are option A (rejected) or raising the ratchet (not allowed by you); **in that case the SDK 58 upgrade is blocked by the audit gate.**

### 11.2 `@csstools/color-helpers` and `MIT-0`
Verified facts: version 5.1.0 in the resolved tree; `package.json` `license: "MIT-0"`; repository `csstools/postcss-plugins` (`packages/color-helpers`). Path: `@csstools/color-helpers@5.1.0` < `@csstools/css-color-parser@3.1.0` < `@asamuzakjp/css-color@3.2.0` < `cssstyle@4.6.0` < `jsdom@26.1.0` < `jest-environment-jsdom@30.5.2` < `jest-expo@58.0.15`: **a test-environment chain only**. Full licence text shipped in the package (`LICENSE.md`):
> MIT No Attribution (MIT-0). Copyright © CSSTools Contributors. Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND ... (standard MIT warranty disclaimer and limitation of liability).
Implications: it is the MIT licence **without** the requirement to include the copyright and permission notice in copies or substantial portions. No copyleft, no field-of-use limits, no attribution obligation, no patent grant (silent, as MIT). It is an OSI-approved SPDX licence and is strictly more permissive than MIT, so it is compatible with everything on the existing allow-list. This is a technical reading, not legal advice.
**Correction of revision 2:** `MIT-0` is **already** in `ALLOWED` in `mobile/scripts/check-licenses.mjs` (present since the M8.0 commit `7524a0d`). I ran the repository's own gate on both preview trees: `Licence gate OK: 1263 packages, 13 covered by documented exceptions` (in-place preview) and `1195 packages, 13` (override resolution); the gate passes without any new exception, and the exception list is not extended. The only gap is documentation drift: `DEPENDENCY_AUDIT.md` section 3 lists the allow-list without `MIT-0` and says a new licence fails the build until approved; no package in today's lockfile uses `MIT-0`.
**Recommendation: no exception is justified because none is needed.** Approve `MIT-0` as an accepted licence class, recorded in `DEPENDENCY_AUDIT.md` (documentation only). I did not add anything.

### 11.3 Focused supply-chain review of the new transitive packages
Scope: `noxcturnal@0.1.14` and its 8 platform binaries, `2g@1.0.1`, `expo-modules-cli@0.5.0`, `expo-modules-macros@0.33.0` and its 4 platform binaries (16 packages).
**Dependency paths (verified from the lockfile; the only dependents):**
- `noxcturnal` < `@expo/metro-config@58.0.23` < `expo@58.0.26`; binaries are `optionalDependencies` of `noxcturnal` (installed only on the matching OS/CPU/libc).
- `2g` < `@expo/cli@58.1.21`, `@expo/metro-config@58.0.23`, `@expo/metro-file-map@58.0.6` < `expo`.
- `expo-modules-cli` < `expo@58.0.26`. `expo-modules-macros` < `expo-modules-core@58.1.0` < `expo`; its 4 binaries are `optionalDependencies`.
**Verified facts (commands I ran):**
- **Integrity:** the `integrity` hash in the resolved lockfile equals the registry's `dist.integrity` for all 16 packages.
- **Registry signatures:** `npm audit signatures` verified registry signatures for all of them (all 8 `noxcturnal` binaries and all 4 macros binaries, each by installing with `--os/--cpu/--libc` for its platform; in-tree 15 of 15 on Linux x64).
- **Provenance attestations** (npm verified the signatures and I read the statements): present for `2g@1.0.1` (source `github.com/kitten/2g`, `release.yml`, `main`), `expo-modules-cli@0.5.0` (source `github.com/expo/expo`, `publish-packages.yml`, branch `sdk-58`), `expo-modules-macros@0.33.0` and its binaries (source `github.com/expo/expo-modules-macros`, `publish.yml`, `main`), all built on GitHub-hosted runners. **Absent for `noxcturnal@0.1.14` and all eight `@noxcturnal/*` packages (registry returns 404 for their attestations),** although they are published by a GitHub Actions identity. I therefore cannot cryptographically tie these tarballs to a source commit.
- **Publisher and age:** `noxcturnal`: single maintainer `philpl`, package created 2026-08-03 (about 16 weeks), 23 versions (first ones canary builds), version 0.1.14 published 2026-11-16; repository field `github.com/expo/noxcturnal`. `2g`: single maintainer `philpl`, 55 versions, repository `kitten/2g`. `expo-modules-cli` (created 2026-09-18, 10 versions) and `expo-modules-macros` (created 2026-04-30, 34 versions): 13 maintainers (Expo organisation). Last-month downloads: `noxcturnal` 21.4M, `2g` 21.0M, `expo-modules-cli` 18.8M, `expo-modules-macros` 21.4M (so they are already widely installed via Expo; this is not evidence of safety).
- **Install scripts:** none of the 16 has a `preinstall`/`install`/`postinstall`/`prepare` script; `noxcturnal` has build/test/release scripts only, `expo-modules-macros` has `prepublishOnly` only, `expo-modules-cli` has lint/test/build scripts. The lockfile `hasInstallScript` set stays `@parcel/watcher` and `fsevents`.
- **Executable/native content (inspected):** `noxcturnal` itself is JavaScript (about 280 KB); it requires only `node:crypto`, `node:path` and `@babel/code-frame`, and loads the matching native addon `noxcturnal.<platform>.node` (an 8-11 MB Rust-built Node-API addon, `oxc`-based transformer) from the package directory or `@noxcturnal/noxcturnal-<platform>`. No `child_process`, no `net`, no network URL in its JavaScript (the `exec(` matches are regular-expression calls). `2g` JavaScript: `node:net` used for local IPC (a socket in the temporary directory, a named pipe on Windows), `child_process.spawn` in its CLI wrapper, no hostname or TCP address found; it writes JSONL session logs. `expo-modules-cli` and `expo-modules-macros` JavaScript launch the platform's `ExpoModulesMacros` binary via `spawn`/`execFile`. `expo-modules-macros` also ships Swift sources and a 18 MB macOS universal Mach-O (`apple/ExpoModulesMacros`) in every install.
- **Binary static analysis (not execution):** Linux x64 `noxcturnal` addon: imports only libc/N-API functions; of the network/process/loader functions I searched for (`socket`, `connect`, `getaddrinfo`, `exec*`, `fork`, `posix_spawn`, `system`, `dlopen`, ...) only `dlsym` and `getenv` are imported; embedded URLs are documentation links (`oxc.rs`, `react.dev`). Windows x64 `noxcturnal` addon: no Winsock/WinHTTP/WinInet DLLs; imports `LoadLibraryA`/`LoadLibraryExW`/`IsDebuggerPresent`. Linux x64 `ExpoModulesMacros`: imports `chmod`, `mkdir`, `rename`, `unlink`, `dlopen`, `getenv`; no socket or exec imports; only URL is an Apple plist DTD. Windows x64 `ExpoModulesMacros.exe`: imports `ws2_32.dll`, `secur32.dll`, `CreateProcessW`, `OpenProcessToken` (consistent with a Swift runtime; I did not determine whether these are reachable).
- **Observed runtime behaviour (iOS export with `NODE_OPTIONS` module-load tracing, Linux x64, this app's configuration):** `2g` is loaded on every Expo CLI run (by `@expo/cli`); `noxcturnal`, `@noxcturnal/*`, `expo-modules-cli`, `expo-modules-macros`, `@expo/modules-macros-*` were **not loaded** and **no process was spawned** for them. Reading the code: Expo SDK 58 loads `noxcturnal` only when `exp.experiments.noxcturnalTransformWorker` is truthy in the app config (`@expo/cli` `instantiateMetro.js`; `@expo/metro-config` `metro-transform-worker.js` gates on `unstable_noxcturnalTransformWorker`). The repository's `app.json`/`app.config.js` contain no `experiments` key.
- **`2g` output (inspected one run):** it writes JSONL session logs under `/tmp/event-log-<uid>/<session>/` (directories mode 0700); the sample contained build-configuration events and environment-variable **names only** (none listed in that run), and no line mentioned token/secret/password words.
**Unverified (not claimed):**
1. That any published tarball corresponds to its repository source; `noxcturnal` has no provenance at all and I did not read its source repository (GitHub access is outside this session's scope).
2. Behaviour of the native binaries when executed: static import tables and strings are not a behavioural assessment, and a binary can make system calls without importing the names I searched for. I did not run any of them.
3. Contents of the other-platform binaries: only the Linux x64 and Windows x64 `noxcturnal`/macros binaries were inspected; macOS (`noxcturnal` arm64 contains the strings `_posix_spawn` and `_execve`; I did not determine whether they are imports), Linux arm64/musl, Windows arm64 and the Mach-O macros binary were verified for integrity and signature only.
4. Behaviour on your Windows PC (installs `noxcturnal-win32-x64-msvc` and `modules-macros-win32-x64`) and in GitHub Actions (Linux x64): the runtime trace was run on Linux only and only for `expo export --platform ios`; `expo start`, `jest` and a Windows run were not traced.
5. What `2g` logs in other commands or under other flags, and whether its logs can contain values of environment variables.
6. Whether anything in the Expo toolchain calls `expo-modules-cli`/`expo-modules-macros` in workflows I did not run (for example native builds).
**Assessment.** I cannot declare these packages secure. What the evidence supports: all 16 are signed by the registry and match our lockfile; none runs install scripts; the packages with a public tooling role (`expo-modules-*`, `2g`) have verifiable provenance to named GitHub repositories; `noxcturnal` is the weak point (no provenance, single maintainer, young package, native code that would run in-process as the bundler's transformer), but it is opt-in and not loaded in this app's configuration. The residual risk is a compromised or misconfigured `noxcturnal` release being enabled later, or a malicious update delivered through a future lockfile change.
**Proposed safeguards if you accept (no code written yet):** exact-version lock (already how the lockfile works); keep `ignore-scripts=true`; add a small test or `check:transport`-style check that `experiments.noxcturnalTransformWorker` is not set; run `npm audit signatures` in the clean-checkout verification (section 6, item 10); re-check provenance on any upgrade of these packages; record the integrity values of the 16 packages in the upgrade report so any later difference is visible.

### 11.4 Additional compatibility findings from the scratch run (not part of your three requests)
- **Typecheck fails: 45 errors.** 44 are the absence of Node type definitions (`node:crypto`, `node:path`, `Buffer`, `__dirname` ...) in `jest.setup.ts` and the tests. The project has no direct `@types/node`; on SDK 57 TypeScript loads the transitive `@types/node` copy through a type-import chain, and on SDK 58 it does not (the package is still installed, `26.6.6`, but nothing pulls it into the compilation). Decision 6 in section 10.
- **One real SDK change:** `app/(tabs)/_layout.tsx` fails with `Property 'navigation' is missing`; the custom tab bar receives `navigateToTab` instead of `navigation` in SDK 58, which makes `navigation.test.tsx` fail at runtime with `Cannot read properties of undefined (reading 'navigate')` in `src/design/components/TabBar.tsx` (diagnosed). The two failing `demo-mode.test.tsx` tests were **not individually diagnosed**; they are probably the same cause, but that is unverified. The minimal fix is in decision 7; visual and behavioural intent unchanged.
- **Everything else on the unmodified SDK 58 preview:** 442 of 445 tests pass, ESLint passes, the web export and the iOS Hermes export build.
- These results are from a scratch copy; none of them is a claim about the repository, which is unchanged and still passes on SDK 57.
