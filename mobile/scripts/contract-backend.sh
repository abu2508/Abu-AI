#!/usr/bin/env bash
# Local-only contract-test backend (NOT part of GitHub CI). Synthetic data, throwaway database, loopback only.
#   mobile/scripts/contract-backend.sh run [jest args]  # RECOMMENDED: up -> test -> down, down ALWAYS runs (also on failure/Ctrl+C)
#   mobile/scripts/contract-backend.sh up               # create DB, migrate, start API on 127.0.0.1:8765, write mobile/.contract.env
#   mobile/scripts/contract-backend.sh down             # stop the API and drop the throwaway database
# Isolation guarantees (see docs/mobile/CONTRACT_TESTS.md):
#   * every `up` creates a NEW database named abu_contract_<random>, a NEW API process (frozen Coach clock, see contract-api.py), NEW JWT/hash/KEK secrets and NEW
#     single-use enrollment codes; a stale previous instance is torn down first (never reused);
#   * the script never reads ABU_DATABASE_URL from .env: it builds its own URL from the Postgres admin credentials, only
#     for 127.0.0.1, and only ever CREATEs or DROPs a database whose name matches ^abu_contract_[0-9a-f]{12}$;
#   * a failure anywhere in `up` rolls back (kills the API, drops the database, removes the state file).
# Needs: the Postgres container from infrastructure/docker-compose(.dev).yml listening on 127.0.0.1:5432, a populated .env
# (read here, never printed), the Python venv at .venv, node, curl. Runs the REAL backend unchanged.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATE="$ROOT/mobile/.contract.env"
PY="$ROOT/.venv/bin/python"
PORT=8765

cmd="${1:-}"
DB_RE='^abu_contract_[0-9a-f]{12}$'
LOOPBACK=127.0.0.1

load_env() { [ -f "$ROOT/.env" ] || { echo "missing $ROOT/.env (needs the local Postgres admin credentials)" >&2; exit 1; }; set -a; . "$ROOT/.env"; set +a; }

# Drop one throwaway database. Refuses any name that is not ours, and only ever talks to loopback.
drop_db() {
  local db="$1"
  [[ "$db" =~ $DB_RE ]] || { echo "refusing to drop '$db': not a contract-test database" >&2; return 1; }
  load_env
  "$PY" - <<PYEOF || true
import os, sqlalchemy as sa
e = sa.create_engine(f"postgresql+psycopg://{os.environ['POSTGRES_ADMIN_USER']}:{os.environ['POSTGRES_ADMIN_PASSWORD']}@$LOOPBACK:5432/postgres", isolation_level="AUTOCOMMIT")
with e.connect() as c: c.execute(sa.text('DROP DATABASE IF EXISTS "$db" WITH (FORCE)'))
PYEOF
}

# Tear down whatever the state file describes. Safe to call repeatedly, when nothing is running, and after a failed `up`.
teardown() {
  [ -f "$STATE" ] || return 0
  local pid db
  pid="$(grep '^ABU_CONTRACT_PID=' "$STATE" | cut -d= -f2 || true)"
  db="$(grep '^ABU_CONTRACT_DB=' "$STATE" | cut -d= -f2 || true)"
  # Only ever signal a process that really is this script's API launcher (a hand-edited state file must not kill anything else).
  if [[ "$pid" =~ ^[0-9]+$ ]] && tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null | grep -q 'mobile/scripts/contract-api.py'; then
    kill "$pid" 2>/dev/null || true
  fi
  if [ -n "$db" ] && ! drop_db "$db"; then
    echo "state file kept (database '$db' was not dropped); fix it and run down again" >&2; return 1
  fi
  rm -f "$STATE"
}

if [ "$cmd" = "down" ]; then teardown; exit 0; fi

