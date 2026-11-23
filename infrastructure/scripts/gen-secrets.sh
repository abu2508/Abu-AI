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
echo "Created .env (mode 600). Do not commit it."
