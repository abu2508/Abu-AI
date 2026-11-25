#!/usr/bin/env bash
# Create .env from .env.example with freshly generated random secrets.
# Refuses to overwrite an existing .env.
set -euo pipefail
cd "$(dirname "$0")/../.."
if [[ -e .env ]]; then
  echo ".env already exists; refusing to overwrite." >&2
  exit 1
fi
rand() { openssl rand -hex 24; }
umask 077
sed \
  -e "s|^POSTGRES_ADMIN_PASSWORD=.*|POSTGRES_ADMIN_PASSWORD=$(rand)|" \
  -e "s|^APP_DB_PASSWORD=.*|APP_DB_PASSWORD=$(rand)|" \
  -e "s|^LITELLM_DB_PASSWORD=.*|LITELLM_DB_PASSWORD=$(rand)|" \
  -e "s|^JWT_SECRET=.*|JWT_SECRET=$(openssl rand -hex 32)|" \
  -e "s|^TOKEN_HASH_KEY=.*|TOKEN_HASH_KEY=$(openssl rand -hex 32)|" \
  -e "s|^LITELLM_MASTER_KEY=.*|LITELLM_MASTER_KEY=sk-$(rand)|" \
  .env.example > .env
# Master key for memory encryption: a keyring FILE (mounted as a Docker secret). Keep it OFF the database
# backups. Losing it makes encrypted memory unrecoverable; back it up separately from the database.
mkdir -p secrets
if [[ ! -e secrets/memory-keyring.json ]]; then
  printf '{"active":"k1","keys":{"k1":"%s"}}' "$(openssl rand -base64 32)" > secrets/memory-keyring.json
  chmod 600 secrets/memory-keyring.json
  # The containers run as a NON-root user that must be able to read this private (mode 600) file:
  #  * as root  : give the file to the image's default user (10001);
  #  * as a user: the containers run as YOU (ABU_UID), the file's owner.
  if [[ "$(id -u)" == "0" ]]; then
    chown 10001:10001 secrets/memory-keyring.json && abu_uid=10001
  else
    abu_uid="$(id -u)"
  fi
  echo "ABU_UID=${abu_uid:-10001}" >> .env
  echo "Created secrets/memory-keyring.json (mode 600). Back it up SEPARATELY from database backups."
fi
# Staging / production: a separate database OWNER role (migrations only); APP_DB_USER becomes the runtime role.
#   ABU_SPLIT_ROLES=1 ./infrastructure/scripts/gen-secrets.sh
if [[ "${ABU_SPLIT_ROLES:-0}" == "1" ]]; then
  {
    echo "APP_DB_OWNER_USER=abu_owner"
    echo "APP_DB_OWNER_PASSWORD=$(rand)"
  } >> .env
  echo "Added APP_DB_OWNER_USER/APP_DB_OWNER_PASSWORD (split database roles)."
fi
echo "Created .env (mode 600). Do not commit it."
