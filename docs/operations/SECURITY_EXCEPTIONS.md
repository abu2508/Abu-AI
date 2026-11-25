# Security scan results, policy and accepted exceptions

Scope: Phase 1 (P1.7) supply-chain and static checks. Scans below were run on **2026-11-25** in the agent sandbox against
commit lineage `f95cff4` + Phase 1; CI (`.github/workflows/security.yml`) repeats them on every push, pull request and
weekly. **Local results are not CI results.** CI results are reported separately in the Phase 1 report.

## 1. Policy

| Check | Blocks the build when | Never done |
|---|---|---|
| `dependency-audit` (pip-audit) | any known vulnerability in the locked production or dev dependencies, or a dependency that cannot be audited (`--strict`) | lowering the severity, ignoring an advisory id |
| `sast` (Bandit) | any finding of any severity or confidence in application code | `# nosec` comments, severity filters |
| `secret-scan` (gitleaks) | any finding in the git history that is not fingerprint-listed in `.gitleaksignore` | path-wide or rule-wide allowlists |
| `image-scan` (Trivy), our API image | a **fixable** HIGH or CRITICAL vulnerability, or a HIGH/CRITICAL Dockerfile misconfiguration | `.trivyignore` entries |
| `image-scan`, report step | never blocks, but **always prints** every HIGH/CRITICAL finding including those with no upstream fix | hiding unfixed findings |
| `image-scan`, third-party images | never blocks (we cannot patch them) but every finding is printed and tracked below | silence |
| `actions-lint` | an Action not pinned to a full commit SHA, write permissions, a job without a timeout, event data in a shell command, or an actionlint error | tag-pinned Actions |

"Fixable" means the scanner reports a fixed version. A finding with no fix is not silently ignored: it is printed on every
run and must have an entry in section 4 with compensating controls, an owner action and a review date. An exception is
**never** permanent: each one expires on its review date and must be re-confirmed or fixed.

## 2. Findings that were fixed in Phase 1

| # | Finding | Fix | Evidence |
|---|---|---|---|
| F-1 | Bandit **B613** (HIGH) in `core/memory/privacy.py` and `domain/messaging/importers.py`: invisible bidirectional/zero-width characters written as raw literals in source ("Trojan Source" shape; a reviewer sees different text from what runs). | Written as `\uXXXX` escapes. Behaviour identical: zero-width table and the control-character and WhatsApp-line patterns compared old vs new over all 65,536 BMP characters. | commit `141c9eb`; 671 related tests pass; Bandit now 0 findings |
| F-2 | Trivy: **5 fixable HIGH** in the base image's system `pip` (vendored `msgpack` 1.1.2, `urllib3` 2.7.0, `setuptools` 70.3.0). The application never uses `pip` at runtime. | The runtime image has no package manager (`pip`, `setuptools`, `wheel` removed after dependencies are installed). | Verified offline on a derived image: fixable HIGH/CRITICAL went from 5 to **0**; stdlib `ssl`, `sqlite3` etc. still import. The full API image is scanned in CI. |
| F-3 | gitleaks: 3 findings in 76 commits. | **Reviewed, not secrets:** (a) a synthetic string in a redaction unit test, (b) the mobile test fixture `rt_abcdef012345`, (c) the code reference `body.refresh_token`. Recorded fingerprint-exact in `.gitleaksignore`. | `.gitleaksignore`; gitleaks exits 0 with it |
| F-4 | Compose: the model gateway (LiteLLM) had a route to the internet although only a mock model exists. | Removed in the hardened layers (`litellm` joins only the internal `data` network). | `test_the_model_gateway_has_no_internet_route` |
| F-5 | Caddy could have trusted a client-supplied `X-Forwarded-For` when Docker presents peers from a private address (per-IP rate-limit bypass). | `trusted_proxies` removed: Caddy trusts no upstream and overwrites the header. | `test_caddyfile_hardening` |

## 3. Clean results

| Check | Result (local, 2026-11-25) |
|---|---|
| pip-audit, production set | 66 dependencies, **0** known vulnerabilities |
| pip-audit, all groups | 78 dependencies, **0** known vulnerabilities |
| Bandit, 18,831 lines of application code | **0** findings (after F-1) |
| Trivy misconfiguration scan, Dockerfile | **0** HIGH/CRITICAL |
| actionlint, 4 workflows | **0** errors |
| Caddy `validate`, both Caddyfiles, real Caddy 2.11.9 | valid |

## 4. Accepted exceptions (review by **2026-12-25**)

Severity is the scanner's. "Blocks" says what the exception stops, so it cannot be forgotten.

