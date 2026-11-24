# Optional user-run check: what the Expo CLI loads and spawns on Windows (SDK 58)

Status: **documentation only; nothing here has been run on Windows.** It belongs to the SDK 58 upgrade plan, section 11.9 (`SDK58_UPGRADE_PLAN.md`). It is an optional evidence step that **you** run on your own PC after the SDK 58 dependencies are installed, and it is read-only: it logs which of the new packages are loaded and which child processes are started, and it changes no project file. Nothing in it needs a credential, and nothing leaves your PC.

Why: the supply-chain review (plan section 11.3) found that `2g` is loaded by every Expo CLI run, and that `noxcturnal`, `expo-modules-cli`, `expo-modules-macros` and the native binaries are **not** loaded or started during `expo export` and `expo start` on **Linux x64**. Your machine is **Windows**, where `2g` uses a named pipe instead of a Unix socket and different binaries are installed (`win32-x64` or `win32-arm64`). The Linux result does not prove the Windows result.

## What I verified already (Linux x64, Node 22.23.5, SDK 58 preview) and what I did not
| Claim | Status |
|---|---|
| `2g` is `require`d by `@expo/cli` on every run (43 load events in one `expo start`) | **Verified on Linux** |
| `2g` creates `/tmp/event-log-<uid>/<session>/` (mode 0700) holding `0.jsonl` and `meta.json` (mode 0600) and listens on two **Unix-domain sockets** inside that directory (`ipc.sock`, `live.sock`); no TCP listener comes from `2g`; the sockets are removed when the CLI exits | **Verified on Linux** |
| On Windows `2g` uses a **named pipe** (`\\.\pipe\event-log-<pid>-<time>-<random>-ipc`, from the source of `publishTempIpcSink`) | **Read in source only; not run on Windows** |
| The pipe's access control (who may connect) | **Unverified** (Node/libuv defaults; I did not inspect them) |
| The session logs live under `%TEMP%\event-log-<uid>\...` on Windows | **Assumption** from `os.tmpdir()`; not run |
| `noxcturnal`, `@noxcturnal/*`, `expo-modules-cli`, `expo-modules-macros`, `@expo/modules-macros-*` are not loaded and nothing is spawned for them during `expo start --web` with a full web bundle request, or `expo export` | **Verified on Linux only** |
| Same on Windows (`expo start --lan` for your iPhone, `expo export`) | **Unverified: this check** |
| `ExpoModulesMacros.exe` (Windows, Swift-built) imports `ws2_32.dll`, `CreateProcessW`, `OpenProcessToken` | **Verified from its import table**; whether any code path uses them is **unverified**; it was never executed |

## Steps (PowerShell, after the SDK 58 install; do not run before the implementation is approved and committed)
1. Confirm which platform binaries `npm ci` installed (read-only):
   ```powershell
   node -p "process.platform + ' ' + process.arch"
   Get-ChildItem .\node_modules\@noxcturnal, .\node_modules\@expo | Where-Object { $_.Name -match 'noxcturnal|macros' } | Select-Object Name
   ```
   Expected: `win32 x64` and exactly one `noxcturnal-win32-x64-msvc` and one `modules-macros-win32-x64` (if your CPU is ARM64, the `arm64` ones).
2. Create the trace helper **outside the repository** (the path below contains your user name with a space, so it is quoted when used):
   ```powershell
   $trace = "$HOME\abu-expo-trace.js"
   @'
   const M = require('module'), fs = require('fs'), cp = require('child_process'), net = require('net');
   const out = process.env.TRACE_OUT;
   const log = (k, v) => fs.appendFileSync(out, `${process.pid} ${k} ${v}\n`);
   const orig = M._load;
   M._load = function (req) {
     if (/^(noxcturnal|@noxcturnal\/|2g$|expo-modules-cli|expo-modules-macros|@expo\/modules-macros)/.test(req)) log('LOAD', req);
     return orig.apply(this, arguments);
   };
   for (const fn of ['spawn', 'spawnSync', 'execFile', 'execFileSync', 'exec', 'execSync', 'fork']) {
     const o = cp[fn];
     cp[fn] = function (c, a) {
       const s = String(c) + ' ' + (Array.isArray(a) ? a.join(' ').slice(0, 80) : '');
       log(/ExpoModulesMacros|noxcturnal|2g|expo-modules/.test(s) ? 'SPAWN-TARGET' : 'spawn-other', s.slice(0, 140));
       return o.apply(this, arguments);
     };
   }
   const lis = net.Server.prototype.listen;
   net.Server.prototype.listen = function (...a) { log('LISTEN', JSON.stringify(a.filter((x) => typeof x !== 'function')).slice(0, 120)); return lis.apply(this, arguments); };
   '@ | Set-Content -Path $trace -Encoding utf8
   ```
