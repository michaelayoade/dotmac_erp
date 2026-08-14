-- PG Observability Setup
-- Enables pg_stat_statements and grants read-only monitoring access to claude_readonly.
--
-- Prerequisites:
--   docker-compose.yml must include: command: ["postgres", "-c", "shared_preload_libraries=pg_stat_statements"]
--   After adding, restart the db container: docker compose restart db
--
-- Usage:
--   make pg-observe-setup
-- Or:
--   docker exec -i dotmac_erp_db psql -U postgres -d dotmac_erp \
--     -v observer_password="$PG_OBSERVER_PASSWORD" < scripts/setup_pg_observability.sql
--
-- PG_OBSERVER_PASSWORD has no default on purpose: see the note at the role
-- creation below.

-- 1. Enable pg_stat_statements (requires shared_preload_libraries in docker-compose.yml)
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- 2. Create the read-only role if it doesn't exist
--
-- The password is supplied by the caller and never written here. It was the
-- literal 'claude_readonly' -- a LOGIN role whose password equalled its own
-- name, granted pg_monitor, which can read pg_stat_activity and therefore
-- query TEXT. `detect-secrets` could not have caught it: scripts/ is on that
-- hook's exclude list, which is also where all four previously-committed
-- credentials lived.
--
-- Done with \gset rather than inside a DO block, because psql does NOT
-- interpolate :'variables' inside dollar-quoted strings -- the substitution
-- would silently not happen and the role would be created with the literal
-- text. psql aborts with "variable not set" when -v is omitted, which is the
-- behaviour we want: no password must stop the script, not quietly produce a
-- guessable login.
SELECT NOT EXISTS (
    SELECT 1 FROM pg_roles WHERE rolname = 'claude_readonly'
) AS create_observer_role \gset

\if :create_observer_role
CREATE ROLE claude_readonly LOGIN PASSWORD :'observer_password';
\endif

-- 3. Grant read access to pg_stat_statements view
GRANT SELECT ON pg_stat_statements TO claude_readonly;

-- 4. Grant pg_monitor role (covers pg_stat_activity, pg_stat_user_tables,
--    pg_stat_user_indexes, pg_statio_user_tables, and other monitoring views)
GRANT pg_monitor TO claude_readonly;

-- 5. Grant read access to all existing tables in public and domain schemas
DO $$
DECLARE
    schema_name TEXT;
BEGIN
    FOR schema_name IN
        SELECT nspname FROM pg_namespace
        WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
          AND nspname NOT LIKE 'pg_temp_%'
    LOOP
        EXECUTE format('GRANT USAGE ON SCHEMA %I TO claude_readonly', schema_name);
        EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO claude_readonly', schema_name);
        EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT ON TABLES TO claude_readonly', schema_name);
    END LOOP;
END
$$;

-- Done. Verify with:
--   SELECT * FROM pg_stat_statements LIMIT 1;
--   SELECT * FROM pg_stat_activity LIMIT 1;
