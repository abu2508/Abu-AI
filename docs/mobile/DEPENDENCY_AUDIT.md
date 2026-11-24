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

**CI licence gate:** allowlist = MIT, ISC, BSD-2/3-Clause, Apache-2.0, BlueOak-1.0.0, 0BSD, Unlicense, CC0-1.0, Python-2.0, and the SPDX combinations above; named exceptions for the five rows above.
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