### E-1  Base image OS packages with no upstream fix (our API image)
- **What:** `python:3.13-slim` (Debian 13.8) carries **53 HIGH** and 0 CRITICAL findings in OS packages with **no fixed version
  published** (`util-linux` family `bsdutils/mount/login/libblkid1/libmount1/libuuid1/libsmartcols1/liblastlog2-2`, `openssl`
  3.5.9 / `libssl3t64`, `libsqlite3-0`, `ncurses`, `libsystemd0/libudev1`, `libacl1`, `perl-base`). 77 LOW, 76 MEDIUM.
- **Why not fixed:** Debian has not released fixes; the digest we pin is the current upstream image.
- **Compensating controls:** runs as non-root uid 10001; read-only root filesystem; all Linux capabilities dropped;
  `no-new-privileges`; no network-exposed shell and no package manager in the image; the API has **no route to the internet** and no published
  port (reachable only from Caddy); the util-linux tools are not invoked by the application; OpenSSL is used only for
  loopback/internal connections.
- **Owner action:** none now. Dependabot (docker) raises a PR when a newer base digest exists; accept it after CI is green.
- **Blocks:** nothing today. Re-evaluate before any production launch.

### E-2  Database image: fixable HIGH/CRITICAL in `gosu` and OS packages
- **What:** `pgvector/pgvector:pg16` (Debian 12.15): **25 fixable HIGH + 1 fixable CRITICAL** (mostly Go standard-library CVEs in the
  bundled `gosu` binary, e.g. CVE-2025-68121), plus 114 HIGH / 3 CRITICAL with no fix.
- **Why not fixed:** the pinned digest is the newest published `pg16` image; the fix is an upstream rebuild.
- **Compensating controls:** `gosu` only runs once, at container start, as the root-owned entrypoint to drop privileges; it
  processes no network input. PostgreSQL has no published port and sits on an internal-only network.
- **Owner action:** none. Refresh the digest when upstream publishes (Dependabot PR).
- **Blocks:** nothing today. Re-evaluate before production.

### E-3  Caddy: `golang.org/x/net` v0.60.0, CVE-2026-78661 (HIGH, fixed in 0.62.0)
- **What:** the pinned `caddy:2.11.9-alpine` (the newest tag found) is built with the vulnerable library.
- **Why not fixed:** no Caddy release with the fixed library was available when checked.
- **Compensating controls:** staging listens on the host loopback only; the production overlay is not authorised and cannot
  start without a domain.
- **Blocks: ANY PUBLIC EXPOSURE.** Caddy is the one component that would face the internet. Before a public endpoint is
  approved, upgrade Caddy (or build it with the fixed library) and re-scan. Listed in the production-readiness checklist.

### E-4  Model gateway: LiteLLM `v1.83.14-stable.patch.3`
- **What:** **16 fixable CRITICAL and 205 fixable HIGH** findings, overwhelmingly in the Python libraries bundled in the image
  (for example `pypdf`, `aiohttp`, `pyasn1`, `tornado`, `PyJWT`, `urllib3`, `mcp`) and its Alpine-based OS layer
  (`python-3.13`, `busybox`, `py3-pip-wheel`).
- **Why not fixed in Phase 1:** the fix is a gateway upgrade. That changes the LiteLLM version, which the repository pins and
  tests (the UI-replacement mount path inside the package changes between versions), and the live-gateway test is skipped in CI.
  It needs deliberate compatibility work and an owner decision, not a silent bump. I could not enumerate newer tags from the
  sandbox.
- **Compensating controls (all verified in tests):** only a mock model is configured and **no provider key exists**; **no
  published port**; the gateway now has **no route to the internet** (F-4); it is reachable only on the internal network;
  administration UI and docs are disabled; the API is its only client and holds the only master key; `no-new-privileges`, all
  capabilities dropped.
- **Blocks:** **enabling any real provider, and any real data.** Exit criteria: upgrade to a release without fixable
  HIGH/CRITICAL findings, re-run the compatibility tests, then (separately, G4) re-add egress to a provider allow-list.
- **Owner decision needed:** schedule the LiteLLM upgrade (recommended as the first task of the next phase).

### E-5  Scan limits (an honest gap, not an exception to a finding)
- The **final API image was built and scanned in the sandbox** with a CA-injected copy of the Dockerfile that lives outside the
  repository (the agent's egress proxy breaks `uv sync` inside `docker build`; the only difference from the CI image is one added
  CA certificate). Result: **0 fixable HIGH/CRITICAL**; 53 HIGH with no upstream fix (E-1); 2 MEDIUM + 2 LOW fixable in Python
  packages and the `uv` binary (informational). CI builds the committed, unmodified Dockerfile and its result is authoritative.
- No SAST beyond Bandit and Ruff's `S` rules (CodeQL needs repository settings and, for private repositories, GitHub Advanced
  Security; owner decision).

## 5. Review log

| Date | Reviewer | Outcome |
|---|---|---|
| 2026-11-25 | Phase 1 implementation | Register created. E-1..E-4 accepted until 2026-12-25 pending the actions above. Not a security sign-off. |
