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
--   docker exec -i dotmac_erp_db psql -U postgres -d dotmac_erp < scripts/setup_pg_observability.sql

-- 1. Enable pg_stat_statements (requires shared_preload_libraries in docker-compose.yml)
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- 2. Create the read-only role if it doesn't exist
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'claude_readonly') THEN
        CREATE ROLE claude_readonly LOGIN PASSWORD 'claude_readonly';
    END IF;
END
$$;

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
