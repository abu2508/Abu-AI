#!/usr/bin/env bash
# Local-only contract-test backend for M8.1 (NOT part of GitHub CI). Synthetic data, throwaway database, loopback only.
#   mobile/scripts/contract-backend.sh up     # create DB, migrate, start API on 127.0.0.1:8765, write mobile/.contract.env
#   mobile/scripts/contract-backend.sh down   # stop the API and drop the throwaway database
# Needs: the Postgres container from infrastructure/docker-compose(.dev).yml listening on 127.0.0.1:5432, a populated .env
# (read here, never printed), and the Python venv. Runs the REAL backend unchanged.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATE="$ROOT/mobile/.contract.env"
PY="$ROOT/.venv/bin/python"
PORT=8765

cmd="${1:-}"
if [ "$cmd" = "down" ]; then
  if [ -f "$STATE" ]; then
    # shellcheck disable=SC1090
    . "$STATE"
    [ -n "${ABU_CONTRACT_PID:-}" ] && kill "$ABU_CONTRACT_PID" 2>/dev/null || true
    set -a; . "$ROOT/.env"; set +a
    "$PY" - <<PYEOF
import os, sqlalchemy as sa
e = sa.create_engine(f"postgresql+psycopg://{os.environ['POSTGRES_ADMIN_USER']}:{os.environ['POSTGRES_ADMIN_PASSWORD']}@127.0.0.1:5432/postgres", isolation_level="AUTOCOMMIT")
with e.connect() as c: c.execute(sa.text('DROP DATABASE IF EXISTS "${ABU_CONTRACT_DB}" WITH (FORCE)'))
PYEOF
    rm -f "$STATE"
  fi
  exit 0
fi
[ "$cmd" = "up" ] || { echo "usage: $0 up|down" >&2; exit 2; }

cd "$ROOT"
set -a; . "$ROOT/.env"; set +a
DB="abu_contract_$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
ADMIN="postgresql+psycopg://${POSTGRES_ADMIN_USER}:${POSTGRES_ADMIN_PASSWORD}@127.0.0.1:5432"
"$PY" - <<PYEOF
import sqlalchemy as sa
e = sa.create_engine("$ADMIN/postgres", isolation_level="AUTOCOMMIT")
with e.connect() as c: c.execute(sa.text('CREATE DATABASE "$DB"'))
d = sa.create_engine("$ADMIN/$DB", isolation_level="AUTOCOMMIT")
with d.connect() as c: c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
PYEOF

export ABU_ENV=test
export ABU_DATABASE_URL="$ADMIN/$DB"
export ABU_JWT_SECRET="$(head -c36 /dev/urandom | od -An -tx1 | tr -d ' \n')"
export ABU_TOKEN_HASH_KEY="$(head -c36 /dev/urandom | od -An -tx1 | tr -d ' \n')"
export ABU_MEMORY_KEK="$(head -c32 /dev/urandom | base64)"
export ABU_LITELLM_BASE_URL="http://127.0.0.1:9"   # unreachable on purpose: no model is ever called
export ABU_RATE_LIMIT_AUTH_PER_MINUTE=60            # headroom for the functional suite; the 429 test bursts past it

"$PY" -c "
from alembic import command
from alembic.config import Config
cfg = Config('alembic.ini'); cfg.set_main_option('sqlalchemy.url', '$ADMIN/$DB'.replace('%','%%'))
command.upgrade(cfg, 'head')" >/dev/null

mkuser() { "$PY" -m services.admin create-user --name "$1" | grep -Eo '[0-9a-f]{8}-[0-9a-f-]{27}' | head -1; }
mkcode() { "$PY" -m services.admin enroll --user "$1" --label "$2" | tail -1; }
UA="$(mkuser "Contract User A (synthetic)")"
UB="$(mkuser "Contract User B (synthetic)")"

nohup "$PY" -m uvicorn services.api.main:create_app --factory --host 127.0.0.1 --port "$PORT" >"$ROOT/mobile/.contract-api.log" 2>&1 &
PID=$!
for _ in $(seq 1 60); do curl -fs "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fs "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 || { echo "API did not start; see mobile/.contract-api.log" >&2; kill "$PID" || true; exit 1; }

umask 077
{
  echo "ABU_CONTRACT_URL=http://127.0.0.1:$PORT"
  echo "ABU_CONTRACT_PID=$PID"
  echo "ABU_CONTRACT_DB=$DB"
  echo "ABU_CONTRACT_USER_A=$UA"
  echo "ABU_CONTRACT_USER_B=$UB"
  echo "ABU_CONTRACT_CODE_A1=$(mkcode "$UA" phone)"
  echo "ABU_CONTRACT_CODE_A2=$(mkcode "$UA" ipad)"
  echo "ABU_CONTRACT_CODE_A3=$(mkcode "$UA" third)"
  echo "ABU_CONTRACT_CODE_A4=$(mkcode "$UA" device-x)"
  echo "ABU_CONTRACT_CODE_A5=$(mkcode "$UA" device-y)"
  echo "ABU_CONTRACT_CODE_B1=$(mkcode "$UB" phone)"
  echo "ABU_CONTRACT_CODE_B2=$(mkcode "$UB" phone-2)"
} >"$STATE"
echo "contract backend up on 127.0.0.1:$PORT (state in mobile/.contract.env, git-ignored)"
