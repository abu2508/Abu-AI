# Plan: upgrade the mobile app from Expo SDK 57 to SDK 58

Status: **PLAN ONLY (revision 4: remaining compatibility checks, `@types/node`, tab bar, demo-mode and Windows/toolchain investigations added; scope and stale statements corrected; sections 2, 4, 10, 11). Nothing in the repository has been changed. Waiting for your final review and explicit implementation approval.**
Branch: `claude/charming-davinci-vqv0du` (base `bf0213af6c54d74b92bb3c83d694e8be2f7e4628`, M8.2 plus review follow-up, CI green).
Standing constraints are unchanged: ₹0–₹300 budget, mock model, synthetic data, four production gates BLOCKED, no real data, no secrets in the app, `NATIVE_AUTH_TRANSPORT_VERIFIED` stays `false`.

## 1. Why
The iPhone redirect canary cannot run. Expo Go from the App Store is for **SDK 58.0.0**; the app is on **SDK 57**. iOS cannot install an older Expo Go (the app's own error message says so), so Expo Go cannot open the project. The only way to get a physical-device answer with the current tooling, without a Mac, an Apple developer account or an EAS build, is to move the app to SDK 58. The device evidence must also describe the transport that will ship, so the upgrade has to come before the canary result means anything.

## 2. Scope
In scope: bump Expo-managed packages to the versions SDK 58 specifies; adapt code only where something breaks; update the documentation that names SDK 57 (the SDK-alignment script reads the installed Expo package and needs no change); re-run every gate; re-read the `expo/fetch` native redirect behaviour for SDK 58; update docs.

**Pending approval items.** These are the only departures from "bump existing dependencies and fix what breaks". None has been made; each is listed in section 10 for an explicit yes/no.
| # | Item | Kind | Why it is needed | Evidence |
|---|---|---|---|---|
| P1 | **Add `@types/node` 26.6.6 to `devDependencies` (new direct dependency)** | dependency | the project uses `node:*` modules, `Buffer` and `__dirname` in tests and `jest.setup.ts`; it never declared Node types and only got them through an accidental type-import chain that SDK 58 removes | 11.6 |
| P2 | `tsconfig.json`: add `"types": ["node"]` | config | declaring P1 alone does **not** fix the typecheck: TypeScript 6.0.3 no longer loads `@types/*` automatically | 11.6 |
| P3 | `@types/react` 19.2.18 -> 19.3.0 | existing devDependency, version bump | `expo install --check` for SDK 58 / React 19.3.0 reports it as outdated | 11.5 |
| P4 | Five scoped `overrides` (`glob` -> 14.0.6, `minimatch` -> 11.0.9 for the vulnerable dev-tooling consumers) | dependency policy | holds the audit at 0 (ratchet stays 20) | 11.1 |
| P5 | New lockfile-consistency guard script (`check:overrides`), wired into `check:all` and `.github/workflows/mobile.yml` | tooling, no new package | npm re-resolution of the P4 overrides can silently rewire `test-exclude@6.0.0` and **none of the current 12 CI steps detects it** | 11.1 |
| P6 | Tab bar migration in `src/design/components/TabBar.tsx` only (`navigation.navigate(name)` -> `navigateToTab(route.key)`) | code, no visual or behaviour change intended | SDK 58 removes `navigation` from custom tab bar props; fixes 3 failing tests and the one remaining type error | 11.7, 11.8 |

**Out of scope:** any feature or UI change; backend source, migrations or API contract; new packages **other than P1**; M8.3; B3b; flipping the native gate; production-gate work; enabling native authenticated connectivity; the canary run itself (a separate step you perform); fixing the SDK 58 `assistant` modal warning (section 4, risk 10); changing coverage thresholds, `audit-baseline.json` (`maxModerate` stays 20) or the licence allow-list.

## 3. Package-by-package dependency table
Source of the target versions: `bundledNativeModules.json` inside `expo@58.0.26` (current `latest`), then resolved in a **scratch copy** (repository `package.json` and `package-lock.json` untouched) with `npm install --package-lock-only --ignore-scripts` on Node 22. All "target" values are exact lockfile versions of that preview resolution. The complete transitive diff is in `docs/mobile/SDK58_DEPENDENCY_DIFF.md`; the lockfile committed during implementation is the authority, and the registry may have moved by then (any difference will be reported).

"Change" uses plain semver labels. For Expo-managed packages a "MAJOR" label mostly means "SDK 57 -> SDK 58 line" (their major number tracks the SDK), not necessarily a breaking API change; the genuinely independent major bump is `react-native-gesture-handler` 2 -> 3 (section 4, risk 2).

### 3.1 Direct dependencies (38 existing: 28 runtime, 10 dev; every entry is exact-pinned in `package.json`)
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
| 30 | `@types/react` | dev | 19.2.18 | 19.2.18 | **19.3.0** | minor (pending, P3: `expo install --check` for SDK 58) |
| 31 | `eslint` | dev | 10.15.0 | 10.15.0 | **10.15.0** | unchanged |
| 32 | `eslint-plugin-react-hooks` | dev | 7.1.1 | 7.1.1 | **7.1.1** | unchanged |
| 33 | `jest` | dev | 30.5.2 | 30.5.2 | **30.5.2** | unchanged |
| 34 | `jest-expo` | dev | 57.0.6 | 57.0.6 | **58.0.15** | MAJOR |
| 35 | `openapi-typescript` | dev | 7.13.0 | 7.13.0 | **7.13.0** | unchanged |
| 36 | `prettier` | dev | 3.10.1 | 3.10.1 | **3.10.1** | unchanged |
| 37 | `typescript` | dev | 6.0.3 | 6.0.3 | **6.0.3** | unchanged |
| 38 | `typescript-eslint` | dev | 8.74.0 | 8.74.0 | **8.74.0** | unchanged |

The 38 packages above are all existing direct dependencies: **25 change and 13 stay the same** (revision 3 said 24 and 14; the `@types/react` bump found by `expo install --check` is the 25th). **Pending approval, not in the table:** one **added** devDependency, `@types/node` 26.6.6 (P1), which would make 39 direct dependencies; it is already in the tree at that exact version as a transitive dependency, so the lockfile entry count does not change (section 11.6). No package is removed from `package.json`. The 13 unchanged are: the project-owned libraries (zod, zustand, react-query, testing-library, eslint, prettier, typescript, jest, openapi-typescript) and the two Expo-table packages that do not move (`@react-native-async-storage/async-storage`, `react-native-web`).

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
5. **Docs that name SDK 57:** `docs/mobile/DEPENDENCY_REGISTER.md`, `DEPENDENCY_AUDIT.md` and any "Expo 57" mention are updated. `scripts/check-sdk-alignment.mjs` reads versions from the installed `expo` package, so it needs no code change (confirmed: it passes on the SDK 58 scratch trees).
6. **Supply chain and licence (corrected in revision 4):**
   - **Audit:** the unmodified SDK 58 tree reports 22 moderate against the ratchet of 20 and fails the audit gate; with the P4 overrides it reports 0 (section 11.1). The ratchet is not changed. The overrides are fragile under `npm install`, hence P5.
   - **Licence:** **no gate change and no exception is needed.** `MIT-0` is already allow-listed in `check-licenses.mjs` (since M8.0); the licence gate passes on all SDK 58 trees (1,263 or 1,195 packages, 13 documented exceptions, one fewer than today). Only the written policy in `DEPENDENCY_AUDIT.md` needs the missing `MIT-0` mention (section 11.2).
   - **New transitive packages:** 16 reviewed in section 11.3 (`noxcturnal` and its 8 binaries, `2g`, `expo-modules-cli`, `expo-modules-macros` and its 4 binaries). All signed and matching our lockfile; none runs install scripts; `noxcturnal` has no provenance and one maintainer but is opt-in and not loaded in this configuration; `2g` is loaded on every CLI run. Not proven safe; residual risk stated there. No new install-script package; `ignore-scripts=true` stays.
7. **Compile and test breaks (fully diagnosed in revision 4, fixes verified in scratch only):** 45 `tsc` errors = 44 missing Node types (P1 + P2) + 1 tab bar type error (P6); 3 failing Jest tests = the tab bar (P6): one in `navigation.test.tsx` and **both** `demo-mode.test.tsx` failures, each diagnosed individually (11.8). With P1-P3 and P6 the proposed final tree passes all 12 CI steps (445/445 tests, coverage thresholds unchanged).
8. **Override fragility (new).** `npm install` in a tree that already has `node_modules` rewired `test-exclude@6.0.0` (needs `glob ^7.1.4`) to `glob@14.0.6` in my scratch runs; a clean generation does not. All 12 CI steps still pass on the drifted lockfile, so CI cannot see it today. Mitigation = P5 plus the lock-generation rule in section 5. Details in 11.1.
9. **The web E2E does not exercise tab navigation.** It passed 34/34 on the unpatched SDK 58 baseline even though pressing a tab is broken there. Only the Jest navigation tests catch the regression, so they must stay in the gate. Native tab behaviour is unverified on a device.
10. **New SDK 58 warning (informational, not fixed):** `Route "assistant" uses presentation "modal" but is the first screen in its native stack, so it is rendered as a card` appears when `/assistant` is the initial URL (it does in tests). A cold start or deep link straight to `/assistant` may render as a card instead of a modal; unverified on a device; normal navigation from a tab is unaffected in tests. Suggested later fix (`unstable_settings.anchor`) is **out of scope**.
11. **Windows toolchain not exercised here (new):** the runtime trace of `expo start`/`expo export` ran on Linux only. Windows differences (named pipe in `2g`, different native binaries) are in section 11.9, with an optional read-only check you run: `docs/mobile/SDK58_WINDOWS_TRACE.md`.
12. **Screenshots (verified):** the SDK 58 web renders of the 25 committed screenshots (Light and Dark) are pixel-identical to SDK 57 for 21 images; the other 4 (`home-live`, `home-offline`, Light and Dark) differ only in clock-derived times in the seeded plan (section 11.5).
13. **A load-sensitive first test:** the first test in `demo-mode.test.tsx` (`/ shows the Synthetic data indicator`) exceeded Jest's 5-second default once, on a cold cache while three other test runs competed for the CPU; it passes in about 3 seconds on an idle machine and in all clean CI-equivalent runs. Not a regression; noted so a one-off timeout is not misread.

## 5. Method (only after approval)
1. Work on the current branch in small commits: (a) dependency pins + lockfile, (b) code fixes forced by the bump, (c) tooling/docs, (d) re-verification evidence.
2. Install with the existing flags (`ignore-scripts=true`); the committed lockfile must be **generated by npm and never hand-edited**. **Lock-generation rule (new):** because overrides can drift when npm re-resolves an existing tree (section 11.1), generate the SDK 58 lockfile from `package.json` in a directory that has **no `node_modules` and no lockfile** (`npm install --package-lock-only --ignore-scripts`), review the diff against section 3, then prove it with `npm ci`, `npm ls --all`, `npm audit` and the P5 guard. Any later dependency change repeats this rule until the overrides are removed.
3. Fix only what fails, with the minimal change. If a fix would alter behaviour or UI, stop and ask.
4. Keep every M8.1/M8.2 security control: transport module, lint/CI bypass checks, native gate false, Keychain-only refresh token, allowlist, redirect controls, no persistence of question text.

## 6. Verification (reported separately, exactly as for M8.2)
1. **Clean-checkout `npm ci` (new, mandatory).** On the final upgrade commit, in an empty directory and Node 22.x (the version CI uses): `git clone --branch claude/charming-davinci-vqv0du <repo> clean && cd clean/mobile && npm ci`. Pass criteria: exit code 0; `npm ci` reports no lockfile/`package.json` mismatch; `git status --short` is empty afterwards (the lockfile is not rewritten); the installed tree matches section 3.1 exactly (`npm ls` of every direct package equals its target version, and `npm ls --all` shows no `invalid` or `extraneous` entry). Then `npm run check:all` runs in that clean tree, not only in my working tree. The GitHub Actions `mobile` workflow performs the same `npm ci` on its own clean checkout and is cited separately. Your Windows PC (`git pull` and `npm ci` on Node 22) is a third confirmation that I ask you to run.
2. **SDK compatibility check (new, mandatory).**
   - `npm run check:sdk` (already in `check:all`): every runtime dependency matches `node_modules/expo/bundledNativeModules.json` for SDK 58 (exact for unranged entries, same major.minor for `~` entries), and no SDK package resolves to another major line in the lockfile.
   - `npx expo install --check` using the project's own Expo CLI, **run online** (the workflow sets `EXPO_OFFLINE=1`, under which the tool prints "Dependency validation is unreliable in offline-mode"): it must report "Dependencies are up to date" or list only deliberate, documented exceptions. In scratch it listed `@types/react@19.2.18 - expected ~19.3.0` until P3 and reported up to date afterwards (11.5).
   - `npx expo-doctor` is **report-only** (it is fetched by `npx`, not added as a dependency; if you prefer I skip it, say so). Its findings are listed in the report and do not replace the two checks above.
   - The compatibility results are reported next to the Expo Go version actually used (SDK 58.0.0 as seen on your iPhone).
3. Local: `tsc`, ESLint, Prettier, `npm run check:all` (445+ tests, coverage thresholds unchanged), API-type consistency, licence/install-script/audit/transport/secret gates; iOS (Hermes) and web exports build.
4. Contract tests vs the synthetic backend through `contract-backend.sh run` (13/13 expected; 13/13 in scratch on SDK 58 with the overrides).
5. Playwright web E2E (34 checks, Light and Dark, no foreign hosts; 34/34 in scratch on SDK 58 with and without the fixes) and regenerated screenshots compared with the committed SDK 57 ones (21/25 identical in scratch; the rest differ only in clock text). Add: the Jest navigation and demo-mode tests must be part of the gate because the web E2E does not press tabs (risk 9).
6. Native gate tests: enrollment, refresh and authenticated requests still blocked on native; gate constant still `false`.
7. GitHub Actions `mobile` workflow on a clean checkout.
8. **Device:** after the upgrade, Expo Go (SDK 58) must open the app in demo mode on your iPhone. That is a user step; success there is **not** redirect evidence.
9. **Overrides (if approved, P4-P5):** `npm ls --all` shows no `invalid`/`extraneous`/`missing`; `npm audit` reports the count recorded in section 11.1; the committed `package-lock.json` is exactly what npm generated (no hand edits: `npm ci` succeeds and `git diff --exit-code package-lock.json` is clean after it); the P5 guard passes (every `test-exclude` 6.x still resolves `glob` 7.x and `minimatch` 3.x, no `glob` in 9.0.0-14.0.1 and no `minimatch` in 7.0.0-11.0.0 anywhere in the lockfile, and the `overrides` object equals the documented set); every override is documented in `DEPENDENCY_AUDIT.md` with its reason and a removal condition. A negative test proves the guard fails on a lockfile with the known drift.
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

## 10. Decisions I need from you (revision 4)
Nothing below has been done. `audit-baseline.json` (`maxModerate` 20), the licence allow-list and exceptions list, and the coverage thresholds stay untouched unless you say otherwise. P1-P6 are defined in section 2.
1. **Approve the upgrade to SDK 58** at exactly the versions in section 3.1 (Expo `58.0.26`).
2. **Accept the gesture-handler 2 -> 3 major bump** (indirect; the app does not import it; no failures attributable to it in any scratch run).
3. **Audit overrides and guard (P4, P5).** Approve the five scoped overrides (section 11.1.B) **together with** the `check:overrides` guard and the lock-generation rule, or decline both. Declining means the upgrade is blocked by the audit gate. Approving the overrides without the guard is not recommended (section 11.1, risk 5).
4. **Licence:** no exception needed (11.2). Approve recording `MIT-0` as an accepted licence in `DEPENDENCY_AUDIT.md` (documentation only).
5. **New transitive packages (11.3):** accept or decline as a consequence of `expo@58.0.26`. My assessment is conditional acceptance with the safeguards in 11.3; the residual points are `noxcturnal` (no provenance, single maintainer, opt-in and not loaded here) and `2g` (loaded on every CLI run, writes session logs to the temp directory).
6. **Node types (P1 + P2).** Approve adding `@types/node` **26.6.6** as an exact-pinned devDependency **and** `"types": ["node"]` in `tsconfig.json` (the second is required; the first alone does not fix the typecheck). Verified: zero lockfile entries added, removed or re-versioned; MIT; no install script; signed; no provenance attestation (DefinitelyTyped). Alternative verified: `@types/node` **22.20.5** (matches the Node 22 runtime and CI; swaps two lockfile entries, `@types/node` and `undici-types` 8.9.0 -> 6.21.0). Recommendation: 26.6.6 as you proposed (zero tree change), accepting that its types describe Node 26 while CI runs Node 22; choose 22.20.5 if you prefer type fidelity. Default if you do not choose: I stop at the typecheck and report.
7. **`@types/react` 19.3.0 (P3):** approve this devDependency bump (needed for `expo install --check` to pass).
8. **Tab bar migration (P6):** approve the code change in `src/design/components/TabBar.tsx` shown in section 11.7, with no change to `app/(tabs)/_layout.tsx`, the visuals or the behaviour.
9. **Windows check:** decide whether you will run the optional, read-only trace in `SDK58_WINDOWS_TRACE.md` after the implementation is merged; whether to include the report-only `npx expo-doctor` run; and confirm your CPU architecture (`node -p process.arch`), which decides which native binary packages your PC installs.
10. **Confirm the sequencing:** implementation, your review, then you run the canary; no flipping the gate in this task.
11. Optional: a throwaway SDK 58 probe app for an early redirect look (not recommended as a substitute).

## 11. Pre-implementation investigations
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
- Jest with coverage: **442 pass, 3 fail, identical to the same project on the no-override preview** (same 3 tests, section 11.7-11.8); coverage figures identical (96.88 / 91.32 / 94.16 / 97.78). Coverage runs `babel-plugin-istanbul` -> `test-exclude` -> `glob`/`minimatch`, the exact path being overridden.
- ESLint: exit 0 (ESLint 10 itself requires `minimatch ^11` already). `expo export --platform web`: succeeds. `expo export --platform ios` (Hermes): succeeds, 3.7 MB bundle.
- Revision 4 completed the remaining checks: contract tests, Playwright E2E (Light and Dark) and the CI-equivalent workflow, section 11.5. Still not tested: `jest --watch`, the real GitHub Actions run (only after implementation).
**Compatibility risks of B:**
1. It crosses major versions for dev tooling: `glob` 10 and 13 -> 14, `minimatch` 9 and 10 -> 11, outside the ranges the consumers declare. Evidence so far is tests passing, not upstream support.
2. Node engines: `glob@14` needs Node `18 || 20 || >=22`, `minimatch@11` needs `20 || >=22`. CI and the local setup use Node 22; Node 18 would no longer work for `npm run test` on `minimatch`.
3. Overrides must be maintained: remove them when Jest/`test-exclude`/`@expo/fingerprint` ship releases that depend on patched versions; each SDK upgrade re-checks them (documented removal condition in `DEPENDENCY_AUDIT.md`).
4. I deliberately scoped the selector to `test-exclude@7.0.2`: my first, unscoped `test-exclude` override also forced `glob@14` onto `test-exclude@6.0.0`, which requires `glob ^7` and `minimatch ^3` (not affected by the advisories) and would have been a genuine incompatibility. `test-exclude@6.0.0` keeps `glob@7.2.3` and `minimatch@3.1.5`.
5. **Re-resolution instability (revision 4 finding, important).** A lockfile generated cleanly by npm from `package.json` (no `node_modules`, no lockfile) is valid and is what all results above used. But re-running `npm install` in a tree that already has `node_modules` (the normal developer workflow, e.g. when adding a package) **rewires `test-exclude@6.0.0` to `glob@14.0.6`**, although it declares `glob ^7.1.4`; the nested `glob@7.2.3` disappears. Evidence: (a) an edge-by-edge check of every lockfile dependency against its declared range (using the real `semver` package) shows exactly the 18 intended out-of-range edges in the clean lock and 19 after the re-resolution; (b) calling `test-exclude@6.0.0` on `glob@14.0.6` fails (`The "original" argument must be of type function`); (c) **all 12 CI steps still pass on the drifted lockfile** (it is not on Jest's coverage path today, which uses `test-exclude@7.0.2`), so nothing in CI would notice. Four formulations were tried: the proposed selector `test-exclude@7.0.2` (clean when generated fresh, drifts on re-resolution); a parent-scoped form `babel-plugin-istanbul@8.0.0 > test-exclude` (drifts and also rewires `test-exclude@6.0.0`'s `minimatch`); range-keyed overrides `glob@^10.4.1`, `minimatch@^9.0.4` and so on (ineffective: audit stays at 19, nested vulnerable copies remain); unscoped `glob`/`minimatch` (not run, rejected by inspection: it would force `glob@14` and `minimatch@11` onto every consumer, including `@redocly/openapi-core`, which pins `minimatch@5.1.9`). The scoped selector remains the best of those that work. **Mitigations proposed (pending approval):** the lock-generation rule (section 5, item 2) and the P5 guard (`check:overrides`: every `test-exclude` 6.x resolves `glob` 7.x and `minimatch` 3.x; no `glob` in 9.0.0-14.0.1 or `minimatch` in 7.0.0-11.0.0 anywhere in the lock; the `overrides` object equals the documented set; no dependencies, about 40 lines, plus a negative test on a drifted lock). The guard detects drift; it cannot prevent npm from producing it.
6. **No hand-edited lockfile.** My first attempts at editing the lockfile by hand left `test-exclude@6.0.0` on `glob@14`; I discarded them. The tested lock differs from the in-place preview of revision 3 in 7 package names, all in the removed `glob@10` subtree; the other 9 packages (`@isaacs/cliui`, `jackspeak`, `foreground-child`, `package-json-from-dist`, `@pkgjs/parseargs`, `eastasianwidth`, `string-width-cjs`, `strip-ansi-cjs`, `wrap-ansi-cjs`) drop out.
**Conclusion:** not blocked. A compatible resolution exists (B) at the cost of five scoped overrides, one guard script and a lock-generation rule. If you do not approve overrides, the only other ways to keep the ratchet at 20 are option A (rejected) or raising the ratchet (not allowed by you); **in that case the SDK 58 upgrade is blocked by the audit gate.** If you approve the overrides but not the guard (P5), the drift risk stays and I recommend against that combination.

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

### 11.4 Compatibility findings from the first scratch run (revision 3; superseded by 11.5-11.8 where they overlap)
On the unmodified SDK 58 preview (no fixes, no overrides): `tsc` 45 errors, 442 of 445 tests pass (3 fail), ESLint passes, web and iOS Hermes exports build. All three test failures and all 45 type errors are now fully diagnosed (11.6-11.8). These results are from scratch copies; the repository is unchanged and still passes on SDK 57.

### 11.5 Remaining compatibility checks (revision 4): contract tests, Playwright E2E, CI-equivalent workflow
All in scratch copies outside the repository, Node 22.23.5, `ignore-scripts=true`, with the project's own files copied in. Three clean, repo-shaped checkouts were built and `npm ci`'d: **baseline** (SDK 58, no overrides, no fixes), **overrides** (SDK 58 + P4), and **final** (SDK 58 + P1-P4, P6; the lock generated per the rule in section 5). The workflow steps are those of `.github/workflows/mobile.yml` run in order, continuing after a failure, with the workflow's environment (`CI=1`, `EXPO_NO_TELEMETRY=1`, `EXPO_OFFLINE=1`). Coverage thresholds in `jest.config.js` were **not** changed.
| CI step | SDK 58 baseline | SDK 58 + overrides | Proposed final (P1-P4, P6) |
|---|---|---|---|
| `check:sdk` | pass | pass | pass |
| `check:transport` | pass | pass | pass |
| `typecheck` | **fail, 45 errors** (existing SDK 58 break) | **fail, 45** (existing) | pass, 0 |
| `lint` | pass | pass | pass |
| `format:check` | fail (see note) | fail (see note) | pass |
| `test:coverage` | **fail: 3 tests** (existing SDK 58 break); thresholds met | **fail: same 3** (existing); thresholds met | pass: **445/445**; thresholds met; 96.88 / 91.25 / 94.18 / 97.78 |
| `api:types:check` | pass | pass | pass |
| `check:licenses` | pass (1,263 pkgs, 13 exceptions) | pass (1,195, 13) | pass (1,195, 13) |
| `check:install-scripts` | pass | pass | pass |
| `check:audit` | **fail: 22 > 20** (existing SDK 58 effect) | pass: 0 | pass: 0 |
| `export:web` | pass | pass | pass |
| `check:secrets` | pass | pass | pass |
Note on `format:check`: the only file it flags is my scratch `package.json`, which I wrote with a script (Prettier reformats it; content identical). In the real implementation npm writes it, and `prettier --write package.json` is run; the final column shows it passes after that. **Failures attributable to the overrides: none.** Failures that exist on SDK 58 regardless of the overrides: `typecheck` (44 missing Node types + 1 tab bar type error), 3 tests (tab bar), audit 22 > 20 (what the overrides fix). Branch coverage moves 91.32 -> 91.25 because the migration adds one branch (`if (route)`); every directory-level threshold still passes unchanged.
- **Contract tests** (real synthetic backend, fresh throwaway database per run, run from the SDK 58 + overrides scratch tree): **13/13 pass**. A first attempt failed 1 of 13 (the kill-switch test) because the scratch copy of `scripts/contract-backend.sh` looked for its state file in the wrong directory; that is a scratch-layout artefact, not an SDK or override failure, and it passed once the scratch pointed at the repository script.
- **Playwright web E2E** (Light and Dark, server-side request forwarding, real synthetic backend): **34 pass / 0 fail** on the unpatched SDK 58 baseline and **34 pass / 0 fail** on SDK 58 + overrides + fixes, both with "no request left the dev server and the synthetic backend (foreign hosts: none)". The E2E does **not** press tabs, so it did not detect the broken tab bar (risk 9).
- **Screenshots:** the 25 committed Light/Dark screenshots were regenerated on SDK 58 and compared pixel by pixel with the committed SDK 57 images: **21 identical**; **4 differ** (`home-live` and `home-offline`, Light and Dark, 0.16-0.18% of pixels, one region) only in clock-derived times of the seeded plan (for example 12:57 vs 15:37), not in design.
- **`npx expo install --check`** (online): on the proposed state without P3 it reports `@types/react@19.2.18 - expected version: ~19.3.0`; with P3 it reports "Dependencies are up to date". With the workflow's `EXPO_OFFLINE=1` it prints "Dependency validation is unreliable in offline-mode", so the real check must run online (section 6).
- **Not done:** the real GitHub Actions run (only after implementation), `jest --watch`, anything on a physical device.

### 11.6 `@types/node@26.6.6` (P1) and the `types` setting (P2)
Verified facts (registry and scratch):
- **Package:** MIT; maintained in DefinitelyTyped, published by the `types` account (`ts-npm-types@microsoft.com`) on 2026-10-28; it is the registry `latest` (also the `ts5.6`-`ts7.1` tags); 109 files, 2.6 MB unpacked; integrity `sha512-MzQj+8Kx3XI40OmUAE6Qoh58ZMNstNNMwn2nfd3qWsfe7nDNSqJNzes4njhz6AHwrSHOffSLwXU6yb474Q4aog==` (equals the lockfile); two registry signatures; **no provenance attestation** (the registry returns 404); no install or any lifecycle scripts; one dependency, `undici-types ~8.9.0` (already in the tree at 8.9.0); declares TypeScript 5.6+ (`typesVersions` for 5.6/5.7).
- **Already in the tree:** `@types/node@26.6.6` is currently a transitive copy with 19 dependents (all `@jest/*`, `jest-*`, `@types/graceful-fs`, `@types/jsdom`) requiring `*`.
- **Tree impact (fresh npm-generated lock, no `node_modules` present):** 1,196 lockfile entries before and after; **0 added, 0 removed, 0 version changes**; only the root `devDependencies` gains the entry. Licence gate (1,195 packages, 13 exceptions), install-script gate and `npm audit` (0) unchanged.
- **Declaring it is not enough (verified):** `tsc` reports 45 errors with the package installed but not declared, and after declaring it would stay unchanged, because TypeScript 6.0.3 no longer includes `@types/*` automatically (the SDK 57 tree only worked because some imported declaration referenced `node`). With `"types": ["node"]` the 44 Node errors disappear (and `tsc` is 0 with P6). The `types` list restricts ambient types to Node only; typecheck stays clean, so nothing else relied on automatic inclusion.
- **Type-fidelity caveat:** the 26.x types describe the Node 26 API while local development and CI run Node 22. Nothing in the repository's code or tests used an API absent from Node 22 (typecheck passes with both type sets), but TypeScript would not warn if someone did. `@types/node@22.20.5` was tested as the alternative: 0 errors, MIT, same shape; it changes exactly two lockfile entries (`@types/node` 26.6.6 -> 22.20.5 and `undici-types` 8.9.0 -> 6.21.0).
- **Unverified:** whether `@types/node` supply-chain provenance matters to you: DefinitelyTyped publishes without attestation (like most `@types/*`).

### 11.7 Tab bar: the exact SDK 58 change and the minimal migration (P6)
- **SDK 57 (`expo-router` 57.0.32), `build/react-navigation/bottom-tabs/types.d.ts`:** `BottomTabBarProps = { state: TabNavigationState; descriptors; navigation: NavigationHelpers<ParamListBase, BottomTabNavigationEventMap>; insets }`.
- **SDK 58 (`expo-router` 58.0.32), same file:** `BottomTabBarProps = { state: BottomTabViewState; descriptors; emitter: BottomTabEmitter; navigateToTab: (routeKey: string) => void; insets }`. `navigation` is **removed**.
- **Semantics (read in `createBottomTabNavigator.js`):** `navigateToTab(routeKey)` finds the route whose **`key`** equals the argument and calls `actions.navigate(route.name, route.params)`; with an unknown key it does nothing (and warns in development: "`navigateToTab` takes a route key, not a route name"). It takes a **key**, not a name. SDK 58's default tab bar first emits `tabPress` and only calls `navigateToTab` if the tab is not focused and the event was not prevented; our bar has never emitted `tabPress`, so calling `navigateToTab` directly preserves its behaviour. Only difference: SDK 58 passes `route.params` where SDK 57 `navigate(name)` passed none; our four tabs have no params.
- **Proposed minimal change (scratch diff against the repository file; `app/(tabs)/_layout.tsx` needs no change):**
```diff
 export type AbuTabBarProps = {
-  state: { index: number; routes: { name: string }[] };
-  navigation: { navigate: (route: string) => void };
+  state: { index: number; routes: { name: string; key: string }[] };
+  navigateToTab: (routeKey: string) => void;
 };
-export function AbuTabBar({ state, navigation }: AbuTabBarProps) {
+export function AbuTabBar({ state, navigateToTab }: AbuTabBarProps) {
@@
-        onPress={() => navigation.navigate(t.route)}
+        onPress={() => {
+          const route = state.routes.find((r) => r.name === t.route);
+          if (route) navigateToTab(route.key);
+        }}
```
- **Verified in scratch:** `navigation.test.tsx` and `demo-mode.test.tsx` 17/17 pass; `tsc` 0 errors (the `_layout.tsx` type error disappears: `BottomTabBarProps` is structurally assignable to the narrowed `AbuTabBarProps`); full suite 445/445; no markup or style line changes, so the visual design is untouched (web screenshots identical apart from clock text, 11.5). **Not verified:** behaviour on a physical iPhone (Jest and the web build only).

### 11.8 The two failing `demo-mode.test.tsx` tests, diagnosed individually
Run on an idle machine, each test alone (`jest tests/components/demo-mode.test.tsx -t "<name>"`), before and after the tab bar migration:
| Test | Without the migration | Exact failure | With the migration |
|---|---|---|---|
| `Finance and Messages also carry the explicit demo banner, and every fixture name is labelled (demo)` | **fails** | `TypeError: Cannot read properties of undefined (reading 'navigate')` at `src/design/components/TabBar.tsx:55`, raised by `fireEvent.press(screen.getByRole('tab', { name: 'Messages' }))` at `demo-mode.test.tsx:23`. The two assertions before it (`Demo data: not real accounts...` label, `Demo Supermarket`) pass; the Messages assertions after it never run. | **passes** |
| `makes no network request (fetch and XMLHttpRequest are rigged to throw in test setup)` | **fails** | the same `TypeError` and `TabBar.tsx:55`, raised by `fireEvent.press(await screen.findByRole('tab', { name }))` at `demo-mode.test.tsx:44` on the **first** loop iteration (`Finance`). The final `expect(spy).not.toHaveBeenCalled()` is **never reached**. | **passes** |
Both failures have the same proximate cause but I verified each separately, with its own press line and its own surviving assertions; neither has a second cause, since each passes with only the P6 change. Important consequence: while the tab bar is broken, the privacy assertion "demo mode makes no network request" in the second test is **not evaluated at all**, so a green run of that test on SDK 58 means something only after P6. The first-in-file test (`/ shows the Synthetic data indicator`) failed once with a 5-second timeout under CPU load (risk 13); it is not one of the two and passes on an idle machine. Proposed fix for each: P6, and nothing else.

### 11.9 Windows and Expo CLI behaviour (2g, native binaries): verified vs assumed
Your development environment is Windows with Node 22.23.5 and an iPhone on Expo Go (the `npx expo start --lan` flow). I cannot run Windows here. **Verified on Linux x64, Node 22.23.5, SDK 58 tree** (module-load, spawn and `net` listen/connect tracing through `NODE_OPTIONS`, during `expo export` for iOS and `expo start --web` plus a full 6 MB web bundle request):
- `2g` is `require`d by `@expo/cli` (43 load events in one `expo start`); `@expo/cli` uses only its **library** (`events.debug(...)` and similar). Nothing in `@expo/cli` spawns the `2g` command line.
- `2g` creates `/tmp/event-log-<uid>/<session>/` (directory mode 0700) with `0.jsonl` and `meta.json` (files mode 0600) and **listens on two Unix-domain sockets** (`ipc.sock`, `live.sock`) inside it, and connects to one of them; all other listeners in the trace were Metro's TCP port. The sockets disappear when the CLI exits. Log content in one inspected run: 14 event types (`root:init`, `env:mode`, `env:load` with environment-variable **names only**, `devserver:*`, `metro:*`), 26 lines; no line contained token/secret/password words; one line contained a filesystem path. Segments rotate inside a session (`rotateSegments`); I found **no pruning across sessions**: session directories remain in the temporary directory (9 after my runs, 84 KB).
- What its "spawned process" is: the `2g record -- <command>` subcommand starts the command a person passes to it (`child_process.spawn`) and traces it. `2g` does not start any process of its own otherwise. Nobody in the Expo CLI path calls it, per the trace and a search of `@expo/cli`.
- `noxcturnal`, `@noxcturnal/*`, `expo-modules-cli`, `expo-modules-macros` and the macros binaries were **not loaded** and **no process was spawned** for them in either run. Besides, the code only loads `noxcturnal` when `experiments.noxcturnalTransformWorker` is truthy, and the repository's app config has no `experiments` key. The only other spawn attempts seen were Expo's platform probes (`osascript`, `xcodebuild`, `xcode-select`, `sh`, `dotslash`), none related to these packages.
- Static facts about the Windows x64 binaries (from the import tables; **never executed**): `noxcturnal.win32-x64-msvc.node` imports no Winsock/WinHTTP/WinInet DLLs (it imports `LoadLibraryA/ExW`, `IsDebuggerPresent`); `ExpoModulesMacros.exe` imports `ws2_32.dll`, `secur32.dll`, `CreateProcessW`, `OpenProcessToken` among many system DLLs, consistent with a Swift runtime; whether any code path uses them is unverified.
**Assumptions / unverified on Windows:** that the same modules load and nothing is spawned; that `2g` writes under `%TEMP%\event-log-<uid>`; the exact named-pipe names (read in source: `\\.\pipe\event-log-<pid>-<time>-<random>-ipc` for the temporary child sink) and the pipe's access control (Node/libuv defaults, not inspected); whether the path with a space in your user folder changes anything (it did not affect SDK 57 runs; not tested with SDK 58); how Windows security software reacts to the new native `.node`/`.exe` files. **How to close the gap:** `docs/mobile/SDK58_WINDOWS_TRACE.md` is a read-only, user-run procedure (steps, helper script with quoting for your user-folder space, how to read the result). It is optional, pending your decision 9, and must not be run before the implementation is approved and merged.

**Update after the owner ran the Windows trace (2026-11-24):** on `win32 x64` the clean traced `expo start --lan` run loaded `2g` (51 events) and **never loaded** `noxcturnal`, `@noxcturnal/*`, `expo-modules-cli`, `expo-modules-macros` or `@expo/modules-macros-*`, and started no process for them. The Windows `2g` log folder is `%LOCALAPPDATA%\Temp\event-log\<pid>-<time>-<random>\` and its named pipes are `...-ipc` and `-live`; pipe access control stayed unverified (`Get-Acl` error 87). New finding unrelated to those 16 packages: Expo starts `dotslash`, which fetches (SHA-256 pinned) and launches the React Native DevTools binary (about 138 MB for Windows, from GitHub or Meta's CDN). Expo Go also requires an Expo account login. Details: `SDK58_WINDOWS_TRACE.md`, "Results of the run".
