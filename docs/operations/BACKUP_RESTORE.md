# Backup and restore runbook

Tool: `infrastructure/scripts/abu_backup.py` (built on the tested streaming AES-256-GCM tool `encrypted_backup.py`).
The database backup and the keyring backup are **two separate artifacts under two separate keys**. Read
`docs/operations/KEYRING_RECOVERY.md` as well.

## 1. What is protected, and the three things you must hold

| Artifact | Protected by | Where it must live |
|---|---|---|
| `abu-ai-<UTC>.dump.enc` (+ `.sha256`) | key **K1** (`backup-db.key`) | local `backups/` **and** an off-host location |
| `abu-keyring-<UTC>.enc` (+ `.sha256`) | key **K2** (`backup-keyring.key`), different from K1 | off-host and on paper/offline |
| K1 and K2 key files | themselves; mode 600 | **K1 and K2 offline, apart from every backup and apart from each other** |

Losing K1 loses the database backups. Losing K2 (and the live keyring) loses the encrypted content permanently.
**A database backup alone cannot be read**: the rehearsal below proves it.

## 2. Targets (proposed; the owner approves or changes them)

| | Proposed target | Basis |
|---|---|---|
| **RPO** (data you can lose) | **24 hours**; 6 hours if desired | one backup per day at 03:30 UTC; tighten the timer to 6-hourly for 6 h |
| **RTO** (time to restore service) | **4 hours** for a single-user deployment | detect (<= 15 min with monitoring), prepare host, restore, verify, resume |
| Retention | newest 14 daily, 8 weekly, 6 monthly | `abu_backup.py prune` defaults; dry run unless `--apply` |

**Evidence that exists:** the isolated rehearsal (below) measured, on a **tiny synthetic** database (49 tables, 184 KB
backup): backup 0.18 s, restore 0.29 s, keyring recovery under 0.01 s. These timings say the tooling has no hidden delay;
they are **not** an estimate for a real database. **Not yet evidenced:** a restore on the actual VPS, a production-sized
dataset, an off-host copy, and a wall-clock RTO from "alert" to "service back". Those are **BLOCKED on the VPS**
(`docs/operations/VPS_ASSESSMENT.md`).

> **Tool versions.** Dump and restore with the **same PostgreSQL major version as the server**. A dump written by a newer
> client can contain settings an older server rejects (found in CI: `unrecognized configuration parameter
> "transaction_timeout"` when a PostgreSQL 17+ `pg_dump` wrote the dump and a 16 server restored it). The procedures below run
> `pg_dump` and `pg_restore` **inside the `db` container**, which always matches; if you run them from the host, install the
> matching client (`postgresql-client-16`). The rehearsal test refuses a mismatched client (and fails in CI).

## 3. One-time setup (operator)

```bash
umask 077
mkdir -p ~/abu-keys
python3 infrastructure/scripts/encrypted_backup.py genkey --key-file ~/abu-keys/backup-db.key        # K1
python3 infrastructure/scripts/encrypted_backup.py genkey --key-file ~/abu-keys/backup-keyring.key   # K2
# Copy K1 and K2 OFFLINE now (password manager attachment, paper print of the base64 text, a USB stick in a safe),
# in two different places. Never put them inside backups/ or next to the database volume.
```

## 4. Take a backup (also the scheduled job)

```bash
export ABU_BACKUP_KEY_FILE=~/abu-keys/backup-db.key ABU_BACKUP_DIR=/var/backups/abu-ai
# optional off-host copy; {file} and {manifest} are substituted; the command is yours (rclone, scp, s3 cli, ...). No shell.
export ABU_BACKUP_OFFHOST_COMMAND='rclone copy {file} remote:abu-ai/ --immutable'
python3 infrastructure/scripts/abu_backup.py backup
```

The default dump command targets the base Compose project. For staging or any project name, pass the exact command after
`--dump-cmd` (everything following the flag is the command), for example:

```bash
python3 infrastructure/scripts/abu_backup.py backup --dump-cmd docker compose --env-file .env -p abu-ai-staging \
  -f infrastructure/docker-compose.yml -f infrastructure/docker-compose.hardened.yml -f infrastructure/docker-compose.staging.yml \
  exec -T db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$APP_DB_NAME"'
```

(This exact pipeline was run against the live sandbox staging database: 364 archive entries, verified.)
A backup is written as `.partial` and renamed only when complete, is **never overwritten**, then verified (checksum,
authenticated decryption, archive table of contents). The result goes into `status.json`; a failed attempt leaves no usable
file (a post-write failure is kept as `*.unverified` for inspection).

Keyring backup after **every** keyring change (`memory add-key`, `rotate-keys`) and once at setup:

```bash
python3 infrastructure/scripts/abu_backup.py keyring-backup \
  --keyring secrets/memory-keyring.json --key-file ~/abu-keys/backup-keyring.key \
  --db-key-file ~/abu-keys/backup-db.key --dest /var/backups/abu-ai
```

Schedule (systemd timer example; **not installed by the repository**):

```ini
# /etc/systemd/system/abu-backup.service
[Service]
Type=oneshot
WorkingDirectory=/opt/abu-ai
EnvironmentFile=/etc/abu-ai/backup.env      # ABU_BACKUP_KEY_FILE, ABU_BACKUP_DIR, ABU_BACKUP_OFFHOST_COMMAND
ExecStart=/usr/bin/python3 infrastructure/scripts/abu_backup.py backup
ExecStartPost=/usr/bin/python3 infrastructure/scripts/abu_backup.py prune --apply
# /etc/systemd/system/abu-backup.timer
[Timer]
OnCalendar=*-*-* 03:30:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
```

## 5. Verify a backup (do this regularly, not only after taking it)

```bash
python3 infrastructure/scripts/abu_backup.py verify --key-file ~/abu-keys/backup-db.key /var/backups/abu-ai/abu-ai-<UTC>.dump.enc
python3 infrastructure/scripts/abu_backup.py status --dest /var/backups/abu-ai --max-age-hours 26    # 0 ok, 1 stale, 2 failed, 3 none
```

`ops_check.py` reads the same status file (see `MONITORING.md`), so a missed or failed backup alerts.

## 6. Restore (into a NEW database; never over the live one)

1. **Stop** the API and admin CLI (`docker compose ... stop api`); keep PostgreSQL running.
2. Create an empty database and (superuser) let the restore create the extension:
   `docker compose ... exec db createdb -U "$POSTGRES_USER" abu_restore`.
3. Decrypt and restore (authenticated decryption fails on any tampering, truncation or wrong key):

```bash
python3 infrastructure/scripts/encrypted_backup.py decrypt --key-file ~/abu-keys/backup-db.key \
  < /var/backups/abu-ai/abu-ai-<UTC>.dump.enc \
  | docker compose ... exec -T db pg_restore --exit-on-error --no-owner -U "$POSTGRES_USER" -d abu_restore
```

4. Recover the keyring if the live one is lost (`KEYRING_RECOVERY.md`), then **prove the data is readable**:

```bash
uv run python infrastructure/scripts/abu_backup.py recovery-check \
  --database-url "postgresql://.../abu_restore" --keyring /path/to/recovered-keyring.json
# prints only counts and key ids; exit 0 only if every sampled row decrypts and at least one was found
```

5. Point the deployment at the restored database (swap names, or restore into the configured name on an empty volume),
   run `python -m services.admin db bootstrap` (re-applies the runtime role's grants; idempotent), start the API, and run
   `ops_check.py`.
6. Keep the pre-restore database until you have verified the restored one. Nothing here deletes data.

## 7. The rehearsal that was run

`tests/integration/test_backup_restore_rehearsal.py` (real PostgreSQL 16, **synthetic data only**; created and dropped its own
databases):

| Step | Result |
|---|---|
| Encrypted backup, verified | ok; the ciphertext contains neither the plaintext markers nor the keyring |
| Keyring backed up under K2 (K2 == K1 refused) | ok |
| **Keyring file deleted** (disaster) | simulated |
| Restore into a brand-new database | 49 tables, **identical row counts**, schema revision `0012`, S2.1A trigger present |
| Read the restored data with a *wrong* keyring | **0 of 2 memory and 0 of 3 finance rows decrypt** (a database backup alone is useless) |
| Recover the keyring with K1 | **refused** (K1 cannot open the keyring backup) |
| Recover the keyring with K2, `recovery-check` | 2/2 memory and 3/3 finance rows decrypt; key ids reported; no key printed |
| Run the application on the restored database | returns exactly the original plaintext |
| Tamper, truncate, wrong key, forged manifest | each detected (`tests/unit/test_backup_tool.py`) |

This demonstrates the *procedure*. It is evidence for gate G2 **only as a rehearsal in the sandbox**; G2 stays BLOCKED until
the owner runs the same recovery on the real host with the real off-host location and real offline keys
(`PRODUCTION_READINESS.md`).

## 8. What this does not do

- It does not encrypt the live volume (G1) or protect a backup whose keys are stored with it.
- It does not copy anything off-host by itself: the destination is an owner decision; the hook only runs the command you give it.
- It does not restore the LiteLLM database (it holds no application data in this deployment: mock model only).
