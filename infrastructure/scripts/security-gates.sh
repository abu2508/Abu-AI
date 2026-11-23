#!/usr/bin/env bash
# Reports the deployment security gates that must be satisfied before ANY real personal, financial or
# messaging data is used. It prints BLOCKED for every gate it cannot verify from here and exits non-zero
# until every gate is explicitly acknowledged by the operator (see README "Deployment security gates").
#
#   ABU_GATE_DISK_ENCRYPTION=verified      disk/volume encryption for the Postgres volume and backups was checked
#   ABU_GATE_KEYRING_BACKUP=verified       the keyring is backed up independently of DB backups and a restore was rehearsed
#   ABU_GATE_CHECKPOINT_METADATA=accepted  plaintext checkpoint metadata risk accepted or removed
#   ABU_GATE_PRODUCTION_EMBEDDER=verified  a vetted, approved production embedding provider is configured
set -u
blocked=0
gate() {  # name, env var, expected, description
  local val="${!2:-}"
  if [ "$val" = "$3" ]; then echo "OK       $1"; else echo "BLOCKED  $1 - $4 (set $2=$3 once true)"; blocked=1; fi
}
gate "disk-volume-encryption"   ABU_GATE_DISK_ENCRYPTION      verified "disk/volume encryption not verified"
gate "keyring-backup-recovery"  ABU_GATE_KEYRING_BACKUP        verified "independent keyring backup and recovery not verified"
gate "checkpoint-metadata"      ABU_GATE_CHECKPOINT_METADATA   accepted "LangGraph checkpoint metadata is stored in plaintext"
gate "production-embedder"      ABU_GATE_PRODUCTION_EMBEDDER   verified "only the deterministic test embedder exists"
if [ "$blocked" -ne 0 ]; then
  echo; echo "Do NOT use real personal, financial or messaging data until every gate is OK."; exit 1
fi
