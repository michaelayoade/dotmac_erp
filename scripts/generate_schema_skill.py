"""
Generate SCHEMA_REF.md for the db-schema Claude Code skill.

Introspects the live PostgreSQL database and produces a complete column-level
reference of all tables, primary keys, foreign keys, and enum types. Run after
every migration to keep the skill current.

Usage:
    poetry run python scripts/generate_schema_skill.py
    # or: make schema-skill
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg  # noqa: F401 — used for connect()

# ── Configuration ────────────────────────────────────────────────────────────


def _default_dsn() -> str:
    """Build DSN from .env file if SCHEMA_DSN not set."""
    env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    pw = "postgres"
    port = "5437"
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line.startswith("POSTGRES_PASSWORD="):
                    pw = line.split("=", 1)[1]
                elif line.startswith("DB_PORT="):
                    port = line.split("=", 1)[1]
    from urllib.parse import quote

    return f"postgresql://postgres:{quote(pw, safe='')}@localhost:{port}/dotmac_erp"


DSN = os.environ.get("SCHEMA_DSN") or _default_dsn()

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    ".claude",
    "skills",
    "db-schema",
    "SCHEMA_REF.md",
)

# Schemas to document (ordered by business importance)
SCHEMAS = [
    # Core business
    "ar",
    "ap",
    "gl",
    "banking",
    "tax",
    # People
    "hr",
    "payroll",
    "leave",
    "attendance",
    "perf",
    "recruit",
    "training",
    "scheduling",
    # Operations
    "inv",
    "fa",
    "fleet",
    "pm",
    "proc",
    "expense",
    "support",
    # Platform
    "core_org",
    "core_config",
    "core_fx",
    "cons",
    "fin_inst",
    "ipsas",
    "lease",
    "payments",
    "automation",
    "sync",
    "rpt",
    "audit",
    "platform",
    # Shared
    "public",
    "settings",
    "common",
    "exp",
    "migration",
    "people",
]


# ── Database helpers ─────────────────────────────────────────────────────────


def _redact_dsn(dsn: str) -> str:
    """Best-effort redaction so we don't print credentials to stderr."""
    try:
        parts = urlsplit(dsn)
        if not parts.username and not parts.password:
            return dsn
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        # Keep username for debugging; always redact password.
        if parts.username:
            netloc = f"{parts.username}:***@{host}"
        else:
            netloc = host
        return urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
        )
    except Exception:
        return "<redacted>"


def _connect() -> psycopg.Connection[tuple[Any, ...]]:
    """Connect to the database."""
    try:
        return psycopg.connect(DSN)
    except psycopg.OperationalError as e:
        print(f"ERROR: Cannot connect to database: {e}", file=sys.stderr)
        print(f"  DSN: {_redact_dsn(DSN)}", file=sys.stderr)
        print("  Set SCHEMA_DSN env var or ensure the DB is running.", file=sys.stderr)
        sys.exit(1)


def _query(
    cur: psycopg.Cursor[Any], sql: str, params: tuple[Any, ...] = ()
) -> list[tuple[Any, ...]]:
    """Execute a query and return all rows."""
    cur.execute(sql, params)
    return cur.fetchall()


def get_schemas(cur: psycopg.Cursor[Any]) -> list[str]:
    """Get schemas that actually exist in the database."""
    rows = _query(
        cur,
        """
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
          AND schema_name NOT LIKE 'pg_temp%%'
          AND schema_name NOT LIKE 'pg_toast_temp%%'
        ORDER BY schema_name
    """,
    )
    existing = {row[0] for row in rows}
    ordered = [s for s in SCHEMAS if s in existing]
    for s in sorted(existing):
        if s not in ordered:
            ordered.append(s)
    return ordered


