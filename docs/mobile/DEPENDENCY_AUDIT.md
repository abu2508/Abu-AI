# Dependency audit (transitive, licence and vulnerability) - results

Performed 2026-11-24 **without installing anything into the project**: the register's packages (everything M8.0-M8.7 may need; deferred native
packages excluded) were resolved in a throw-away scratch directory with `npm install --package-lock-only --ignore-scripts` (resolves the tree from registry metadata only,
runs no package code), then audited with `npm audit --package-lock-only` and scanned for licences and install scripts from the lockfile. The Skia `postinstall` script was
downloaded to the scratch directory and read. Raw outputs are not committed (they are reproducible with the same commands); the scratch `package.json` equals the register.

## 1. Resolution

- **1,608 packages** in the full tree (768 prod-flagged, 804 dev, 46 optional). Most are the Expo/Metro/Babel toolchain, not shipped in the app bundle.
- **Conflict found and resolved:** `eslint-plugin-react-native-a11y@3.5.1` declares peer `eslint ^3 || ... || ^8` and cannot coexist with ESLint 10 (npm ERESOLVE). That plugin is also
  stale (last release Nov 2024). **Decision: drop it** (the register is updated). Accessibility linting is covered by our own ESLint rules plus Testing Library role/label queries.
- With the plugin removed the tree resolves; npm printed three "overriding peer dependency" warnings (to be reviewed when the real install happens at M8.0).

## 2. Vulnerabilities (`npm audit`)

Direct dependencies had no advisories, but the **transitive** audit found **23** (20 moderate, 3 high, 0 critical). My earlier "no known advisories" statement covered only direct packages; this audit supersedes it.

| Advisory | Package chain | Severity | Ships in the app? | Assessment and handling |
|---|---|---|---|---|
| GHSA-vcc3-ghjq-m6fr, GHSA-qr28-fpjm-wq7f (DoS via crafted percent-encoded input) | `expo-router` -> `query-string@7.1.3` -> `decode-uri-component@0.2.2` | **High** (3 entries) | **Yes (runs on-device when Router parses URLs/links)** | Fix: override `decode-uri-component` to **0.5.1** (patched; npm audit then reports **0 high/critical**). Needs verification at M8.0 that Router/Metro still work with it (same API; semver-incompatible with `^0.2.2`, so it is an override). Defence in depth regardless: cap deep-link length and reject malformed percent-encoding **before** Router sees the URL (unit-tested in M8.1) |
| GHSA-g3hv-75wg-m249 (minimatch globparts DoS), GHSA-3g3m-2fjr-6pw4 (glob AbortSignal bypass) | `jest` -> `@jest/*` -> `babel-plugin-istanbul` -> `test-exclude` -> `glob`/`minimatch`; `expo` -> `@expo/fingerprint` -> `minimatch` | Moderate (20 entries) | **No: Node-side dev/build tooling only** | Triggered only by attacker-controlled glob patterns processed on a developer machine/CI; our patterns come from repo config. Accepted and tracked; `npm audit` in CI uses `--omit=dev` for the gate on shipped code and reports dev findings as warnings, with a ratchet (no new high/critical anywhere). `npm audit fix --force` would downgrade Jest to 29 and Expo to 52: rejected |

After the override: **0 high, 0 critical, 20 moderate (all Node-side tooling)**.

## 3. Licences (all 1,608 packages)

MIT 1,333 | ISC 112 | BSD-3-Clause 47 | Apache-2.0 32 | BlueOak-1.0.0 32 | BSD-2-Clause 24 | **MPL-2.0 12** | (MIT OR CC0-1.0) 4 | Python-2.0 2 | Unlicense 2 | 0BSD 2 |
MIT AND Apache-2.0 1 | (MIT OR Apache-2.0) 1 | CC0-1.0 1 | **CC-BY-4.0 1** | **(BSD-3-Clause OR GPL-2.0) 1** | no licence field 1.

Items outside the plain permissive set (none blocks, all need to be recorded):

