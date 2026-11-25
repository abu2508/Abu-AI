# Database roles and migrations

Principle: the process that serves requests must not be able to change the schema. Two roles:

| Role | Name (example) | Can | Cannot | Used by |
|---|---|---|---|---|
| **Owner** | `abu_owner` | own the database, run migrations, create the checkpoint tables, grant privileges | log in from the API (its URL is absent from the runtime environment; the deployment guard refuses to start if it is present) | the one-shot `migrate` step only |
| **Runtime** | `abu_app` | `SELECT/INSERT/UPDATE/DELETE` on application tables and the `lg_checkpoints` tables, use sequences, read `alembic_version` | `CREATE`/`ALTER`/`DROP`/`TRUNCATE` anything, create schemas or extensions, change `alembic_version`, be a superuser, own any object | the API, the operator CLI (`admin`), `ops_check.py` |

Neither role is a superuser. The `vector` extension is created at initialisation by the PostgreSQL admin (superuser) role,
which the application never holds.

## How it is set up

1. **Initialisation** (`infrastructure/postgres/01-init.sh`, first start of an empty data directory). With `APP_DB_OWNER_USER`
   set, it creates the owner (owns the database) and the runtime role (`CONNECT` only). With it unset, the old single-role
   behaviour is unchanged (development, the current CI).
2. **Bootstrap** (`python -m services.admin db bootstrap`, run by the Compose `migrate` service as the owner):
   migrations to head, LangGraph checkpoint tables, then idempotent grants to the runtime role. It needs only
   `ABU_MIGRATION_DATABASE_URL`; it is safe to repeat and **never drops, truncates or rewrites data**.
3. **Runtime start** (`services.api.entrypoint:create_checked_app`) refuses to run unless its role is a least-privilege
   runtime role (no superuser/createdb/createrole/bypassrls/replication, no `CREATE` on `public`, `lg_checkpoints` or the
   database, owns no application object).
4. `ABU_CHECKPOINT_AUTO_SETUP=false` stops the application running LangGraph's `CREATE TABLE IF NOT EXISTS` at startup
   (that needs `CREATE`; PostgreSQL checks the privilege before the existence test).

## Writing a migration (developers)

Unchanged: Alembic revisions in `database/migrations/versions`, additive where possible. New tables are covered for the
runtime role automatically (default privileges are set for the owner's future objects), so no migration needs a `GRANT`.
After a deploy the `migrate` step re-applies grants to everything that exists.

## Existing databases

`infrastructure/postgres/split-existing-db.sql` converts a single-role database to the two-role layout by changing
**ownership only** (no row is read, moved or deleted). **It has not been applied to any real database.** It was rehearsed
on a throwaway database (all rows preserved, runtime DDL denied afterwards) in
`tests/integration/test_role_separation.py::test_an_existing_single_role_database_can_be_split_without_losing_a_row`.
To use it: take and verify a backup first (`BACKUP_RESTORE.md`), stop the API, run it as the superuser, run the bootstrap as
the new owner, start the API. Roll back by restoring the backup.

## Evidence (all on real PostgreSQL 16, isolated throwaway databases and roles)

- 22 statements that change the schema or the migration history are refused for the runtime role (`CREATE TABLE`, `ALTER`,
  `DROP`, `TRUNCATE`, `CREATE INDEX`, `DROP FUNCTION`, `UPDATE alembic_version`, `CREATE ROLE`, `ALTER ROLE ... SUPERUSER`,
  `ALTER TABLE ... OWNER`, ...); a non-owner `GRANT` changes nothing.
- Finance (including the S2.1A transfer-link trigger), memory, the orchestrator and encrypted checkpoints all work **as the
  runtime role**.
- Tables created later by the owner are usable by the runtime role without a re-grant; the bootstrap is idempotent.
- The privilege guard accepts the runtime role and rejects the owner and the superuser.

**Not evidenced:** the role split on the real VPS database, and a migration run through the Compose `migrate` service
against the hardened stack (no container runtime to run it in the sandbox; the CI builds the image and validates the Compose
layers but does not start them).
