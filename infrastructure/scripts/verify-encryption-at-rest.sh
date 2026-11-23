#!/usr/bin/env bash
# Reports whether the filesystem holding Docker's data (and therefore the PostgreSQL volume) sits on an
# encrypted block device (LUKS/dm-crypt). It can only DETECT dm-crypt on this host. If it prints
# NOT VERIFIED, volume encryption is not proven: fix that before storing real personal data.
set -uo pipefail
root=$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)
dev=$(df --output=source "$root" 2>/dev/null | tail -1)
echo "docker data root : $root"
echo "backing device   : ${dev:-unknown}"
if command -v lsblk >/dev/null 2>&1 && [ -n "${dev:-}" ]; then
  # Walk the device ancestry looking for a dm-crypt/LUKS layer.
  if lsblk -s -no TYPE "$dev" 2>/dev/null | grep -qx crypt; then
    echo "RESULT: ENCRYPTED (dm-crypt/LUKS layer found under the data volume)"
    exit 0
  fi
fi
echo "RESULT: NOT VERIFIED - no dm-crypt/LUKS layer detected for the Docker data volume."
echo "        On a VPS: enable full-disk/volume encryption at the provider or create a LUKS volume, then re-run."
exit 2