| Package | Licence | Context | Decision |
|---|---|---|---|
| `lightningcss` + 11 platform binaries | MPL-2.0 (file-level weak copyleft) | CSS tooling in the Metro/Expo web toolchain; build-time | Acceptable: used unmodified, not redistributed in the iOS app. Allowlisted by name |
| `caniuse-lite` | CC-BY-4.0 | browser-support data used by build tooling | Acceptable (attribution); not shipped in the iOS app |
| `node-forge` | BSD-3-Clause OR GPL-2.0 (dual) | transitive of Expo tooling | We take the BSD-3-Clause option |
| `argparse` | Python-2.0 (PSF, permissive) | tooling | Acceptable |
| `exit@0.1.2` | no `license` field in the lockfile entry | dev-only peer of Jest tooling | Registry package metadata is MIT upstream; allowlisted by name with a note |

**CI licence gate:** allowlist = MIT, ISC, BSD-2/3-Clause, Apache-2.0, BlueOak-1.0.0, 0BSD, Unlicense, CC0-1.0, Python-2.0, **MIT-0**, and the SPDX combinations above; named exceptions for the five rows above.
*(SDK 58 correction: `MIT-0` (MIT No Attribution: the MIT licence without the notice-retention requirement; no copyleft; OSI-approved SPDX id) has been in `ALLOWED` in `mobile/scripts/check-licenses.mjs` since M8.0; this paragraph previously omitted it. No code or allow-list change was made, and no exception is involved. First used by `@csstools/color-helpers` 5.1.0, a test-environment-only transitive dependency via jsdom 26.)*
Any new licence, and any GPL/AGPL/LGPL/SSPL as the *only* option, fails the build until approved.

## 4. Install scripts

Only three packages in the tree have install-time scripts:
- `@shopify/react-native-skia@2.6.2` (`postinstall`, **inspected below**), `@parcel/watcher` (dev; native file-watcher prebuilds for Metro), `fsevents` (optional, macOS-only file watching).
- Policy: install with `--ignore-scripts` by default; allowlist exactly these three; CI fails if any other package gains an install script.

### Skia `postinstall` (`scripts/install-libs.js`, 133 lines) - inspected
- Reads one environment variable (`SK_GRAPHITE`) to pick the prebuilt-binary package family.
- Resolves the sibling packages `react-native-skia-apple-ios`, `-apple-macos`, `-apple-tvos` and `-android` with `require.resolve`, checks that xcframeworks exist, **deletes and recreates only its own `libs/{ios,macos,tvos,android}` directories** and copies the prebuilt frameworks from those sibling packages into them with `fs.cpSync`.
- **No network access, no `child_process`/exec, no downloads, no writes outside its own package directory.** Exits non-zero on missing packages.
- Residual risk is the **binary content** of the four sibling packages (pinned at 147.1.0, integrity hashes in the lockfile). Mitigation: lockfile integrity, the packages are published by the same organisation, and the orb/charts have an SVG-only fallback if Skia is later judged unacceptable.
- Verdict: **safe to allowlist.** The installed `libs/` contents will be recorded (hash listing) in the M8.0 report.

## 5. Consequences for the register and plan

- `eslint-plugin-react-native-a11y` **removed**.
- Override `decode-uri-component: 0.5.1` added (verification at M8.0).
- The scanners are specified (licence allowlist, install-script allowlist, `npm audit` gate with the dev-tooling ratchet) and become M8.0 deliverables.
- **Installation is incremental** (see DEPENDENCY_REGISTER section 8): M8.0 installs only what it needs; each later milestone installs its own packages after the same audit.
- Everything above should be re-run against the real lockfile at M8.0 before the first install is committed; results go into the M8.0 test report.

## M8.1 addendum
The seven new runtime packages add no vulnerabilities: `npm audit` is unchanged at 20 moderate (all pre-existing Node-side tooling), 0 high, 0 critical. None of the new packages has an install script. The ratchet stays at 20.

