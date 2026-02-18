---
name: pg-observe
description: "Run PostgreSQL observability diagnostics via the erp-db MCP (slow queries, unused indexes, bloat, locks, cache hit ratio)"
arguments:
  - name: section
    description: "Which check to run: 'slow-queries', 'unused-indexes', 'bloat', 'explain', 'index-usage', 'locks', 'connections', 'cache-hits', or 'all' for everything"
---

# PostgreSQL Observability

Run diagnostic queries against the DotMac ERP database using the `erp-db` MCP server.

## Prerequisites

Run `make pg-observe-setup` once to enable `pg_stat_statements` and grant monitoring permissions.

## Sections

Run the section matching `$ARGUMENTS` (or all sections if `$ARGUMENTS` is "all").

### 1. Top-20 Slow Queries (`slow-queries`)

```sql
SELECT
    round(mean_exec_time::numeric, 2) AS avg_ms,
    calls,
    round(total_exec_time::numeric, 0) AS total_ms,
    round((100 * total_exec_time / NULLIF(sum(total_exec_time) OVER (), 0))::numeric, 1) AS pct,
    left(query, 120) AS query_preview
FROM pg_stat_statements
WHERE userid != (SELECT usesysid FROM pg_user WHERE usename = 'postgres')
ORDER BY mean_exec_time DESC
LIMIT 20;
```

Format as a markdown table. Flag queries with avg_ms > 100 as **SLOW** and > 500 as **CRITICAL**.

### 2. Unused Indexes (`unused-indexes`)

```sql
SELECT
    schemaname || '.' || relname AS table,
    indexrelname AS index,
    pg_size_pretty(pg_relation_size(i.indexrelid)) AS size,
    idx_scan AS scans
FROM pg_stat_user_indexes i
JOIN pg_index USING (indexrelid)
WHERE idx_scan = 0
  AND NOT indisunique
  AND NOT indisprimary
ORDER BY pg_relation_size(i.indexrelid) DESC
LIMIT 30;
```

Report total wasted space. Suggest `DROP INDEX CONCURRENTLY` for confirmed dead indexes.

### 3. Table Bloat & Vacuum Stats (`bloat`)

```sql
SELECT
    schemaname || '.' || relname AS table,
    n_live_tup AS live_rows,
    n_dead_tup AS dead_rows,
    round(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS dead_pct,
    last_vacuum::date AS last_vacuum,
    last_autovacuum::date AS last_autovac,
    last_analyze::date AS last_analyze
FROM pg_stat_user_tables
WHERE n_dead_tup > 1000
ORDER BY n_dead_tup DESC
LIMIT 20;
```

Flag tables with dead_pct > 20% as needing `VACUUM ANALYZE`. Flag tables never vacuumed as **WARNING**.

### 4. EXPLAIN a Query (`explain`)

If the user provides a query after "explain", run:

```sql
EXPLAIN (FORMAT TEXT, COSTS, BUFFERS) <user query here>;
```

**CRITICAL**: Use `EXPLAIN` only — NEVER `EXPLAIN ANALYZE` (which actually executes the query). Parse the output for Seq Scans on large tables, high cost estimates, and nested loops.

### 5. Index Usage Ratios (`index-usage`)

```sql
SELECT
    schemaname || '.' || relname AS table,
    seq_scan,
    idx_scan,
    CASE WHEN (seq_scan + idx_scan) > 0
         THEN round(100.0 * idx_scan / (seq_scan + idx_scan), 1)
         ELSE 0 END AS idx_hit_pct,
    n_live_tup AS rows
FROM pg_stat_user_tables
WHERE n_live_tup > 100
ORDER BY seq_scan DESC
LIMIT 20;
```

Flag tables with idx_hit_pct < 80% and > 10K rows as candidates for new indexes.

### 6. Lock Contention (`locks`)

```sql
SELECT
    pid,
    age(clock_timestamp(), query_start) AS duration,
    usename,
    wait_event_type,
    wait_event,
    state,
    left(query, 100) AS query_preview
FROM pg_stat_activity
WHERE wait_event IS NOT NULL
  AND state != 'idle'
  AND pid != pg_backend_pid()
ORDER BY query_start;
```

If no locks, report "No active lock contention". Flag queries waiting > 5s.

### 7. Connection Stats (`connections`)

```sql
SELECT
    usename,
    state,
    count(*) AS connections,
    max(age(clock_timestamp(), query_start))::text AS longest_query
FROM pg_stat_activity
WHERE pid != pg_backend_pid()
GROUP BY usename, state
ORDER BY connections DESC;
```

Also show: `SELECT count(*) AS total, setting AS max FROM pg_stat_activity, pg_settings WHERE name = 'max_connections' GROUP BY setting;`

Flag if total > 80% of max_connections.

### 8. Cache Hit Ratio (`cache-hits`)

```sql
SELECT
    schemaname || '.' || relname AS table,
    heap_blks_read,
    heap_blks_hit,
    CASE WHEN (heap_blks_hit + heap_blks_read) > 0
         THEN round(100.0 * heap_blks_hit / (heap_blks_hit + heap_blks_read), 2)
         ELSE 100 END AS hit_pct
FROM pg_statio_user_tables
WHERE heap_blks_read > 100
ORDER BY hit_pct ASC
LIMIT 20;
```

Also show the global cache hit ratio:
```sql
SELECT
    round(100.0 * sum(heap_blks_hit) / NULLIF(sum(heap_blks_hit) + sum(heap_blks_read), 0), 2) AS global_cache_hit_pct
FROM pg_statio_user_tables;
```

Flag if global cache hit ratio < 95% as **WARNING** — may need more `shared_buffers`.

## Output Format

Present results as a markdown report with:
- Section headers
- Markdown tables for query results
- **Status indicators**: OK / WARNING / CRITICAL
- **Actionable recommendations** for each finding
- A summary verdict at the end: HEALTHY / NEEDS_ATTENTION / CRITICAL
