#!/usr/bin/env bash
# Runs once, on first initialisation of the data directory.
# Creates separate logical databases and least-privilege users for the app and LiteLLM,
# and enables pgvector in the application database only.
set -euo pipefail

# Role model (docs/operations/DATABASE_ROLES.md):
#   APP_DB_OWNER_USER unset  -> legacy single role: APP_DB_USER owns the database (development and the existing CI).
#   APP_DB_OWNER_USER set    -> split roles: APP_DB_OWNER_USER owns the database and runs DDL (migrate step only);
#                               APP_DB_USER is the runtime role (CONNECT only here; table grants are applied by
#                               `python -m services.admin db bootstrap` once the tables exist).
if [ -n "${APP_DB_OWNER_USER:-}" ]; then
  : "${APP_DB_OWNER_PASSWORD:?APP_DB_OWNER_PASSWORD is required when APP_DB_OWNER_USER is set}"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v app_db="$APP_DB_NAME" -v app_user="$APP_DB_USER" -v app_pw="$APP_DB_PASSWORD" \
    -v own_user="$APP_DB_OWNER_USER" -v own_pw="$APP_DB_OWNER_PASSWORD" \
    -v ll_db="$LITELLM_DB_NAME" -v ll_user="$LITELLM_DB_USER" -v ll_pw="$LITELLM_DB_PASSWORD" <<'SQL'
CREATE ROLE :"own_user" LOGIN PASSWORD :'own_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE :"ll_user"  LOGIN PASSWORD :'ll_pw';
CREATE DATABASE :"app_db" OWNER :"own_user";
CREATE DATABASE :"ll_db"  OWNER :"ll_user";
REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"ll_db"  FROM PUBLIC;
GRANT CONNECT ON DATABASE :"app_db" TO :"app_user";
SQL
else
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v app_db="$APP_DB_NAME" -v app_user="$APP_DB_USER" -v app_pw="$APP_DB_PASSWORD" \
    -v ll_db="$LITELLM_DB_NAME" -v ll_user="$LITELLM_DB_USER" -v ll_pw="$LITELLM_DB_PASSWORD" <<'SQL'
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_pw';
CREATE ROLE :"ll_user"  LOGIN PASSWORD :'ll_pw';
CREATE DATABASE :"app_db" OWNER :"app_user";
CREATE DATABASE :"ll_db"  OWNER :"ll_user";
-- Nobody but the owner may connect to either database.
REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"ll_db"  FROM PUBLIC;
SQL
fi

# Extension creation needs superuser; the app role cannot do this itself.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$APP_DB_NAME" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
