-- OPERATOR-RUN, OPTIONAL: split an EXISTING single-role database into an owner role and a runtime role.
--
-- Nothing runs this automatically, and it has never been applied to any real database: it was only rehearsed on an
-- isolated, throwaway database (tests/integration/test_role_separation.py). It changes OWNERSHIP only: no row is read,
-- moved, rewritten or deleted, and no table is dropped.
--
-- BEFORE YOU RUN IT: take and verify an encrypted backup (docs/operations/BACKUP_RESTORE.md) and stop the API, the admin
-- CLI and LiteLLM's connection to this database is unaffected (separate database). Run as the PostgreSQL superuser:
--
--   psql -v ON_ERROR_STOP=1 -U <superuser> -d postgres \
--        -v db=abu_ai -v runtime=abu_app -v owner=abu_owner -v owner_pw="$NEW_OWNER_PASSWORD" \
--        -f infrastructure/postgres/split-existing-db.sql
--   # then, as the NEW owner, apply the grants and confirm the revision (no re-migration needed):
--   ABU_MIGRATION_DATABASE_URL=postgresql+psycopg://abu_owner:...@db:5432/abu_ai \
--     python -m services.admin db bootstrap --runtime-role abu_app
--
-- Roll back by re-running with owner and runtime swapped, or restore the backup.
\set ON_ERROR_STOP on

CREATE ROLE :"owner" LOGIN PASSWORD :'owner_pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER DATABASE :"db" OWNER TO :"owner";
\connect :"db"
-- every table, sequence, index, function, schema and type the runtime role owned now belongs to the owner
REASSIGN OWNED BY :"runtime" TO :"owner";
-- the runtime role keeps CONNECT; the bootstrap step grants the table privileges
GRANT CONNECT ON DATABASE :"db" TO :"runtime";
