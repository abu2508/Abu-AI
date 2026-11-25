# Incident response

Single-user system; the "team" is the owner. The aim is a short, rehearsed sequence: **contain, preserve evidence, recover,
learn.** Write what you did as you do it (time, action) in a private log; **never paste secrets or content into it.**

## 0. Immediate containment tools (all exist today)

| Goal | Command |
|---|---|
| Stop new model work now | `docker compose ... run --rm admin kill-switch activate --reason "<short>"` (in-flight calls finish) |
| Revoke every device session | `... admin sessions list --user <id>` then `sessions revoke <session-id>` |
| End **all** sessions at once | rotate `JWT_SECRET` and restart the API (every access token fails; refresh needs re-enrollment) |
| Stop all paid spending | `... admin spend paid off` (default is off) |
| Stop the API from the network | `docker compose ... stop caddy` (then `api`) |
| Preserve state | take a backup first (`BACKUP_RESTORE.md`) before changing anything destructive |

## 1. Triage by signal

| Alert / symptom | Likely meaning | First moves |
|---|---|---|
| `auth:auth.refresh_reuse_detected` (any) | a rotated refresh token was replayed: **possible token theft** | revoke that user's sessions; kill switch if unsure; review `audit_events` around the time; re-enrol the device; rotate `JWT_SECRET` if more than one session is involved |
| `auth:enrollment.failed` spike | guessing enrollment codes | codes are single-use, 128-bit, 10 minutes: low risk; check rate limits held; consider shortening exposure (stop Caddy) |
| `auth_anomaly` log event | burst of 401/403/429 | find the source in Caddy logs by time (addresses are not logged by design; use the host firewall's logs); tighten the firewall |
| `spend` CRIT (frozen) | unknown cost / possible charge | do **not** unfreeze; reconcile with the provider's invoice (`spend resolve`); then `spend unfreeze --reason` |
| `backup` CRIT | backup failed / stale / off-host failed | run it by hand; read the error; fix; take one **before** doing anything else risky |
| `schema_revision` CRIT | database and release disagree | stop the API; check what ran; restore the pre-release backup if a migration failed (`RELEASE.md` rollback) |
| `database` CRIT | PostgreSQL unreachable | `docker compose ps`; check disk; do **not** delete the volume |
| `disk` CRIT | >= 90% | free logs/old backups via `prune` (never delete the newest backup); investigate growth |
| `container:*` CRIT | service down/unhealthy | `docker compose logs --tail 200 <svc>` (logs are redacted); restart once; escalate if it repeats |

## 2. Suspected secret or key exposure

1. **Contain**: kill switch; stop Caddy if the exposure is external.
2. **Rotate** what was exposed: `JWT_SECRET`/`TOKEN_HASH_KEY` (ends all sessions), database passwords (alter the role, update `.env`),
   `LITELLM_MASTER_KEY`, provider keys (revoke at the provider first).
3. **Keyring exposed:** treat all encrypted content as readable by whoever holds a database copy. Add a new key and re-wrap
   (`memory add-key`, `memory rotate-keys`), take a **new keyring backup**, then decide with the owner whether older database
   copies and backups must be destroyed (rotation does not re-encrypt copies that already exist).
4. If the secret was ever committed: it is compromised permanently; rotate, and note that git history rewrites do not undo copies.

## 3. Data-loss or corruption

Restore into a **new** database (`BACKUP_RESTORE.md` section 6), prove readability with `recovery-check`, then switch. Keep the
damaged database until the restored one is verified.

## 4. After every incident (within a week)

Record: what happened and when, how it was detected (alert or by chance), what contained it, data possibly affected, what was
rotated, the gap that let it happen, and the change made. Add a test or an alert where one was missing. If real personal data
was ever involved, assess notification duties with a qualified adviser; this repository does not provide legal advice.

## 5. Not yet in place

A paging channel (owner decision), an off-host log copy, and a rehearsed on-host recovery (`PRODUCTION_READINESS.md`).
Until those exist an incident is detected only if someone reads the check output.
