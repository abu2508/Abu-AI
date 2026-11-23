#!/usr/bin/env bash
# Runs once, on first initialisation of the data directory.
# Creates separate logical databases and least-privilege users for the app and LiteLLM,
# and enables pgvector in the application database only.
set -euo pipefail

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

# Extension creation needs superuser; the app role cannot do this itself.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$APP_DB_NAME" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
