# VPS operations guide

For the **existing VPS** (no new server, no purchase). Read `VPS_ASSESSMENT.md` first: nothing below is to be applied until the
host has been assessed and judged suitable, and several items are **owner actions that the repository cannot perform**.
Rule one: **do not disturb existing workloads.** Abu AI runs in its own Compose project with its own networks and volumes.

## 1. Host baseline (verify; the assessment script reports most of it)

| Item | Requirement | How to check |
|---|---|---|
| OS | a supported LTS release with security updates on | `vps-assess.sh` |
| Disk encryption (**G1**) | provider volume encryption or LUKS under Docker's data root, **confirmed in the provider console** | `infrastructure/scripts/verify-encryption-at-rest.sh` (detects dm-crypt only) plus the provider's own statement |
| Time | NTP synchronised (JWT expiry and TLS depend on it) | `timedatectl` |
| Updates | unattended security updates, or a written monthly patch routine | `systemctl is-active unattended-upgrades` |
| Firewall | default **deny inbound**; allow SSH only from the owner's address(es); nothing else in staging | host firewall **and** the provider's cloud firewall |
| SSH | key authentication, root login and passwords disabled | `sshd -T` (root) |
| Docker | Engine >= 24 and Compose v2; the daemon socket is **never** mounted into containers | `docker version` |
| Swap | some swap, so a memory spike does not kill the database | `free -h` |

The repository does **not** change the firewall, DNS, SSH or any package. Those are owner actions.

## 2. Ports (what must and must not be reachable)

| Port | Service | Exposure |
|---|---|---|
| 5432 | PostgreSQL | **never** published; internal Docker network only |
| 4000 | model gateway | **never** published; internal only |
| 8000 | API | **not published** in staging/production (reachable only from Caddy) |
| 8443 | Caddy (staging) | `127.0.0.1` only |
| 80/443 | Caddy (production) | **not authorised**; opened only after owner approval |

If an existing reverse proxy already owns 80/443, production would need a different arrangement (reuse that proxy with a
route to the API). Decide that when production is approved; do not stop the existing proxy.

## 3. Sharing the host safely

- Use `-p abu-ai-staging` for every Compose command; resource limits (`mem_limit`, `pids_limit`) are set so a fault cannot starve
  neighbours. Adjust `DB_MEM_LIMIT`, `LITELLM_MEM_LIMIT`, `API_MEM_LIMIT` to what the assessment shows is free.
- Never run `docker system prune`, `docker volume prune` or `docker compose down -v` on a shared host.
- Do not reuse an existing PostgreSQL server: Abu AI's database is its own container and volume.

## 4. Routine tasks

| Task | How | When |
|---|---|---|
| Backup + verify + prune | `BACKUP_RESTORE.md` (systemd timer) | daily; verify weekly |
| Health check | `MONITORING.md` timer | every 1-2 minutes |
| Retention jobs | `python -m services.admin memory purge-expired`, `orchestrator purge`, `domain purge` via `docker compose run --rm admin ...` | weekly (no scheduler exists yet) |
| Update pinned images | Dependabot PR -> CI -> staging -> merge (`DEPLOYMENT.md`) | weekly review |
| Key custody drill | `KEYRING_RECOVERY.md` section 3 | quarterly |
| Disk check | `ops_check.py --disk` | continuous |
| Gate status | `./infrastructure/scripts/security-gates.sh` | before any decision about real data |

## 5. Things never to do

Read, paste, e-mail or screenshot `.env` or `secrets/`; publish a port by editing a Compose file by hand; run the owner
(`APP_DB_OWNER_*`) credentials in the API or CLI; put real personal, financial or message data into staging; disable a check
to get a deployment through; delete a backup to free space (use `prune`, which keeps the newest and is a dry run by default).
