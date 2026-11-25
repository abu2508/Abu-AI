# Release governance

Status: **written strategy only.** Nothing in this document has been executed: no pull request exists, no branch has been
merged, no tag has been created, branch protection has not been changed, and history has not been rewritten. Every action
that changes GitHub settings or `main` is listed under [Owner actions](#owner-actions) and needs the repository owner.

This is a **non-production foundation.** A release candidate defined here is a *reviewable build*, not an approval to run
Abu AI with real personal data. The four security gates (G1-G4) and the native-authentication requirements (R1-R11) are
tracked in `docs/operations/PRODUCTION_READINESS.md` and are independent of the release process.

## 1. Current state (recorded at the Phase 1 baseline)

| Fact | Value |
|---|---|
| Accepted baseline | `f95cff4` (S2.1A accepted and closed) |
| Working branch | `claude/charming-davinci-vqv0du`, in sync with its remote |
| `main` | `87f51e5`, the single "Initial commit"; unprotected |
| Relationship | `main` is a **direct ancestor** of the working branch: 0 commits behind, 68 ahead |
| Merge shape | a fast-forward is possible: there is **no divergence** and no conflict risk |
| Tags / releases | none |

Because `main` is an ancestor, the release question is not "how do we reconcile two histories" but "how do we review 68
commits before they become the base for everything else". The strategy below keeps that review reversible.

### Release inventory (`main..f95cff4`: 507 files, +97,329 / -1)

| Area | Files | Contents |
|---|---|---|
| `mobile/` | 158 | Expo app, tests, scripts, generated API types, OpenAPI snapshot |
| `docs/` | 122 | mobile plans, reports, native-gate documents, design |
| `tests/` | 60 | backend unit, integration, contract, security tests |
| `core/` | 48 | orchestrator, memory, governance, model router, registries |
| `services/` | 38 | API, admin CLI, LiteLLM config |
| `database/` | 31 | schemas, 12 Alembic migrations (0001-0012), seeds |
| `domain/` | 24 | finance, messaging, coach |
| `infrastructure/`, `.github/`, root | 19 | Compose files, scripts, 3 workflows, `pyproject.toml`, `uv.lock`, README |
| `agents/` | 7 | domain agents |

Review order that matches risk: (1) `database/migrations` and `infrastructure/` (irreversible or operational), (2)
`services/api` and `core/` authentication, governance and memory (security), (3) `domain/finance` (S2.1A, already accepted),
(4) workflows, (5) everything else.

## 2. Branch strategy

- `main` is the **release branch**: only reviewed, CI-green changes land on it, and every release is a tag on `main`.
- Work happens on short-lived topic branches (`claude/*` for agent sessions, `feature/*`, `fix/*`, `docs/*`).
- There is no long-lived `develop` branch. One user, one deployment: an extra branch adds merge work and no safety.
- Direct pushes to `main` are not part of the procedure once protection is on (see Owner actions).

## 3. Bringing the existing 68 commits onto `main` (proposal, not executed)

Recommended: **one pull request from the accepted baseline commit, merged with a merge commit.**

1. Open a PR `claude/charming-davinci-vqv0du` -> `main` **at a named commit**, not at a moving branch tip. The accepted
   baseline is `f95cff4`; Phase 1 adds commits after it, so decide whether the PR covers `f95cff4` only (cleanest audit) or the
   Phase 1 tip. Recommended: first PR = `f95cff4` (the accepted product baseline), second PR = Phase 1.
2. Merge with **"Create a merge commit"**, not squash and not rebase. Reasons: the 68 commits are already a meaningful
   history (milestones, reports); squashing would destroy per-milestone `git bisect` and `git revert` granularity; rebasing
   would rewrite hashes that the acceptance reports quote.
3. Tag the merge commit `v0.1.0-rc.1` (see section 5).
4. **Rollback of the merge** is `git revert -m 1 <merge-commit>` as a normal reviewed PR. `main` is never force-pushed.

Alternatives considered and rejected: fast-forward push of the branch to `main` (works, but skips review and leaves no
merge point to revert); squash (loses history); rebase (rewrites quoted hashes).

Nothing here is destructive: the branch stays in place until the owner deletes it, and the baseline hash remains reachable.

## 4. Required checks for a release candidate

A release candidate is mergeable only when **every** check below is green on the exact commit being merged.

| Workflow | Check (job) | Purpose |
|---|---|---|
| `backend` | `ruff` | lint and format |
| `backend` | `mypy` | strict type check |
| `backend` | `pytest` | full tests/ tree on PostgreSQL 16 + pgvector |
| `backend` | `docker` | API image build and Compose validation (placeholder values) |
| `backend-openapi` | `openapi-snapshot` | committed OpenAPI snapshot matches the backend |
| `mobile` | `checks` | types, lint, tests with coverage, supply-chain gates, secret scan |
| `security` | `dependency-audit` | backend dependency vulnerabilities |
| `security` | `sast` | static application security testing |
| `security` | `secret-scan` | repository secret scan |
| `security` | `image-scan` | container image vulnerabilities |
| `security` | `actions-lint` | workflow permissions and SHA-pinning check |

Rules:
- A check is never disabled, skipped or made non-blocking to get a release through. If a check is wrong, fix the check in
  its own reviewed PR with an explanation.
- Path filters on the workflows mean a docs-only change may not trigger every check. A **release PR must be validated with a
  manual `workflow_dispatch` run of every workflow** (all of them support it), so a release is never merged on stale results.
- Findings accepted as exceptions live in `docs/operations/SECURITY_EXCEPTIONS.md` with severity, reason, owner and expiry.

## 5. Versioning and tags

- Scheme: **SemVer** `vMAJOR.MINOR.PATCH`, with pre-release suffixes `-rc.N` for release candidates, e.g. `v0.1.0-rc.1`.
- `0.x` means "no stability promise and no production approval". `1.0.0` is reserved for the first release approved for real
  data, which also requires G1-G4 and the production-readiness checklist.
- Tags are **annotated** (`git tag -a`), created only on `main`, and are never moved or deleted. A mistake is corrected by a
  new tag.
- The tag message records: the commit, the CI run ids, the Alembic head revision, and the image digests.
- Alembic revisions are numbered independently (`0012` today). Every release notes its head revision. A release that
  contains a migration says so in its notes.

## 6. Release identification

A running deployment must be identifiable without trusting anyone's memory:

- Container images are built with the OCI labels `org.opencontainers.image.revision` (git SHA) and `.version` (tag), supplied
  as build arguments (see `services/api/Dockerfile`).
- Deployments reference images **by digest**, recorded in `infrastructure/image-digests.env` (see
  `docs/operations/DEPLOYMENT.md`).
- The deployed revision is read from the image (`docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}'`)
  and the database revision from `alembic current` (run in the `migrate` service); both are compared with the release notes
  before and after every deploy. (`services.admin version ...` manages *agent* versions and is unrelated.)

## 7. Pull request and merge procedure

1. Branch from current `main`.
2. Keep one concern per PR. Commit messages follow the existing style (imperative summary, reason in the body).
3. Open the PR. CI must be green on the head commit. Run all workflows manually if path filters skipped any.
4. Self-review the diff against the risk-ordered list in section 1. The owner is the sole reviewer today; the PR template
   asks the author to state: what changed, what could break, how it was tested, whether a migration or a config change is
   involved, and whether any security gate or authentication requirement is affected.
5. Merge with a merge commit (or squash for single-purpose topic branches where the history has no value).
6. Delete the topic branch after merge. Tag on `main` when a release is cut.
7. Pull requests that touch any of the following get a **second, explicit owner confirmation in the PR body** before merge:
   `database/migrations/`, `infrastructure/`, `.github/workflows/`, authentication, `core/governance/`, or any file that
   changes a security gate or `NATIVE_AUTH_TRANSPORT_VERIFIED`.

## 8. Cutting a release

1. Confirm `main` is the commit to release and all required checks are green on it (manual dispatch if needed).
2. Build the images in CI; record the digests.
3. Take a **pre-release backup** (`docs/operations/BACKUP_RESTORE.md`) of the target environment.
4. Create the annotated tag and release notes: changes, Alembic head, migrations (yes/no), config changes, known issues,
   rollback instructions.
5. Deploy to **staging first** (`docs/operations/DEPLOYMENT.md`); run the post-deploy checks; only then consider any other
   environment.

## 9. Rollback

| Layer | Rollback |
|---|---|
| Code / image | redeploy the **previous release's image digest** (recorded in its tag message). No rebuild. |
| Configuration | environment files are versioned per release in the operator's private store; restore the previous one. |
| Database, compatible migration | migrations are additive; the previous image keeps working with the new schema. Prefer this. |
| Database, incompatible migration | **restore the pre-release backup** (rehearsed procedure) rather than `alembic downgrade` on live data. A downgrade is available and tested, but it drops columns and so discards the data in them. |
| Bad merge on `main` | `git revert -m 1 <merge>` through a reviewed PR; never force-push `main`. |

The order is: stop traffic (kill switch / stop the API), restore, verify health and the Alembic head, resume. Record the
event using `docs/operations/INCIDENT_RESPONSE.md`.

## Owner actions

These cannot be done from the agent environment (the available GitHub tools cannot change repository settings) and were
**not** done:

1. **Protect `main`** (Settings -> Branches -> Add rule, or a ruleset): require a pull request before merging; require the
   status checks in section 4 to pass and the branch to be up to date; block force pushes and deletions; (optionally)
   require linear history only if you reject merge commits, which this document does not recommend.
2. **Add the checks to the rule** only after each has run green once, otherwise the rule blocks every merge.
3. **Decide** when to open the first PR and at which commit (section 3).
4. **Enable** Dependabot alerts and security updates, secret scanning and push protection in repository settings, if the
   plan allows (private repositories need GitHub Advanced Security for some of these).
5. **Restrict workflow permissions** to read-only by default (Settings -> Actions -> General).
6. Create the first annotated tag when the first release PR is merged.
