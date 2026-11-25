# Production readiness

**Verdict: NOT READY, and not authorised.** Phase 1 built a deployment *foundation*. It does not approve production, real
personal data, a public endpoint, native authentication or any provider. Passing repository tests is not production
readiness. Statuses use: **PASS** (evidence demonstrated), **PARTIAL** (implemented, evidence incomplete),
**BLOCKED** (external dependency or owner decision), **NOT STARTED**.

Where evidence came from matters, so each line says which kind it is:
`local` = agent sandbox; `CI` = GitHub Actions; `VPS` = the owner's host; `device` = a physical phone. **No VPS evidence and no
device evidence exists yet.**

## 1. Security gates (`infrastructure/scripts/security-gates.sh` still prints BLOCKED for all four)

| Gate | Status | What exists | What is missing |
|---|---|---|---|
| **G1** disk and volume encryption verified on the real host | **BLOCKED** | read-only host assessment script and criteria (`VPS_ASSESSMENT.md`); `verify-encryption-at-rest.sh`; runbook | **VPS evidence** (assessment output) **and** the provider's statement that the volume is encrypted, or a LUKS volume. A script that says NOT VERIFIED is not proof either way. |
| **G2** independent keyring backup and a rehearsed recovery | **PARTIAL** (gate not passed) | tool, runbooks, key separation enforced; **local** rehearsal on real PostgreSQL with synthetic data: lose keyring -> restore -> database alone unreadable -> keyring recovered from its independent backup -> restored app returns identical plaintext (`BACKUP_RESTORE.md` section 7) | the same drill **on the real host** with a **real off-host destination** and **offline keys**; owner sets `ABU_GATE_KEYRING_BACKUP=verified` |
| **G3** checkpoint metadata is plaintext | **BLOCKED** (owner decision) | reviewed and measured: three structural keys, no content (`G3_CHECKPOINT_METADATA.md`); tripwire test | the owner's decision: accept (A), remove (B) or encrypt (C) |
| **G4** production embedding provider | **BLOCKED** (owner decision) | the deployment guard **refuses production** while only the test embedder exists | an approved provider: privacy terms, retention, training use, pricing, data handling; a migration if the dimension changes |

## 2. Native authentication (R1-R11): unchanged

`NATIVE_AUTH_TRANSPORT_VERIFIED` is **false**. R1-R11 are **NOT MET**; **R7 is BLOCKED** (it requires G1-G4). Phase 1 changed
none of this and does not clear any of it: an HTTPS staging endpoint on a host loopback with a self-signed certificate is not
"the actual approved backend" (R1), no physical-device evidence was gathered, and Expo Go evidence is never transferable
(R11). Enrollment, refresh and authenticated native requests stay disabled.

## 3. Phase 1 requirements

| Req | Status | Evidence kind | Notes |
|---|---|---|---|
| P1.1 release governance | **PARTIAL** | docs | strategy, tags, rollback, PR template written. **Not applied:** branch protection, required checks, first PR and tag (owner actions in `RELEASE.md`). No PR, merge or history rewrite was made. |
| P1.2 VPS suitability | **BLOCKED** | local (script tests) | read-only, redacting, secret-free assessment script is delivered and tested; **no host evidence supplied**, verdict NOT ASSESSED. |
| P1.3 deployment architecture | **PARTIAL** | local, CI | hardened Compose layers, Caddy, fail-closed guard, pinned images; see section 4 for what ran. Not on a VPS. |
| P1.4 database and container hardening | **PARTIAL** | local, CI | role separation proven on real PostgreSQL (32 tests); least-privilege runtime; capabilities dropped, read-only roots, no-new-privileges; digests pinned. Not on a VPS; existing databases untouched. |
| P1.5 backup and recovery | **PARTIAL** | local | encrypted, verified, retained backups; independent keyring backup; rehearsed restore (synthetic). RPO/RTO are **targets**. No off-host copy, no on-host restore. |
| P1.6 observability | **PARTIAL** | local | JSON redacted logs, credential-free access log, anomaly events, health check for every listed signal. **No alert channel exists (owner decision).** |
| P1.7 CI and supply-chain | **PARTIAL** | local, CI | scans run locally with real results; CI jobs are wired; **fixed** a HIGH SAST finding and 5 HIGH image findings; **exceptions E-1..E-4 documented** (`SECURITY_EXCEPTIONS.md`). Branch protection that makes them required is an owner action. |
| P1.8 documentation | **PASS** | docs | README refreshed; runbooks and guides in `docs/operations/`; S2.1A limitations documented. |

