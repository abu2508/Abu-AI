# Keyring recovery runbook (gate G2)

The memory keyring (`secrets/memory-keyring.json`, mode 600, a Docker secret) holds the master keys that wrap every
per-record data key. It is **not** in PostgreSQL and **not** in database backups, by design. If it is lost and has no
independent backup, every encrypted memory record, finance description and label, and encrypted checkpoint value is
**unrecoverable**.

## 1. Independent backup (do it at setup and after every key change)

```bash
python3 infrastructure/scripts/abu_backup.py keyring-backup \
  --keyring secrets/memory-keyring.json --key-file ~/abu-keys/backup-keyring.key \
  --db-key-file ~/abu-keys/backup-db.key --dest /var/backups/abu-ai
```

- Encrypted under **K2**, which must differ from the database backup key **K1** (the tool refuses otherwise), so no single
  secret recovers both the database and its keys.
- Copy the file off-host, and keep **K2 offline** in a place that is not the VPS and not next to K1.
- The output lists key **ids** only (for example `k1, k2`); no key material is printed or logged.
- After `memory add-key` / `memory rotate-keys`, back up again: an old keyring backup cannot open records wrapped with a
  newer key.

## 2. Recovery

```bash
python3 infrastructure/scripts/abu_backup.py keyring-restore \
  --key-file ~/abu-keys/backup-keyring.key --out /path/to/recovered-keyring.json  /var/backups/abu-ai/abu-keyring-<UTC>.enc
```

- Writes a **new** mode-600 file, never overwrites, validates the structure, and deletes a half-written file on failure.
- Install it as `secrets/memory-keyring.json` (owner = the container uid, `gen-secrets.sh` shows how), then restart.
- **Prove it before relying on it:**
  `uv run python infrastructure/scripts/abu_backup.py recovery-check --database-url ... --keyring /path/to/recovered-keyring.json`.
  Exit 0 means real encrypted rows decrypt; exit 3 means some did not (the keyring is missing a key id); exit 4 means there
  was nothing encrypted to check, so recovery is **not demonstrated**.

## 3. Drill schedule

Run section 2 against the latest backups into a scratch database **quarterly** and after any change to key custody. Record the
date and the `recovery-check` counts (counts only) in the incident/operations log.

## 4. Status of gate G2

| Evidence | Status |
|---|---|
| Procedure exists, is documented and tested | done (`test_backup_restore_rehearsal.py`, `test_backup_tool.py`) |
| Independent backup under a different key | enforced by the tool |
| Rehearsal: lose keyring, restore database, recover keyring from its backup, read the data | **PASS in the sandbox, synthetic data** |
| Same drill on the real host with the real off-host location and real offline keys | **BLOCKED** (owner action) |
| Off-host destination chosen | **BLOCKED** (owner decision) |

G2 is **not** passed until the last two rows are done and the owner sets `ABU_GATE_KEYRING_BACKUP=verified`.