3. Run the normal development command with the helper attached, in the `mobile` folder (the quoting matters because of the space in the path):
   ```powershell
   cd "$HOME\Abu-AI\mobile"
   $env:TRACE_OUT = "$HOME\abu-expo-trace.log"
   Remove-Item $env:TRACE_OUT -ErrorAction SilentlyContinue
   $traceFwd = $trace -replace '\\', '/'
   $env:NODE_OPTIONS = "--require `"$traceFwd`""
   $env:NODE_OPTIONS
   npx expo start --lan
   ```
   **Use forward slashes in `NODE_OPTIONS`.** Node reads a backslash inside the quotes as an escape character, so `C:\Users\Abdullah B\abu-expo-trace.js` becomes `C:UsersAbdullah Babu-expo-trace.js` and Node fails with `Cannot find module` before Expo even starts (this happened on the first attempt; nothing is traced and nothing is harmed). The line `$env:NODE_OPTIONS` prints the value: it must read `--require "C:/Users/Abdullah B/abu-expo-trace.js"`. If Node ever fails to start, run `Remove-Item Env:NODE_OPTIONS` first.
   Open the app once on the iPhone (so Metro bundles), then stop Expo with `Ctrl+C`. Then clear the variable: `Remove-Item Env:NODE_OPTIONS`.
4. While Expo is running (a second PowerShell window), look at what `2g` created (read-only):
   ```powershell
   Get-ChildItem "$env:TEMP\event-log-*" -Recurse -ErrorAction SilentlyContinue | Select-Object FullName, Length, LastWriteTime
   [System.IO.Directory]::GetFiles("\\.\pipe\") | Where-Object { $_ -like '*event-log*' }
   ```
5. Summarise the trace (read-only) and send me the output, not the file:
   ```powershell
   Get-Content $env:TRACE_OUT | ForEach-Object { ($_ -split ' ',3)[1..2] -join ' ' } | Group-Object | Sort-Object Count -Descending | Select-Object -First 25 Count, Name
   ```
6. Clean up: `Remove-Item "$HOME\abu-expo-trace.js","$HOME\abu-expo-trace.log"`.

## How to read the result
- **Expected, if Windows matches Linux:** many `LOAD 2g` lines; `LISTEN` lines for your Metro port and one or two `\\.\pipe\...` paths; **no** `LOAD noxcturnal`, **no** `LOAD expo-modules-*`, **no** `SPAWN-TARGET` lines.
- **Anything else is a finding to report, not to fix:** a `LOAD noxcturnal` line would mean the opt-in transform worker is enabled on Windows; a `SPAWN-TARGET` line naming `ExpoModulesMacros` would mean the macros binary is started by the dev server.
- The helper logs module names and command names only (no environment values, no file contents, no arguments beyond the first 80 characters of a spawn).
- A clean result is evidence for **this** machine, SDK 58.0.26 and this configuration on the day it was run; it is not a general assurance (plan section 11.3 and 11.9 list what stays unverified).

## What `2g` is, and the "spawned process"
`2g` has two parts. The **library** is what `@expo/cli` uses: it records structured JSONL events (build configuration flags, Metro progress, URLs, environment-variable **names**) into the session directory above and offers a local IPC endpoint so worker processes can add events to the same session. The **`2g` command line** includes `2g record -- <command>`, which starts the command you give it as a child process, attaches to its events and writes a trace; the source shows this (`child_process.spawn(<your command>)`). `@expo/cli` does not run that command-line tool: I found no `spawn` of `2g` in `@expo/cli`, and the trace of `expo start` and `expo export` showed no spawn of it. So the only process `2g` itself ever starts is the command a person passes to `2g record`. This is verified from source and from the Linux trace; it is **unverified on Windows**.