## 4. What ran, and where

| Evidence | Kind | Result |
|---|---|---|
| Ruff lint and format, mypy | local | clean (see the Phase 1 report for totals) |
| Full backend pytest (real PostgreSQL 16) | local | see the Phase 1 report |
| Migration tests, role-separation tests, rehearsal, scanners | local | see sections 1-3 and `SECURITY_EXCEPTIONS.md` |
| Real final API image built and scanned | local (CA-injected copy of the Dockerfile outside the repo) | 0 fixable HIGH/CRITICAL; 53 HIGH with no upstream fix (E-1) |
| Hardened staging stack started for real in the sandbox (synthetic data, loopback only, own project, then deleted): owner/runtime roles, guard, Caddy/TLS, headers, DDL denied, network isolation, enrol -> authenticated calls, no credentials in logs, health check, failure-and-recovery alert drill, live backup pipeline | local | **PASS** in the sandbox (`DEPLOYMENT.md` section 2b); found and fixed 3 defects |
| `.github/workflows/*` | CI | recorded in the Phase 1 report with run links |
| Anything on the owner's VPS | VPS | **none** |
| Anything on a phone | device | **none** |

## 5. Blockers before any production launch (all must be closed; none is closed)

1. G1-G4 satisfied **with evidence** (section 1), and the owner's approval recorded.
2. VPS assessed and judged suitable; staging, backup, restore, monitoring and alert path all demonstrated **on that host**.
3. An alert channel chosen and tested (owner).
4. **LiteLLM upgraded** past the 16 CRITICAL / 205 HIGH fixable findings (E-4) with the compatibility tests re-run.
5. **Caddy upgraded** past the HIGH `golang.org/x/net` advisory (E-3) before any public exposure.
6. A production embedding provider approved (G4); the guard refuses production until then.
7. Public endpoint, DNS and firewall changes approved separately; production overlay reviewed.
8. Branch protection and required checks applied; first release PR reviewed; the `security` jobs green.
9. Finance correctness: open S2.0 findings triaged and real-format validation approved (no real statement or SMS format has
   ever been tested).
10. Data rights: export and complete erasure exist only for memory; finance, messaging and coach have none (see section 7).
11. Privacy/legal review for personal financial and third-party message data (owner and a qualified adviser).
12. Native authentication, if it is to be used: R1-R11 evidence on a physical device (separate approval).

## 6. Owner decisions waiting

| # | Decision | Why it is needed |
|---|---|---|
| 1 | Run the VPS assessment script and share the output; confirm provider disk encryption and any cloud firewall | G1, P1.2 |
| 2 | Apply branch protection and required checks; decide when to open the first PR and at which commit | P1.1, P1.7 |
| 3 | Choose the alert channel and who must be reached | P1.6 |
| 4 | Choose the off-host backup destination; hold K1 and K2 offline | G2, P1.5 |
| 5 | G3: accept (A), remove (B) or encrypt (C) | G3 |
| 6 | G4: whether and which embedding provider; its terms | G4 |
| 7 | Schedule the LiteLLM upgrade (recommended first task of the next phase) and the Caddy upgrade | E-3, E-4 |
| 8 | Approve RPO 24 h / RTO 4 h or change them | P1.5 |
| 9 | CodeQL / GitHub Advanced Security, Dependabot security updates, secret-scanning push protection (repository settings) | P1.7 |

## 7. Carried-forward Finance limitations (S2.1A, accepted; documented in the README)

1. Bulk confirmation keeps its legacy behaviour for rows without a `review_reason`; unknown debits are not newly routed into review.
2. Unsplit EMI can make the summary `net` overstate available cash; a finance UI must not present `net` as available cash.
3. Legacy EMI rows are not backfilled; explicit user action is required to move them into the new classification model.

Not addressed by design: S2.1B, S2.1C, S2.2 and findings FIN-04/05/06/06a/07/08/10/12/13/14.

## 8. Other gaps recorded, not fixed here

- Data export does not exist; erasure exists for memory only (`memory erase-user`), not for finance, messaging or coach.
- Rate limits are per process and in memory; spend reservations serialise on one row lock (single user by design).
- HS256 shared secret; refresh tokens are bearer tokens, not device-bound.
- Embeddings are stored unencrypted (G1 and backups encryption are the mitigations).
- Retention jobs are manual operator commands; there is no scheduler.
