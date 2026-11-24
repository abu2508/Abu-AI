# Plan: upgrade the mobile app from Expo SDK 57 to SDK 58

Status: **PLAN ONLY. Nothing has been changed. Waiting for your approval.**
Branch: `claude/charming-davinci-vqv0du` (base `bf0213af6c54d74b92bb3c83d694e8be2f7e4628`, M8.2 plus review follow-up, CI green).
Standing constraints are unchanged: ₹0–₹300 budget, mock model, synthetic data, four production gates BLOCKED, no real data, no secrets in the app, `NATIVE_AUTH_TRANSPORT_VERIFIED` stays `false`.

## 1. Why
The iPhone redirect canary cannot run. Expo Go from the App Store is for **SDK 58.0.0**; the app is on **SDK 57**. iOS cannot install an older Expo Go (the app's own error message says so), so Expo Go cannot open the project. The only way to get a physical-device answer with the current tooling, without a Mac, an Apple developer account or an EAS build, is to move the app to SDK 58. The device evidence must also describe the transport that will ship, so the upgrade has to come before the canary result means anything.

## 2. Scope
In scope: bump Expo-managed packages to the versions SDK 58 specifies; adapt code only where something breaks; update the tooling that pins SDK 57; re-run every gate; re-read the `expo/fetch` native redirect behaviour for SDK 58; update docs.
**Out of scope:** any feature or UI change; backend source, migrations or API contract; new packages; M8.3; B3b; flipping the native gate; production-gate work; enabling native authenticated connectivity; the canary run itself (a separate step you perform).

## 3. Target versions (read from `expo@58.0.26`, the current `latest`, `bundledNativeModules.json`; nothing installed yet)
| Package | Now (SDK 57) | SDK 58 target |
|---|---|---|
| expo | 57.0.34 | 58.0.26 |
| react / react-dom | 19.2.3 | 19.3.0 |
| react-native | 0.86.3 | 0.88.1 (two minors) |
| expo-router | 57.0.32 | ~58.0.32 |
| @expo/metro-runtime | 57.0.19 | ~58.0.17 |
| expo-constants / -crypto / -device / -font / -linking / -network / -secure-store / -splash-screen / -status-bar / -symbols / -system-ui | 57.x | ~58.0.16 / .8 / .5 / .12 / .17 / .5 / .3 / .14 / .5 / .5 / .7 |
| react-native-gesture-handler | 2.32.0 | **~3.3.0 (major)** |
| react-native-reanimated | 4.5.1 | 4.7.1 |
| react-native-worklets | 0.10.1 | 0.13.0 |
| react-native-safe-area-context / -screens / -svg | 5.7.0 / 4.26.2 / 15.15.4 | ~5.11.1 / ~4.30.1 / 15.16.0 |
| async-storage, react-native-web | 2.2.0, 0.21.4 | unchanged (2.2.0, ~0.21.4) |
| jest-expo (dev) | 57.0.6 | ~58.0.15 |
Exact pins will be written without ranges (existing convention). All of these are **existing** dependencies; **no new package is added**. Packages the project owns outside Expo's table (zod, zustand, @tanstack/react-query, testing-library, eslint, prettier, typescript, jest) are not bumped. Node 22.23 satisfies `expo@58`'s engine range (`^22.13`). A side effect to expect: React 19.3 satisfies the `react-reconciler` peer that currently warns during `npm ci`.

## 4. Known risks (what I expect to break, and how I will check)
1. **React Native 0.86 -> 0.88** (two minors): possible API removals or changed defaults. Check by typecheck, lint, the full Jest suite and the web export.
2. **gesture-handler 2 -> 3 (major):** the app does not import it directly (`grep` over `src`, `app`, `babel.config.js`, `app.json` is empty); it is reached through expo-router. Check navigation/accessibility tests and the web E2E.
3. **reanimated 4.5 -> 4.7 and worklets 0.10 -> 0.13:** babel/worklets plugin pairing. Check that Jest, the iOS bundle export and the web export all build.
4. **`expo/fetch` native redirect behaviour:** my M8.2 conclusion (iOS delegate refuses redirects, Android `followRedirects(false)`) came from SDK 57 source. I will re-read the SDK 58 native sources and update the notes, but **source reading still is not evidence**: only the device canary can show it.
5. **Tooling that pins 57:** `scripts/check-sdk-alignment.mjs` (SDK 57 expectations), `docs/mobile/DEPENDENCY_REGISTER.md`, and any "Expo 57" mention. Updated as documentation/tooling, not behaviour.
6. **Supply-chain ratchets:** licence gate (1,407 packages, 14 documented exceptions), install-script gate, audit gate (moderate 20, ratchet 20) may change with the new tree. The ratchet may only go **down or stay**; a rise stops the work and comes back to you. Any new install-script package or licence exception is reported, not silently accepted (`ignore-scripts=true` stays).
7. **Screenshots and the E2E harness:** web renders may shift pixel-level; I will regenerate and check them, not hand-edit.

## 5. Method (only after approval)
1. Work on the current branch in small commits: (a) dependency pins + lockfile, (b) code fixes forced by the bump, (c) tooling/docs, (d) re-verification evidence.
2. Install with the existing flags (`ignore-scripts=true`); use `npm ci`-reproducible lockfile; use `npx expo install --check`-equivalent validation through the repo's `check:sdk` script, updated to SDK 58.
3. Fix only what fails, with the minimal change. If a fix would alter behaviour or UI, stop and ask.
4. Keep every M8.1/M8.2 security control: transport module, lint/CI bypass checks, native gate false, Keychain-only refresh token, allowlist, redirect controls, no persistence of question text.

## 6. Verification (reported separately, exactly as for M8.2)
1. Local: `tsc`, ESLint, Prettier, `npm run check:all` (445+ tests, coverage thresholds unchanged), API-type consistency, licence/install-script/audit/transport/secret gates; iOS (Hermes) and web exports build.
2. Contract tests vs the synthetic backend through `contract-backend.sh run` (13/13 expected).
3. Playwright web E2E (34 checks, Light and Dark, no foreign hosts) and regenerated screenshots.
4. Native gate tests: enrollment, refresh and authenticated requests still blocked on native; gate constant still `false`.
5. GitHub Actions `mobile` workflow on a clean checkout.
6. **Device:** after the upgrade, Expo Go (SDK 58) must open the app in demo mode on your iPhone. That is a user step; success there is **not** redirect evidence.
7. Report: `docs/mobile/reports/SDK58_UPGRADE.md` with commit SHAs, package diff, changed files, test/coverage numbers, CI link, gate results, known limitations. Then **stop**.

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
1. **Approve upgrading to SDK 58** at the versions in section 3 (current `latest` `58.0.26`), as version bumps of existing dependencies only.
2. **Accept the gesture-handler 2 -> 3 major bump** (indirect; the app does not import it) or tell me to stop if it breaks anything.
3. **Confirm the audit ratchet may not rise:** if the new tree pushes moderate findings above 20, I stop and report rather than raising the ratchet.
4. **Confirm the sequencing:** upgrade, review, then you run the canary; no flipping the gate in this task.
5. Optional: if you would like an early, non-binding look at SDK 58's redirect behaviour before the full upgrade, I can write a tiny throwaway probe app, but I recommend against it as a substitute (it would not be the shipped code).
