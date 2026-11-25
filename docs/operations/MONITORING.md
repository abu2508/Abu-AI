# Monitoring, alerting, logs and retention

**Provider-neutral by design.** The repository detects and reports; *where an alert is delivered* (e-mail, chat, pager) is an
**owner decision that has not been made**, so no external provider is selected, configured or contacted. Until the owner
chooses, alerts can be read from the check's output and its state file.

## 1. What is monitored

`infrastructure/scripts/ops_check.py` (one JSON report; exit 0 OK / 1 WARN / 2 CRIT):

| Signal | Source | WARN | CRIT |
|---|---|---|---|
| API availability | `GET /health` | | unreachable or unexpected body |
| Database and schema | runtime-role `SELECT`s | | unreachable, query fails, **schema revision differs from the release** |
| Container health | `docker compose ps` | | any of db, litellm, api, caddy missing, not running, or unhealthy |
| Disk | `statvfs` of the paths you give (Docker root, backups) | >= 80% used | >= 90% used |
| **Backup success / failure** | `status.json` written by `abu_backup.py` | newest success older than the limit | older than 2x the limit, last attempt failed, **off-host copy failed**, or never recorded |
| **Kill switch** | `kill_switch_state` | active (system is deliberately halted) | |
| **Spend freeze** | `spending_config.paid_frozen` | | frozen (an operator must reconcile and unfreeze) |
| **Authentication anomalies** (15 min window) | append-only `audit_events` | enrollment failures >= 5; refresh failures >= 10; **refresh-token reuse >= 1** | enrollment >= 20; refresh >= 40; **reuse >= 3** |
| Burst of 401/403/429 | in-process detector, JSON log event `auth_anomaly` | one warning per 60 s window above the threshold (default 20) | |

Notes: refresh-token **reuse** means a rotated token came back, which is how theft shows up; treat any occurrence as an
incident (`INCIDENT_RESPONSE.md`). Plain bad-bearer-token 401s are counted by the in-process detector and the access log, not
the audit table. The database role used by the check needs only `SELECT` (tested with a role that has exactly that).

## 2. Run it

The database publishes no port, so the checks are run in **two halves**, which together cover every signal in section 1:

```bash
# (a) inside the internal network, with the runtime role (database, kill switch, spend, authentication anomalies):
docker compose --env-file .env -p abu-ai-staging -f infrastructure/docker-compose.yml -f infrastructure/docker-compose.hardened.yml \
  -f infrastructure/docker-compose.staging.yml run --rm -T ops
# (b) on the host (public URL, containers, disk, backups, alert delivery):

```bash
python3 infrastructure/scripts/ops_check.py --api-url https://localhost:8443 --insecure-loopback \
  --compose-project abu-ai-staging --disk /var/lib/docker --disk /var/backups/abu-ai --backup-dir /var/backups/abu-ai \
  --state-file /var/lib/abu-ops/state.json --notify-cmd /usr/local/bin/abu-alert --repeat-minutes 60
```

(Outside Docker, e.g. in development, `ABU_OPS_DATABASE_URL` can instead be set in the environment: never as an argument.)
`--insecure-loopback` skips certificate verification **only** for `localhost`/`127.0.0.1` (staging's self-signed CA).
Run it every 1-2 minutes from a systemd timer or cron. The database URL is never printed; failures show the exception class
only.

## 3. Alert delivery (owner decision, not made)

`--notify-cmd` is any command that reads the alert text on stdin: a small script around `mail`, a chat webhook client, a push
service. It is run **without a shell**. Delivery policy built in: notify on every **state change** (including "recovered"),
repeat a **persisting CRIT** every `--repeat-minutes` (default 60), and **retry** next run if delivery failed. An unchanged OK or
WARN is silent. Choose the channel by answering: *who must be woken, at what hours, and what is the acceptable delay?*
Until then the report's exit code is the only alert.

## 4. Logs

- Staging and production emit **one JSON object per line** (`ABU_LOG_FORMAT=json`; the deployment guard requires it).
- **Access log**: method, path *shape* (UUIDs and ids collapsed to `{id}`), status, duration, request id. **Never** the query
  string, headers, cookies, bodies, client address or user identity. `X-Request-ID` is echoed (only if it is a short safe token).
- **Redaction** runs on the final text of every record and exception: bearer tokens, JWTs, `sk-`/API keys, URL credentials,
  `password`/`token`/`secret`/`api_key`/`kek`-style fields (including `refresh_token`), private keys, long digit runs (account,
  card, phone numbers) and e-mail addresses. It is a safety net; the code does not log those things in the first place.
- Caddy's access log (JSON) drops `Authorization`, `Cookie`, `Idempotency-Key`, `X-Forwarded-For`, the client address, `Set-Cookie` and **the query string** (path only; verified live).
- **Never logged by design:** tokens, passwords, API keys, encryption keys, bank-account details, raw imported statements,
  private message content, memory contents. `tests/unit/test_logging_telemetry.py` asserts that a bearer token, a query-string
  value, a request body and a cookie do not reach the log.

## 5. Retention

| Data | Retention | Where |
|---|---|---|
| Container logs | 5 files x 10 MB per container, then discarded | Docker `json-file` (bounded, so logs cannot fill the disk) |
| Audit events | kept (append-only, no secrets) | PostgreSQL `audit_events` |
| Backups | 14 daily / 8 weekly / 6 monthly | `abu_backup.py prune` |
| Memory, threads, proposals | per `README` retention table; `memory purge-expired` is an operator job | PostgreSQL |
| Ops state | one small JSON file | `--state-file` |

A log shipper or longer retention is an owner decision; if one is added it must keep the redaction guarantees above.

## 6. Systemd timer example (not installed by the repository)

```ini
# /etc/systemd/system/abu-ops-check.service
[Service]
Type=oneshot
WorkingDirectory=/opt/abu-ai
EnvironmentFile=/etc/abu-ai/ops.env          # ABU_OPS_DATABASE_URL (mode 600)
ExecStart=/usr/bin/python3 infrastructure/scripts/ops_check.py --api-url https://localhost:8443 --insecure-loopback --compose-project abu-ai-staging --disk /var/lib/docker --backup-dir /var/backups/abu-ai --state-file /var/lib/abu-ops/state.json --notify-cmd /usr/local/bin/abu-alert
# /etc/systemd/system/abu-ops-check.timer
[Timer]
OnCalendar=*-*-* *:*:00
AccuracySec=15s
[Install]
WantedBy=timers.target
```

## 7. Evidence and gaps

**Run for real in the sandbox** (hardened staging stack, synthetic data, loopback only): all four containers reported healthy;
the in-network runner returned database `ok` at revision 0012, kill switch, spend and the three authentication signals (it
counted a deliberate enrollment-code reuse); the host half returned API liveness, disk and container health. A **failure
drill** stopped the model gateway: the check went CRIT and delivered exactly **one** alert, stayed silent on the repeat
while the fault persisted, announced "recovered" once the gateway was healthy again, then went quiet.

Tested: every signal above (unit tests for liveness, disk, containers and the notification policy; real-PostgreSQL tests for
the database, kill switch, spend freeze, audit anomalies, backup signal and the read-only role); redaction and the access log.
**Not evidenced:** the check running on the owner's host, and the alert reaching a human (no channel exists).
