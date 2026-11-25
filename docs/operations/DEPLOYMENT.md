# Deployment architecture and staging procedure

**Status: nothing has been deployed.** This is the reproducible configuration and the steps for the operator. Staging is
loopback-only and uses synthetic data. A public HTTPS endpoint, DNS and any firewall change need separate owner approval.

## 1. Architecture

```
 internet (NOT in staging)             host loopback only in staging
        |                                     |
   ports 80/443 (production only) ----> Caddy :443  (TLS, security headers, 5 MB body limit, credential-free logs)
                                              |   internal network "proxy" (no internet)
                                              v
                                         api (FastAPI + LangGraph, uid 10001, read-only root, no capabilities)
                                              |   internal network "data" (no internet)
                      +-----------------------+------------------------+
                      v                                                v
   db  PostgreSQL 16 + pgvector (no published port)          litellm  (mock model only, no published port, no internet route)

 one-shot:  migrate  (OWNER role)  migrations -> checkpoint tables -> grants      admin (profile "admin", runtime role)
```

- Only **Caddy** publishes a port. The database, the model gateway and the API publish none.
- The API and the gateway have **no route to the internet**. When a provider is eventually approved (G4), egress is added to
  the gateway only, deliberately, with an allow-list.
- Health: `db`, `litellm` and `api` have health checks and Compose `depends_on` waits for `service_healthy` /
  `service_completed_successfully`; Caddy actively health-checks the API upstream and `ops_check.py` watches all four services.
  `/health` is public liveness, `/health/ready` is authenticated readiness.
- Persistent storage: named volumes `pgdata` (database), `caddy_data` (certificates); `secrets/memory-keyring.json` is a
  host file mounted as a Docker secret. The staging project name (`-p abu-ai-staging`) gives staging its **own** volumes, so it
  cannot touch another stack's data.
- No Kubernetes, Redis, Kafka or other new infrastructure; single VPS, as before.

Files: `docker-compose.yml` (base/development) + `docker-compose.hardened.yml` (shared hardening) +
`docker-compose.staging.yml` / `docker-compose.production.yml`; `caddy/Caddyfile.staging|production`;
`postgres/01-init.sh`; `services/api/Dockerfile`. All images are pinned by digest.

## 2. Staging procedure (operator; after `VPS_ASSESSMENT.md` says the host is suitable)

```bash
git checkout <release-tag-or-commit>            # a reviewed, CI-green commit (RELEASE.md)
ABU_SPLIT_ROLES=1 ./infrastructure/scripts/gen-secrets.sh        # creates .env (600) and the keyring (600); refuses to overwrite
# staging only: say that the data is synthetic (the test embedder is the only one that exists)
sed -i 's/^ALLOW_TEST_EMBEDDER_IN_PRODUCTION=.*/ALLOW_TEST_EMBEDDER_IN_PRODUCTION=true/' .env
COMPOSE="docker compose --env-file .env -p abu-ai-staging -f infrastructure/docker-compose.yml -f infrastructure/docker-compose.hardened.yml -f infrastructure/docker-compose.staging.yml"
$COMPOSE config --quiet && $COMPOSE up -d --build                 # db init -> migrate (owner) -> litellm -> api -> caddy
$COMPOSE ps                                                       # all healthy; migrate "exited (0)"
```

Post-deploy checks (all from the host; nothing leaves it):

```bash
curl -ks https://localhost:8443/health                            # {"status":"ok"}
ABU_OPS_DATABASE_URL=... python3 infrastructure/scripts/ops_check.py --api-url https://localhost:8443 --insecure-loopback \
   --compose-project abu-ai-staging --disk /var/lib/docker --backup-dir /var/backups/abu-ai
$COMPOSE run --rm admin create-user --name "Synthetic tester"     # operator CLI, runtime role
```

Then rehearse backup and restore (`BACKUP_RESTORE.md`) and the monitoring alert path (`MONITORING.md`) on staging.

Stop: `$COMPOSE down` (volumes kept). **Never** add `-v` on a stack with data you need.

## 2b. What was run for real (sandbox, synthetic data, loopback only; the stack was then deleted)

| Check | Result |
|---|---|
| Real final image built from the repository Dockerfile (only difference: the sandbox proxy's CA, via a copy kept outside the repo) | imports ok; uid 10001; **no pip**; revision label set; the guard **refuses** an unsafe production configuration |
| `db` first start with split roles; `migrate` as the owner | database owned by the owner role; the runtime role owns **0** objects; revision `0012`; `migrate` exited 0 |
| `api` started under `ABU_ENV=staging` | healthy: it passed the deployment guard and the startup privilege check |
| `caddy` | only published port is `127.0.0.1:8443`; HTTP/2; HSTS, `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, no `Server` header, `X-Request-ID` |
| Runtime role against the live database | `CREATE TABLE`, `CREATE SCHEMA`, `DROP`, `ALTER`, `TRUNCATE`, `UPDATE alembic_version` all refused; `SELECT` works |
| Network isolation | `api` and `litellm` cannot resolve any external name; only Caddy has a route out |
| End-to-end through TLS | operator creates a user, device enrols, authenticated calls succeed (`/health/ready`: database, pgvector, litellm `ok`); no token -> 401, bad token -> 401, 6 MB body -> 413 at Caddy, enrollment-code reuse -> 401 |
| Credentials in logs | the real access token, refresh token and enrollment code appear in **0** lines of api, caddy, db or litellm logs |
| Query strings | verified **not** logged by Caddy or the API (after a fix; see below) |
| Hardening as applied | api, migrate, caddy: read-only root, all capabilities dropped, no-new-privileges, pids and memory limits |

Three defects were found by running it, and fixed: the **model gateway needs more than 1 GiB** to start (measured 1.09 GiB
steady state; it sat at the 1 GiB cap at ~100% CPU and never became healthy), so the default is now `LITELLM_MEM_LIMIT=2g`;
the gateway tried to download a model-cost map from GitHub at start-up although it has no internet route, so
`LITELLM_LOCAL_MODEL_COST_MAP=True` is set; and **Caddy's access log recorded query strings**, which can carry search text, so
the URI is now logged without its query. Residual: the `litellm` container runs as root inside its container (capabilities
dropped, no-new-privileges, no published port, no internet route); making it non-root needs a compatibility test with the
gateway image and is part of the LiteLLM upgrade (E-4).

## 3. Updating pinned images

Dependabot opens PRs that change a digest. For each: read the release notes, let CI run (including the `security` workflow),
deploy to staging, run the post-deploy checks and the restore rehearsal, then merge. Never replace a digest by hand with a tag.

## 4. What production needs that staging does not (all unapproved)

An owner-approved domain and DNS; ports 80/443 opened deliberately; Caddy upgraded past the known HIGH advisory (E-3); the
LiteLLM image upgraded (E-4); G1-G4 satisfied with evidence; the test embedder replaced by an approved provider (the guard
refuses production until then); real off-host backups and a restore proven on the real host; monitoring routed to a real
channel. See `PRODUCTION_READINESS.md`.
