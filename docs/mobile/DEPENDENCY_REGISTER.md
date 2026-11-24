# Dependency candidate register (for approval before any installation)

**Nothing in this register is installed.** Approve the list (or mark packages to swap); installation happens in M8.0 only.

Data provenance: versions, licences, release dates and download counts were read from public npm registry metadata on 2026-11-24
(read-only queries; nothing installed into the project). The **transitive audit is complete** and recorded in [DEPENDENCY_AUDIT](DEPENDENCY_AUDIT.md): 1,608 packages resolved, licences scanned,
vulnerabilities audited (23 found transitively: 3 high fixed by an override, 20 moderate in Node-side dev tooling only), install scripts inspected including Skia's. It supersedes the
earlier statement that no advisories existed (that covered direct packages only).

## 1. How versions are chosen

- Expo-managed packages are pinned to the versions **bundled by the chosen Expo SDK** (read from the SDK's `bundledNativeModules.json`), so
  native modules, React Native and Reanimated stay mutually compatible. Newer upstream releases are deliberately not adopted ahead of the SDK.
- Exact pins and a committed lockfile; `npm ci` in CI; updates are deliberate and reviewed.
- Packages outside the SDK (state, validation, tests, lint) use the current stable release, constrained by the SDK template (for example TypeScript 6.0.x).

## 2. Expo SDK choice (decision needed)

| | SDK 57 (recommended) | SDK 58 |
|---|---|---|
| First release | 2026-06-30 | 2026-09-29 |
| Latest patch (2026-11-24) | 57.0.34 (34 patches) | 58.0.26 |
| React Native / React | 0.86.3 / 19.2.3 | newer line |
| Maturity | about 5 months of fixes | about 2 months |
| Fit | Everything below is aligned to it | Each version would need re-verification |

Recommendation: **SDK 57** for a security-sensitive app (more patches, wider field use), with an upgrade as a separate reviewed task. Tell me if you prefer 58.

## 3. Runtime dependencies

| Package | Version (exact) | Purpose | Licence | Last release; downloads | Security / supply-chain notes | Alternatives considered | Needed from |
|---|---|---|---|---|---|---|---|
| `expo` | 57.0.34 | App framework, CLI, build tooling, module system | MIT | 2026-11-23; 17.3M/wk | Large first-party surface; no install script; SDK pinned here (section 2) | Bare React Native + own tooling (far more native upkeep) | M8.0 |
| `react-native` | 0.86.3 | Runtime (SDK-bundled version) | MIT | 2026-08-24; 22.3M/wk | Widely audited; follow the SDK patch line only | - | M8.0 |
| `react` | 19.2.3 | UI library (SDK-bundled) | MIT | 2025-12-11; 275.7M/wk | Pinned by the SDK | - | M8.0 |
| `react-dom` | 19.2.3 | Web target for the design-system gallery / Router web | MIT | 2025-12-11; 260.2M/wk | Dev/gallery only; not in the iOS bundle | - | M8.0 |
| `react-native-web` | 0.21.4 | Render components on web for the gallery + Playwright | MIT | 2026-10-08; 11.0M/wk | Dev/gallery only | Storybook RN (heavier, more native deps) | M8.0 |
| `expo-router` | 57.0.32 | File-based navigation (decided) | MIT | 2026-11-23; 12.2M/wk | First-party; deep links allowlisted by us | React Navigation directly (Router builds on it) | M8.0 |
| `react-native-screens` | 4.26.2 | Native navigation containers | MIT | 2026-07-16; 15.8M/wk | SDK-pinned peer of Router | - | M8.0 |
| `react-native-safe-area-context` | 5.7.0 | Safe-area insets | MIT | 2026-02-24; 17.0M/wk; last release Feb 2026, stable | SDK-pinned; no network or storage | - | M8.0 |
| `expo-linking` | 57.0.14 | Deep-link parsing (`abuai://`) | MIT | 2026-11-12; 14.1M/wk | We enforce an allowlist; no link performs a mutation | RN `Linking` (lower level) | M8.1 |
| `expo-constants` | 57.0.25 | Read build config (base URL, flags) | MIT | 2026-11-23; 18.6M/wk | Config must contain no secrets (bundle scan in CI) | - | M8.0 |
| `expo-status-bar` | 57.0.1 | Status-bar style per theme | MIT | 2026-07-15; 14.0M/wk | Trivial | - | M8.2 |
| `expo-system-ui` | 57.0.4 | Root background colour and interface-style hook for live theme | MIT | 2026-09-11; 8.3M/wk | Trivial | - | M8.2 |
| `expo-splash-screen` | 57.0.12 | Splash control (Abu mark) | MIT | 2026-11-23; 11.5M/wk | Trivial | - | M8.2 |
| `expo-font` | 57.0.4 | Template/Router dependency; **no custom font is used** (system font) | MIT | 2026-09-11; 17.2M/wk | No remote fonts permitted | - | M8.0 |
| `expo-web-browser` | 57.0.3 | Template dependency of Router; **not used by us** | MIT | 2026-09-11; 10.8M/wk; removal candidate | Unused, so no exposure; remove at M8.0 if the template allows | - | M8.0 |
| `@tanstack/react-query` | 5.108.4 | Server-state cache, retries, invalidation (in-memory only) | MIT | 2026-11-23; 92.0M/wk | No persister package; short `gcTime`; query keys carry no PII | SWR; hand-rolled hooks | M8.1 |
| `zustand` | 5.1.0 | Tiny UI state (appearance, lock, ephemeral UI) | MIT | 2026-11-06; 96.9M/wk | No persistence middleware | React context + reducer; Jotai | M8.1 |
| `zod` | 4.6.7 | Runtime validation of every API response; inferred types | MIT | 2026-10-12; 470.6M/wk | Rejects unexpected shapes; no network | valibot (smaller), io-ts | M8.1 |
| `expo-secure-store` | 57.0.4 | Keychain storage for the refresh token (`WHEN_UNLOCKED_THIS_DEVICE_ONLY`) | MIT | 2026-09-11; 12.4M/wk | Security-critical, first-party; options reviewed in the M8.0 spike | react-native-keychain (third party, more config) | M8.1 |
| `expo-local-authentication` | 57.0.3 | Optional Face ID/passcode app lock | MIT | 2026-09-11; 3.2M/wk | Device-gated; nothing leaves the device | - | M8.1 |
| `@react-native-async-storage/async-storage` | 2.2.0 | **Non-sensitive** preferences only (appearance, lock delay) | MIT | 2025-06-05; 11.1M/wk; SDK-bundled 2.2.0 (Jun 2025); upstream is 3.x, we follow the SDK pin | Never tokens or data; lint restricts imports to one module; storage-scan test | expo-secure-store for prefs; MMKV (extra native module) | M8.2 |
| `react-native-reanimated` | 4.5.1 | Orb and transition animation (worklets) | MIT | 2026-07-02; 14.8M/wk | Native code; SDK-pinned; needs `react-native-worklets` | Animated API (weaker for the orb) | M8.2 |
| `react-native-worklets` | 0.10.1 | Runtime required by Reanimated 4 | MIT | 2026-07-01; 14.2M/wk | Native; peer `0.10.x` of Reanimated | - | M8.2 |
| `react-native-gesture-handler` | 2.32.0 | Gestures for sheets and swipe actions | MIT | 2026-06-11; 14.7M/wk | SDK-pinned; native | - | M8.2 |
| `@shopify/react-native-skia` | 2.6.2 | Orb glow and chart primitives (GPU 2D) | MIT | 2026-04-04; 2.4M/wk; larger bundle; fallback = SVG-only orb | **Has a `postinstall` script** (`install-libs.js`) and pulls 4 prebuilt-binary packages: allowlist after reading it, rely on lockfile integrity | react-native-svg only (no glow/shaders); victory-native (builds on Skia) | M8.2 |
| `react-native-svg` | 15.15.4 | Original Abu glyph/icon set and simple charts | MIT | 2026-03-18; 13.6M/wk | Pure rendering; SVG assets are our own and committed | Pre-rendered PNGs (not themeable) | M8.2 |
| `expo-blur` | 57.0.3 | Glass/blur material (tab bar, sheets, privacy blur) | MIT | 2026-09-11; 5.3M/wk | Reduce Transparency fallback required | `expo-glass-effect` (iOS 26 glass); plain translucent views | M8.2 |
| `expo-symbols` | 57.0.3 | Apple SF Symbols for generic system glyphs (approved) | MIT | 2026-09-11; 8.2M/wk | Apple symbols used as licensed for apps; iOS only (web gallery uses stand-ins) | Original icons only (approved just for brand/domain) | M8.2 |
| `expo-haptics` | 57.0.3 | Restrained feedback on commit actions | MIT | 2026-09-11; 8.4M/wk | None | - | M8.2 |
| `expo-document-picker` | 57.0.3 | Pick CSV/XLSX/PDF/ICS/chat exports | MIT | 2026-09-29; 5.8M/wk | Returns a cache URI: read into memory, delete the cache copy; never persisted | - | M8.3 |
| `expo-file-system` | 57.0.7 | Read the picked file into memory; delete picker cache | MIT | 2026-09-11; 17.3M/wk | Lint forbids writing private data to disk | - | M8.3 |
| `expo-clipboard` | 57.0.2 | Explicit Copy of reply drafts | MIT | 2026-09-11; 7.5M/wk | Copy only on user action; never read | - | M8.4 |
| `expo-notifications` | 57.0.26 | **Local** reminder notifications only (no remote push; G1) | MIT | 2026-11-23; 10.0M/wk | Permission just-in-time; generic text; no remote-push registration | `notifee` (third party) | M8.5 |
| `expo-camera` | 57.0.6 | Optional QR scan of the enrollment code | MIT | 2026-09-29; 4.9M/wk | Camera permission; the code string only; **decide at M8.1 whether to include** | Typed entry only (no dependency) | M8.1 (optional) |
| `expo-calendar` | 57.0.7 | EventKit read (device-verified only in M8.8b) | MIT | 2026-11-23; 1.1M/wk | Calendar permission; **synthetic calendar only**; read-only | Own Swift module | M8.8 |
| `expo-localization` | 57.0.2 | Locale/number-format hints | MIT | 2026-09-11; 5.9M/wk | Low risk | `Intl` only | M8.3 (optional) |

## 4. Development and test dependencies

| Package | Version (exact) | Purpose | Licence | Last release; downloads | Security / supply-chain notes | Alternatives considered | Needed from |
|---|---|---|---|---|---|---|---|
| `typescript` | 6.0.3 | Strict type-checking (SDK 57 template range `~6.0.3`) | Apache-2.0 | 2026-04-16; 451.0M/wk | Dev-only | TypeScript 7.0.2 is npm `latest` but the SDK template targets 6.x: not used until the SDK supports it | M8.0 |
| `@types/react` | 19.2.18 | React types (template range `~19.2.2`) | MIT | 2026-07-30; 248.4M/wk | Dev-only | - | M8.0 |
| `jest` | 30.5.2 | Test runner | MIT | 2026-09-18; 63.2M/wk | Dev-only; no network in tests | Vitest (weaker RN support) | M8.0 |
| `jest-expo` | 57.0.6 | Jest preset for Expo/RN | MIT | 2026-10-12; 5.9M/wk | SDK-aligned | - | M8.0 |
| `@testing-library/react-native` | 14.1.0 | Component tests by role/label | MIT | 2026-10-08; 6.6M/wk | Dev-only; peer `test-renderer` installs with it | - | M8.0 |
| `msw` | 3.0.7 | Mock HTTP at the network layer in component tests | MIT | 2026-11-18; 36.2M/wk | Dev-only; optional peers (vite, graphql) must not install: confirm at M8.0 | nock; hand-written fetch mocks | M8.0 |
| `eslint` | 10.15.0 | Linting | MIT | 2026-11-13; 216.2M/wk | Dev-only; confirm `eslint-config-expo` supports v10, else pin ESLint 9 | Biome (less RN/Expo coverage) | M8.0 |
| `typescript-eslint` | 8.74.0 | TypeScript lint rules | MIT | 2026-11-23; 122.8M/wk | Dev-only | - | M8.0 |
| `eslint-config-expo` | 57.0.2 | Expo lint baseline (SDK 57 line) | MIT | 2026-08-26; 4.0M/wk | Dev-only | Hand-rolled config | M8.0 |
| `eslint-plugin-react-hooks` | 7.1.1 | Hooks correctness | MIT | 2026-04-17; 123.7M/wk | Dev-only | - | M8.0 |
| `prettier` | 3.10.1 | Formatting | MIT | 2026-11-12; 196.8M/wk | Dev-only | Biome | M8.0 |
| `@playwright/test` | 1.65.1 | Web-gallery visual regression and web E2E in Codespaces | Apache-2.0 | 2026-11-13; 138.8M/wk | Dev-only; downloads browsers (CI cache); pinned | Cypress; Storybook test-runner | M8.2 |
| `openapi-typescript` | 7.13.0 | Generate API types from the committed OpenAPI snapshot (B2) | MIT | 2026-02-11; 18.0M/wk; last release Feb 2026 | Dev-only; reads a local file only | orval; hand-written types | M8.1 |

Also needed (not npm packages): **Maestro CLI** (E2E flows; Apache-2.0; only where a simulator or device exists), Node 22 LTS (already present), Docker (backend for contract tests).

## 5. Deferred to M8.8 (native iOS; depends on the Apple decision, not requested now)

| Package | Version (exact) | Purpose | Licence | Last release; downloads | Security / supply-chain notes | Alternatives considered | Needed from |
|---|---|---|---|---|---|---|---|
| `expo-dev-client` | 57.0.22 | Development build with custom native code (M8.8 only) | MIT | 2026-11-23; 9.1M/wk | Debug-only menu; never in a release profile | Expo Go cannot run custom Swift | M8.8 |
| `expo-background-task` | 57.0.24 | Opportunistic reminder refresh (optional) | MIT | 2026-11-23; 1.1M/wk | OS-throttled; optional | Foreground polling only | M8.8 (optional) |
| `@bacons/apple-targets` | 5.0.0 | Config plugin adding Share Extension / App Intents targets | MIT | 2026-07-17; 1.3M/wk | Community plugin that generates native targets: review the generated project; **needs macOS/EAS to build** | `expo-share-extension`; manual Xcode project | M8.8a |
| `expo-share-extension` | 5.0.6 | Alternative Share Extension plugin | MIT | 2026-02-23; 12k/wk | Community, about 12k downloads/week: low adoption; **not preferred** | `@bacons/apple-targets` | M8.8a (alt.) |
| `expo-speech-recognition` | 58.0.1 | Alternative to a custom on-device speech module | MIT | 2026-10-29; 1.5M/wk | Published against SDK 58: confirm SDK 57 support and that recognition can run **on-device only** | Custom Swift module over `SFSpeechRecognizer` | M8.8a (alt.) |

**Explicitly not included:** analytics, ads, crash-reporting SDKs, remote logging, social login, any AI/LLM SDK, any push vendor, persisted-cache plugins for React Query,
`@expo/vector-icons` (original SVG + SF Symbols instead), `expo-image` / `@expo/ui` (in the SDK template, not needed). `expo-glass-effect` (iOS 26 glass) stays an
option for the Hybrid look, reviewed separately.

## 5b. Incremental installation schedule (nothing outside the current milestone is installed)

| Milestone | Installs (exact versions above) |
|---|---|
| M8.0 | `expo`, `react`, `react-native`, `expo-router` and the packages Router requires as peers (`react-native-screens`, `react-native-safe-area-context`, `expo-linking`, `expo-constants`, `expo-status-bar`, `react-native-web`, `react-dom`, `expo-font` if the template needs it); dev: `typescript`, `@types/react`, `jest`, `jest-expo`, `@testing-library/react-native`, `eslint` + `typescript-eslint` + `eslint-config-expo` + `eslint-plugin-react-hooks`, `prettier`, `openapi-typescript` |
| M8.1 | `zod`, `@tanstack/react-query`, `zustand`, `expo-secure-store`, `expo-local-authentication`, (`expo-camera` only if QR scan is approved), `msw` (dev) |
| M8.2 | `react-native-reanimated`, `react-native-worklets`, `react-native-gesture-handler`, `@shopify/react-native-skia`, `react-native-svg`, `expo-blur`, `expo-symbols`, `expo-haptics`, `expo-system-ui`, `expo-splash-screen`, `@react-native-async-storage/async-storage`, `@playwright/test` (dev) |
| M8.3 | `expo-document-picker`, `expo-file-system`, (`expo-localization` if needed) |
| M8.4 | `expo-clipboard` |
| M8.5 | `expo-notifications` |
| M8.8 (deferred) | `expo-calendar`, `expo-dev-client`, optional/alternative native packages in section 5 |

Each milestone re-runs the audit/licence/install-script scan on the real lockfile before its install is committed.

## 6. Review process for any later addition

1. Justify it (what problem, why existing packages cannot). 2. Record licence, maintainers/adoption, last release, install scripts, native permissions, network use.
3. Check advisories and transitive licences. 4. Prefer first-party Expo modules. 5. Pin exactly. 6. Add it here with an approval line. Reject copyleft licences, unmaintained
(over 18 months without release) security-relevant code, analytics/telemetry, and broad permissions.

## 7. Risk notes

- **Skia install script:** inspected (DEPENDENCY_AUDIT section 4): local file copy only, no network or exec. Install with `--ignore-scripts` by default and allowlist it (and `@parcel/watcher`, `fsevents`) explicitly; fallback is an SVG-only orb.
- **Removed:** `eslint-plugin-react-native-a11y` (stale and its peer range stops at ESLint 8: it cannot coexist with ESLint 10). Accessibility linting = our own rules + Testing Library queries.
- **Override:** `decode-uri-component` -> `0.5.1` to clear the high-severity advisories reached through `expo-router` -> `query-string` (verify at M8.0).
- **ESLint 10 vs the Expo config:** the tree resolves with ESLint 10 and `eslint-config-expo@57.0.2`; confirm lint runs at M8.0, fall back to ESLint 9 if needed.
- **MSW 3 peers:** optional peers (vite, graphql) must not be pulled in; confirm at M8.0.
- **AsyncStorage 2.x vs upstream 3.x:** staying on the SDK-bundled line is intentional.
- **Licences:** every direct dependency listed is MIT or Apache-2.0 (TypeScript and Playwright are Apache-2.0). Transitive licences are scanned in M8.0; anything outside the allowlist blocks CI until approved.
- **Maintenance:** the pinned versions were all published within the last ~12 months except AsyncStorage 2.2.0 (Jun 2025; upstream has newer releases) and the stale a11y lint plugin; none is deprecated on npm.