if [ "$cmd" = "killswitch" ]; then   # killswitch activate|deactivate  (admin CLI against the throwaway database)
  [ -f "$STATE" ] || { echo "no running contract backend" >&2; exit 1; }
  ABU_CONTRACT_DB="$(grep '^ABU_CONTRACT_DB=' "$STATE" | cut -d= -f2)"
  [[ "$ABU_CONTRACT_DB" =~ $DB_RE ]] || { echo "refusing: state names a non-contract database" >&2; exit 1; }
  cd "$ROOT"; load_env
  export ABU_ENV=test ABU_DATABASE_URL="postgresql+psycopg://${POSTGRES_ADMIN_USER}:${POSTGRES_ADMIN_PASSWORD}@$LOOPBACK:5432/${ABU_CONTRACT_DB}"
  export ABU_JWT_SECRET="$(head -c36 /dev/urandom | od -An -tx1 | tr -d ' \n')" ABU_TOKEN_HASH_KEY="$(head -c36 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  exec "$PY" -m services.admin kill-switch "${2:?activate|deactivate}" --reason "contract test"
fi

if [ "$cmd" = "run" ]; then          # up -> test -> down; down runs on success, failure and interrupt
  shift
  trap 'teardown' EXIT
  trap 'exit 130' INT TERM
  "$0" up
  (cd "$ROOT/mobile" && node --env-file=.contract.env node_modules/jest/bin/jest.js --config jest.contract.config.js "$@")
  exit $?
fi

[ "$cmd" = "up" ] || { echo "usage: $0 run [jest args] | up | down | killswitch activate|deactivate" >&2; exit 2; }

# ---- up ----
cd "$ROOT"
load_env
teardown                                             # never reuse a previous instance (its enrollment codes are spent)
if (exec 3<>"/dev/tcp/$LOOPBACK/$PORT") 2>/dev/null; then
  echo "port $PORT is already in use by something that is not this script's instance; stop it first (refusing to reuse it)" >&2; exit 1
fi
DB="abu_contract_$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
[[ "$DB" =~ $DB_RE ]] || { echo "internal error: bad database name" >&2; exit 1; }
ADMIN="postgresql+psycopg://${POSTGRES_ADMIN_USER}:${POSTGRES_ADMIN_PASSWORD}@$LOOPBACK:5432"

# From here on, any failure rolls back everything this run created (API process, database, state file).
PID=""
rollback() { rc=$?; if [ "$rc" -ne 0 ]; then echo "contract backend setup failed (exit $rc); rolling back" >&2; [ -n "$PID" ] && kill "$PID" 2>/dev/null || true; drop_db "$DB" || true; rm -f "$STATE"; fi; }
trap rollback EXIT

"$PY" - <<PYEOF
import sqlalchemy as sa
e = sa.create_engine("$ADMIN/postgres", isolation_level="AUTOCOMMIT")
with e.connect() as c: c.execute(sa.text('CREATE DATABASE "$DB"'))
d = sa.create_engine("$ADMIN/$DB", isolation_level="AUTOCOMMIT")
with d.connect() as c: c.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
PYEOF

export ABU_ENV=test
# Deterministic clock for the live process (see mobile/scripts/contract-api.py): today's UTC date at 06:30 UTC (= 12:00 in the
# default Asia/Kolkata zone, the wall-clock time of the B3 golden). The date comes from ONE `date` call so the API clock and the
# seeded data can never disagree, even if a run straddles UTC midnight.
CONTRACT_DATE="$(date -u +%F)"
export ABU_CONTRACT_NOW="${CONTRACT_DATE}T06:30:00+00:00"
export ABU_DATABASE_URL="$ADMIN/$DB"
export ABU_JWT_SECRET="$(head -c36 /dev/urandom | od -An -tx1 | tr -d ' \n')"
export ABU_TOKEN_HASH_KEY="$(head -c36 /dev/urandom | od -An -tx1 | tr -d ' \n')"
export ABU_MEMORY_KEK="$(head -c32 /dev/urandom | base64)"
export ABU_LITELLM_BASE_URL="http://127.0.0.1:9"   # unreachable on purpose: no model is ever called
export ABU_RATE_LIMIT_AUTH_PER_MINUTE=60            # headroom for the functional suite; the 429 test bursts past it
export ABU_RATE_LIMIT_IP_PER_MINUTE=2000           # per-IP headroom: every local client shares 127.0.0.1; the per-USER limit (300/min) stays the real default