def get_tables(cur: psycopg.Cursor[Any], schema: str) -> list[str]:
    """Get all base tables in a schema."""
    rows = _query(
        cur,
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """,
        (schema,),
    )
    return [row[0] for row in rows]


def get_columns(
    cur: psycopg.Cursor[Any], schema: str, table: str
) -> list[dict[str, str]]:
    """Get columns for a table with type and nullability."""
    rows = _query(
        cur,
        """
        SELECT
            column_name,
            CASE
                WHEN data_type = 'USER-DEFINED' THEN udt_name
                WHEN data_type = 'character varying' THEN 'varchar(' || character_maximum_length || ')'
                WHEN data_type = 'character' THEN 'char(' || character_maximum_length || ')'
                WHEN data_type = 'numeric' THEN 'numeric(' || numeric_precision || ',' || numeric_scale || ')'
                WHEN data_type = 'ARRAY' THEN udt_name
                ELSE data_type
            END as display_type,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
    """,
        (schema, table),
    )
    return [
        {"name": row[0], "type": row[1], "nullable": row[2], "default": row[3]}
        for row in rows
    ]


def get_primary_keys(cur: psycopg.Cursor[Any], schema: str, table: str) -> list[str]:
    """Get primary key columns for a table."""
    rows = _query(
        cur,
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid
          AND a.attnum = ANY(i.indkey)
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
        ORDER BY array_position(i.indkey, a.attnum)
    """,
        (schema, table),
    )
    return [row[0] for row in rows]


def get_foreign_keys(
    cur: psycopg.Cursor[Any], schema: str, table: str
) -> list[dict[str, str]]:
    """Get foreign key relationships for a table (including cross-schema).

    Uses pg_constraint instead of information_schema to avoid:
    - Privilege-filtering (information_schema hides FKs from non-owning users)
    - Cross-schema FK omission (the old join filtered on same-schema only)
    - Composite FK cartesian products
    """
    rows = _query(
        cur,
        """
        SELECT
            a_src.attname AS src_column,
            rn.nspname || '.' || rc.relname AS ref_table,
            a_ref.attname AS ref_column
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_class rc ON rc.oid = con.confrelid
        JOIN pg_namespace rn ON rn.oid = rc.relnamespace
        JOIN LATERAL unnest(con.conkey, con.confkey)
             WITH ORDINALITY AS cols(src_attnum, ref_attnum, ord) ON true
        JOIN pg_attribute a_src ON a_src.attrelid = con.conrelid
             AND a_src.attnum = cols.src_attnum
        JOIN pg_attribute a_ref ON a_ref.attrelid = con.confrelid
             AND a_ref.attnum = cols.ref_attnum
        WHERE con.contype = 'f'
          AND n.nspname = %s
          AND c.relname = %s
        ORDER BY con.conname, cols.ord
    """,
        (schema, table),
    )
    return [
        {"column": row[0], "ref_table": row[1], "ref_column": row[2]} for row in rows
    ]


def get_enums(cur: psycopg.Cursor[Any]) -> dict[str, list[str]]:
    """Get all enum types and their values."""
    rows = _query(
        cur,
        """
        SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder)
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        GROUP BY t.typname
        ORDER BY t.typname
    """,
    )
    return {row[0]: row[1] for row in rows}


def get_row_count(cur: psycopg.Cursor[Any], schema: str, table: str) -> int:
    """Get approximate row count from pg_stat (fast, no seq scan)."""
    rows = _query(
        cur,
        """
        SELECT reltuples::bigint
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s
    """,
        (schema, table),
    )
    return max(0, rows[0][0]) if rows else 0


