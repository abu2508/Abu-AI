# Abu AI mobile: iOS capability matrix (plan)

Honest statement of what each iOS capability needs and where it can be built and tested. "Codespaces/Windows" here means a
Linux or Windows machine with no Xcode: JS/TS tooling, unit tests, Expo web and cloud build submission only.

## 1. Environment reality

| Activity | Linux / Windows / Codespaces | macOS + Xcode | EAS cloud build | Physical iPhone |
|---|---|---|---|---|
| Write and type-check TypeScript | yes | yes | n/a | n/a |
| Jest unit and component tests | yes | yes | n/a | n/a |
| Design-system web gallery + Playwright screenshots (light/dark) | yes (approximation of iOS) | yes | n/a | n/a |
| Contract tests against the Docker backend | yes | yes | n/a | n/a |
| Run in Expo Go (pure-JS features) | Metro runs here; the app runs on a phone via Expo Go | yes | n/a | yes |
| iOS Simulator | **no** | yes | n/a | n/a |
| Development build with custom native code | **no** (cannot compile Swift) | yes | **yes** (builds in the cloud) | install needed |
| Share Extension, App Intents, App Groups | write config; **cannot compile or run** | yes | yes (build) | **verify here** |
| TestFlight / ad-hoc install | no | needs account | needs account | yes |
| Maestro/Detox E2E | Maestro flows can be authored; running needs a simulator/device | yes | Maestro Cloud (optional, paid) | yes |

Consequence: P0-P7 are largely Linux-friendly; P8 native work needs EAS (or a Mac) **and** a physical iPhone, and an Apple
Developer Program membership for anything beyond the simulator. That cost is outside the AI budget and needs approval.

## 2. Capability matrix

| Capability | What iOS allows | Plan | Native (Swift)? | Backend support | Where built/tested | Key risks |
|---|---|---|---|---|---|---|
| Secure token storage | Keychain, `ThisDeviceOnly` | `expo-secure-store` | no (JS module) | n/a | dev anywhere; verify on device | Keychain access groups needed only if extensions read secrets (we avoid) |
| Biometric app lock | Face ID/Touch ID/passcode | `expo-local-authentication` | no | n/a | device | Simulator can fake; real check on device |
| Background privacy blur | App switcher snapshot | JS blur overlay on `inactive` | no | n/a | device | Must trigger before snapshot |
| Local notifications | Schedule, permission prompt | `expo-notifications` for reminders fetched from `/coach/reminders/due` | no | **G1** polling only | build in EAS dev client; device to verify | Background refresh limited; local only |
| Remote push (APNs) | Needs APNs key + server sender | **not in M8** | no | **missing (G1)** | n/a | Needs backend work + paid account |
| Share Extension (send text to Abu) | Share sheet text/URL from Messages, Mail, Safari | Swift extension target via config plugin; writes text into an **encrypted App Group inbox**; main app ingests via the authenticated API (`/finance/ingest` or messaging import) | **yes** | Existing endpoints; **G8** scoped credential missing | write in Codespaces; compile in EAS; **verify on device** | Extensions must not hold refresh tokens (rotation race). Until G8, hand-off requires opening the app |
| Shortcuts / App Intents | "Add to Abu" intent; Shortcuts automation on "Message received" can pass the text | Swift App Intents in the main app target (opens app in foreground or runs in app process), calling `ingest` | **yes** | Existing `ingest`; G8 for headless runs | EAS build; **device only** | Automations may require user confirmation depending on iOS version/settings; must be verified on device; cannot read other apps' notifications |
| Bank notification capture | iOS exposes **no** API to read other apps' notifications | Only: Shortcuts automation on SMS received, share-sheet, paste, forward. Stated plainly in onboarding | n/a | `POST /finance/ingest` | device | User expectation management |
| EventKit (calendar read) | Read events after permission | `expo-calendar` -> build ICS payload on device -> `POST /coach/calendar/import`; read-only | no (JS module) but needs dev build | ICS import exists; no bulk JSON | EAS dev client + device with a **synthetic** calendar | Real calendar data is blocked until gates verified; use a dedicated synthetic calendar only |
| Reminders (EventKit reminders) | Read/write system Reminders | **Not planned** (use Abu's own reminders + local notifications) | n/a | n/a | n/a | Avoids extra permission and data flow |
| Voice entry | `SFSpeechRecognizer` (on-device where supported) | Native module returns a **transcript string only**; audio never leaves the device; backend `POST /finance/transactions/voice` takes the transcript | **yes** | exists | EAS + device (simulator mic limited) | Language/on-device availability; permission prompts |
| Camera QR scan (enrollment) | Camera permission | `expo-camera` barcode scan (code string only) | no | G4 (code from CLI) | dev build; device | Optional; typed entry is the fallback |
| Document import | Files picker | `expo-document-picker` -> read to memory -> base64 -> API | no | `finance/import`, `calendar/import`, messaging import | dev client; works in Expo Go | Large files; never persisted; clean the picker cache copy |
| Clipboard (reply drafts) | `UIPasteboard`; iOS 16+ paste prompts | `expo-clipboard` copy only | no | n/a | device | Drafts are private: avoid leaving them indefinitely (clear on timer where the OS allows) |
| Haptics | Taptic engine | `expo-haptics` | no | n/a | device | Respect system settings |
| App icon variants (light/dark/tinted) | iOS 18+ | asset catalogue via config | no | n/a | EAS build | Needs final icon art (P2) |
| Live appearance change | `UITraitCollection` change events | RN appearance listener + `userInterfaceStyle: automatic` | no | n/a | dev anywhere; verify on device/simulator | None significant |
| Dynamic Type, VoiceOver, Reduce Motion | OS accessibility | RN accessibility APIs | no | n/a | device (simulator Accessibility Inspector on Mac) | Needs real-device audit |
| Background fetch for reminders | Opportunistic, OS-throttled | optional `expo-background-task` | no | polling | device | Not reliable; reminders must also work when the app is opened |
| Universal links / deep links | Custom scheme allowlist | `abuai://` only, no mutations | no | n/a | any | Link spoofing: validate and ignore unknown |
| Privacy manifest, permission strings | Required by Apple | config | no | n/a | EAS | Strings must match actual use; just-in-time prompts |
| Widgets / Live Activities | WidgetKit | **Out of scope for M8** | yes | needs data path | n/a | Private data on lock screen; revisit after gates |

## 3. The extension boundary (why it is built this way)

The backend rotates refresh tokens and kills a session on reuse. If an extension and the app both refresh, one of them
will present a stale token and the session dies. Therefore: **only the main app owns the refresh token.** Extensions
write plain data (shared text, an intent's text) into an App Group inbox encrypted with a key kept in the Keychain; the
main app drains the inbox and performs the authenticated call. A future scoped, revocable, ingest-only device credential
(G8, needs backend approval) would let extensions post directly without the refresh token.

## 4. Verification plan for items that cannot be tested here

Each P8 item ships with (a) JS fake + unit tests (run anywhere), (b) a Maestro/manual device script, and (c) an explicit
"verified on device: yes/no, iOS version" line in the phase report. Anything not verified on a device is reported as
**unverified**, never as done.

## 5. Permission strategy

Just-in-time prompts with a pre-prompt explainer: notifications (when the first reminder is created), camera (when scanning),
microphone + speech (first voice entry), calendar (user starts EventKit sync), Face ID (app lock setup). No tracking,
no contacts, no location, no photos. Each permission string states the exact use and that synthetic data is used in this phase.