"$PY" -c "
from alembic import command
from alembic.config import Config
cfg = Config('alembic.ini'); cfg.set_main_option('sqlalchemy.url', '$ADMIN/$DB'.replace('%','%%'))
command.upgrade(cfg, 'head')" >/dev/null

# Registry + mock agents: the orchestrator needs them (the default app uses the in-process mock model; no LiteLLM, no external call).
"$PY" -m database.seed >/dev/null
"$PY" -m services.admin orchestrator bootstrap-mock >/dev/null

mkuser() { "$PY" -m services.admin create-user --name "$1" | grep -Eo '[0-9a-f]{8}-[0-9a-f-]{27}' | head -1; }
mkcode() { "$PY" -m services.admin enroll --user "$1" --label "$2" | tail -1; }
UA="$(mkuser "Contract User A (synthetic)")"
UB="$(mkuser "Contract User B (synthetic)")"
UH="$(mkuser "Home Data User (synthetic)")"
UE="$(mkuser "Empty User (synthetic)")"

nohup "$PY" "$ROOT/mobile/scripts/contract-api.py" --host "$LOOPBACK" --port "$PORT" >"$ROOT/mobile/.contract-api.log" 2>&1 &
PID=$!
for _ in $(seq 1 60); do curl -fs "http://$LOOPBACK:$PORT/health" >/dev/null 2>&1 && break; sleep 0.5; done
curl -fs "http://$LOOPBACK:$PORT/health" >/dev/null 2>&1 || { echo "API did not start; see mobile/.contract-api.log" >&2; exit 1; }

umask 077
{
  echo "ABU_CONTRACT_URL=http://$LOOPBACK:$PORT"
  echo "ABU_CONTRACT_PID=$PID"
  echo "ABU_CONTRACT_DB=$DB"
  echo "ABU_CONTRACT_NOW=$ABU_CONTRACT_NOW"
  echo "ABU_CONTRACT_USER_A=$UA"
  echo "ABU_CONTRACT_USER_B=$UB"
  echo "ABU_CONTRACT_CODE_A1=$(mkcode "$UA" phone)"
  echo "ABU_CONTRACT_CODE_A2=$(mkcode "$UA" ipad)"
  echo "ABU_CONTRACT_CODE_A3=$(mkcode "$UA" third)"
  echo "ABU_CONTRACT_CODE_A4=$(mkcode "$UA" device-x)"
  echo "ABU_CONTRACT_CODE_A5=$(mkcode "$UA" device-y)"
  echo "ABU_CONTRACT_CODE_B1=$(mkcode "$UB" phone)"
  echo "ABU_CONTRACT_CODE_B2=$(mkcode "$UB" phone-2)"
  echo "ABU_CONTRACT_USER_H=$UH"
  echo "ABU_CONTRACT_USER_E=$UE"
  echo "ABU_CONTRACT_CODE_HSEED=$(mkcode "$UH" seeder)"
  for n in 1 2 3 4; do echo "ABU_CONTRACT_CODE_H$n=$(mkcode "$UH" "home-$n")"; done
  for n in 1 2 3; do echo "ABU_CONTRACT_CODE_E$n=$(mkcode "$UE" "empty-$n")"; done
} >"$STATE"
# synthetic data for the Home data user (through the backend's own HTTP API)
HSEED="$(grep '^ABU_CONTRACT_CODE_HSEED=' "$STATE" | cut -d= -f2)"
ABU_CONTRACT_DATE="$CONTRACT_DATE" node "$ROOT/mobile/scripts/seed-contract-data.mjs" "http://$LOOPBACK:$PORT" "$HSEED"
trap - EXIT     # success: keep the instance running for `up` callers
echo "contract backend up on $LOOPBACK:$PORT (state in mobile/.contract.env, git-ignored)"