def get_cross_schema_fks(
    cur: psycopg.Cursor[Any],
) -> list[dict[str, str]]:
    """Get all foreign keys that cross schema boundaries.

    Uses pg_constraint to avoid information_schema privilege-filtering
    and cartesian products on composite FKs.

    Returns rows sorted by source schema + table, filtering out the
    ubiquitous organization_id and audit (created_by_id / updated_by_id) FKs.
    """
    rows = _query(
        cur,
        """
        SELECT
            sn.nspname  AS src_schema,
            sc.relname  AS src_table,
            a_src.attname AS src_column,
            rn.nspname  AS ref_schema,
            rc.relname  AS ref_table,
            a_ref.attname AS ref_column
        FROM pg_constraint con
        JOIN pg_class sc ON sc.oid = con.conrelid
        JOIN pg_namespace sn ON sn.oid = sc.relnamespace
        JOIN pg_class rc ON rc.oid = con.confrelid
        JOIN pg_namespace rn ON rn.oid = rc.relnamespace
        JOIN LATERAL unnest(con.conkey, con.confkey)
             WITH ORDINALITY AS cols(src_attnum, ref_attnum, ord) ON true
        JOIN pg_attribute a_src ON a_src.attrelid = con.conrelid
             AND a_src.attnum = cols.src_attnum
        JOIN pg_attribute a_ref ON a_ref.attrelid = con.confrelid
             AND a_ref.attnum = cols.ref_attnum
        WHERE con.contype = 'f'
          AND sn.nspname <> rn.nspname
          AND a_src.attname NOT IN (
              'organization_id', 'created_by_id', 'updated_by_id'
          )
        ORDER BY sn.nspname, sc.relname, a_src.attname
    """,
    )
    return [
        {
            "src_schema": r[0],
            "src_table": r[1],
            "src_column": r[2],
            "ref_schema": r[3],
            "ref_table": r[4],
            "ref_column": r[5],
        }
        for r in rows
    ]


def get_indexes(
    cur: psycopg.Cursor[Any], schema: str, table: str
) -> list[dict[str, Any]]:
    """Get non-PK indexes for a table."""
    rows = _query(
        cur,
        """
        SELECT
            i.relname AS index_name,
            ix.indisunique AS is_unique,
            array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) AS columns
        FROM pg_index ix
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(ix.indkey)
        WHERE n.nspname = %s
          AND t.relname = %s
          AND NOT ix.indisprimary
        GROUP BY i.relname, ix.indisunique
        ORDER BY i.relname
    """,
        (schema, table),
    )
    return [{"name": r[0], "unique": r[1], "columns": r[2]} for r in rows]


# ── Generator ────────────────────────────────────────────────────────────────