## SDK 58 addendum (upgrade from Expo SDK 57; plan: [SDK58_UPGRADE_PLAN](SDK58_UPGRADE_PLAN.md), report: [reports/SDK58_UPGRADE](reports/SDK58_UPGRADE.md))
**Tree and gates.** 1,195 packages resolved (clean npm generation), licence gate OK with 13 documented exceptions (one fewer than before), install-script gate unchanged (`@parcel/watcher`, `fsevents`; `ignore-scripts=true`), `npm audit` **0** (0 high, 0 critical). The ratchet in `audit-baseline.json` stays at **20** and is not lowered here.

**Advisories and overrides.** Two moderate advisories, both in Node-side dev/CLI tooling and both unfixed upstream inside the ranges Jest and Expo require: `glob` GHSA-3g3m-2fjr-6pw4 (affects 9.0.0 to 14.0.1; fixed in 14.0.2) and `minimatch` GHSA-g3hv-75wg-m249 (affects 7.0.0 up to 11.0.1; fixed in 11.0.1). Without action SDK 58 raises the audit count from 20 to 22 (`jest-expo` 58 depends on `jest-watch-typeahead` 3, which peers on Jest 30). Five scoped `overrides` in `mobile/package.json` move only the vulnerable consumers to the fixed releases (`glob` 14.0.6, `minimatch` 11.0.9):

| Override | Reason |
|---|---|
| `test-exclude@7.0.2` -> `glob` 14.0.6, `minimatch` 11.0.9 | vulnerable `glob` 10.x / `minimatch` 9.x, 10.x under `babel-plugin-istanbul` (Jest coverage) |
| `@jest/reporters`, `jest-config`, `jest-runtime` -> `glob` 14.0.6 | vulnerable `glob` 13.x required as `^13.0.6` by Jest 30.5.2 |
| `@expo/fingerprint` -> `minimatch` 11.0.9 | vulnerable `minimatch` 10.2.6 required as `^10.2.6` |
(the pre-existing `decode-uri-component` and `openapi-typescript` overrides are unchanged). They deliberately cross major versions for **dev tooling only**; tests, coverage, ESLint, and web and iOS Hermes exports pass with them. **Not** done: a pin of `jest-watch-typeahead` to 2.2.1 (it only hides the count, advisories remain); range-keyed overrides (ineffective); unscoped `glob`/`minimatch` overrides (would break consumers pinned to older majors, for example `test-exclude@6.0.0` and `@redocly/openapi-core`).

**Removal condition.** Remove an override when the consumer it targets ships a release whose declared range already excludes the advisory (Jest/`test-exclude`/`@expo/fingerprint) and `npm audit` stays at or below the ratchet without it. Re-check every override at each Expo SDK or Jest upgrade.

**Fragility and the guard.** npm re-resolving an existing tree can rewire `test-exclude@6.0.0` (needs `glob ^7.1.4`) onto `glob@14`; none of the other CI steps notices. Therefore: (1) the lockfile is generated by npm from a directory with no `node_modules` and no lockfile and is never hand-edited (plan section 5); (2) `npm run check:overrides` (in `check:all` and the workflow) fails if the overrides differ from the documented set, if any `glob` 9.0.0-14.0.1 or `minimatch` 7.0.0-11.0.0 is in the lockfile, or if any `test-exclude` 6.x does not resolve `glob` 7.x and `minimatch` 3.x; its negative tests are in `tests/unit/guards.test.ts`.

**New transitive packages (reviewed, accepted conditionally).** `noxcturnal` 0.1.14 and eight `@noxcturnal/*` platform binaries, `2g` 1.0.1, `expo-modules-cli` 0.5.0, `expo-modules-macros` 0.33.0 and four `@expo/modules-macros-*` binaries (16 packages; MIT; no install scripts; registry-signed and matching the lockfile). `noxcturnal` has no provenance attestation and a single maintainer and is **opt-in**: Expo loads it only when `experiments.noxcturnalTransformWorker` is truthy. It stays disabled, enforced by `npm run check:noxcturnal` (config files plus the resolved Expo config; negative tests in `tests/unit/guards.test.ts`). `2g` is loaded by every Expo CLI run (local IPC and JSONL session logs in the temporary directory). Full review and the verified/unverified split: SDK58_UPGRADE_PLAN section 11.3 and 11.9. Re-check provenance and the lockfile integrity values whenever these packages change.