def generate() -> None:
    """Generate the SCHEMA_REF.md file."""
    conn = _connect()
    cur = conn.cursor()

    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []

    lines.append("# DotMac ERP — Complete Database Schema Reference")
    lines.append("")
    lines.append(f"*Auto-generated on {now} from live database.*")
    lines.append("*Run `make schema-skill` to regenerate after migrations.*")
    lines.append("")

    # ── Summary ──────────────────────────────────────────────────────────
    schemas = get_schemas(cur)
    total_tables = 0
    # Cache tables per schema to avoid querying twice
    schema_tables: dict[str, list[str]] = {}
    schema_counts: list[tuple[str, int]] = []

    for schema in schemas:
        tables = get_tables(cur, schema)
        schema_tables[schema] = tables
        schema_counts.append((schema, len(tables)))
        total_tables += len(tables)

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **{len(schemas)} schemas**, **{total_tables} tables**")
    top = ", ".join(
        f"`{s}` ({c})" for s, c in sorted(schema_counts, key=lambda x: -x[1])[:10]
    )
    lines.append(f"- Top schemas: {top}")
    lines.append("")

    # ── Enum Reference ───────────────────────────────────────────────────
    enums = get_enums(cur)
    lines.append(f"## Enum Types ({len(enums)} total)")
    lines.append("")

    for enum_name, values in enums.items():
        val_str = ", ".join(values)
        if len(val_str) > 120:
            val_str = val_str[:117] + "..."
        lines.append(f"- **`{enum_name}`**: {val_str}")

    lines.append("")

    # ── Per-Schema Tables ────────────────────────────────────────────────
    for schema in schemas:
        tables = schema_tables[schema]
        if not tables:
            continue

        lines.append("---")
        lines.append("")
        lines.append(f"## `{schema}` schema ({len(tables)} tables)")
        lines.append("")

        for table in tables:
            pk_cols = get_primary_keys(cur, schema, table)
            columns = get_columns(cur, schema, table)
            fks = get_foreign_keys(cur, schema, table)
            indexes = get_indexes(cur, schema, table)
            row_count = get_row_count(cur, schema, table)

            pk_str = ", ".join(pk_cols) if pk_cols else "none"
            lines.append(f"### `{schema}.{table}`")
            lines.append(f"PK: `{pk_str}` | ~{row_count:,} rows")
            lines.append("")
            lines.append("| Column | Type | Null | Default |")
            lines.append("|--------|------|------|---------|")

            for col in columns:
                nullable = "YES" if col["nullable"] == "YES" else ""
                default = col["default"] or ""
                # Escape pipe characters that would break markdown tables
                default = default.replace("|", "\\|")
                if len(default) > 40:
                    default = default[:37] + "..."
                name = col["name"]
                if name in pk_cols:
                    name = f"**{name}** (PK)"
                lines.append(f"| {name} | {col['type']} | {nullable} | {default} |")

            if fks:
                lines.append("")
                lines.append("**Foreign keys:**")
                for fk in fks:
                    lines.append(
                        f"- `{fk['column']}` -> `{fk['ref_table']}.{fk['ref_column']}`"
                    )

            if indexes:
                lines.append("")
                lines.append("**Indexes:**")
                for idx in indexes:
                    cols = ", ".join(idx["columns"])
                    unique = " (unique)" if idx["unique"] else ""
                    lines.append(f"- `{idx['name']}`: ({cols}){unique}")

            lines.append("")

    # ── Cross-Schema Relationships ───────────────────────────────────────
    cross_fks = get_cross_schema_fks(cur)
    lines.append("---")
    lines.append("")
    lines.append(f"## Cross-Schema Foreign Keys ({len(cross_fks)} relationships)")
    lines.append("")
    lines.append(
        "These are FK constraints where the source and target live in different "
        "schemas (excluding the ubiquitous `organization_id`, `created_by_id`, "
        "and `updated_by_id` columns)."
    )
    lines.append("")

    # Group by source schema for readability

    lines.append("| Source | FK Column | Target | Ref Column |")
    lines.append("|--------|-----------|--------|------------|")
    for fk in cross_fks:
        src = f"`{fk['src_schema']}.{fk['src_table']}`"
        tgt = f"`{fk['ref_schema']}.{fk['ref_table']}`"
        lines.append(f"| {src} | `{fk['src_column']}` | {tgt} | `{fk['ref_column']}` |")
    lines.append("")

    # ── Schema dependency graph (which schemas reference which) ────────
    schema_deps: dict[str, set[str]] = {}
    for fk in cross_fks:
        key = fk["src_schema"]
        schema_deps.setdefault(key, set()).add(fk["ref_schema"])

    lines.append("### Schema Dependency Summary")
    lines.append("")
    lines.append("Which schemas reference which (excluding org/audit FKs):")
    lines.append("")
    for src_schema in sorted(schema_deps.keys()):
        targets = sorted(schema_deps[src_schema])
        lines.append(f"- **`{src_schema}`** -> {', '.join(f'`{t}`' for t in targets)}")
    lines.append("")

    cur.close()
    conn.close()

    # ── Write output ─────────────────────────────────────────────────────
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        f.write("\n".join(lines))

    print(f"Generated: {OUTPUT_PATH}")
    print(f"  {len(schemas)} schemas, {total_tables} tables, {len(enums)} enums")
    print(f"  {len(cross_fks)} cross-schema FKs")
    print(f"  {len(lines)} lines written")


if __name__ == "__main__":
    generate()
