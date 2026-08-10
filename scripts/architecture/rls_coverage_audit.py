"""Report which organization-scoped tables are actually protected by RLS.

ERP enables row-level security through *point-in-time sweeps*:
``alembic/versions/add_rls_policies.py`` asks ``information_schema`` which
tables carried ``organization_id`` at the moment it ran, over a fixed list of
schemas, and loops. Fourteen further migrations do the same. Nothing re-runs.

The consequence is that **coverage cannot be derived from source**. Whether a
given table is protected depends on when it was created relative to each sweep,
and the model layer has since grown to 37 schemas against that migration's list
of 16. The only sound answer comes from the live catalog, and until this script
there was no ``pg_catalog`` introspection anywhere in ``app/`` or ``tests/`` —
so the question "which of our organization-scoped tables are protected?" had no
answer short of opening psql.

That matters beyond tidiness. Isolation here is two independent layers (see
``app/db/session_context.py``): a SQLAlchemy ORM listener and PostgreSQL RLS.
A table can therefore be *unprotected at the database* while every ORM read
looks correct, and the gap only shows when something reaches the database by
another path — a raw query, a task that primed one layer, an export.

Usage::

    python scripts/architecture/rls_coverage_audit.py                # report
    python scripts/architecture/rls_coverage_audit.py --json         # machine
    python scripts/architecture/rls_coverage_audit.py --enforce      # fail on gaps
    python scripts/architecture/rls_coverage_audit.py --enforce \\
        --baseline docs/rls-coverage-baseline.json                   # fail on NEW gaps

Report mode is the default deliberately. The coverage number is unknown at the
time of writing, and a gate that fails an unknown number of times on its first
run gets disabled rather than fixed. Measure, record a baseline, then enforce
that the baseline only shrinks — the shape ADR-0008's migration sequence gate
already uses in Sub.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

from sqlalchemy import create_engine, text

# Schemas that belong to the server or to Alembic bookkeeping, never to a tenant.
SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

# The column ERP scopes by. The kernel's equivalent is `tenant_id`; reconciling
# the two is an E8 decision and deliberately not assumed here.
SCOPE_COLUMN = "organization_id"

_CATALOG_SQL = text(
    """
    SELECT
        n.nspname                                   AS schema_name,
        c.relname                                   AS table_name,
        c.relrowsecurity                            AS rls_enabled,
        c.relforcerowsecurity                       AS rls_forced,
        EXISTS (
            SELECT 1 FROM information_schema.columns col
            WHERE col.table_schema = n.nspname
              AND col.table_name  = c.relname
              AND col.column_name = :scope_column
        )                                           AS has_scope_column,
        (
            SELECT count(*) FROM pg_policies p
            WHERE p.schemaname = n.nspname AND p.tablename = c.relname
        )                                           AS policy_count
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r'
      AND n.nspname <> ALL(:system_schemas)
      AND n.nspname NOT LIKE 'pg_temp%'
    ORDER BY n.nspname, c.relname
    """
)


@dataclass
class Table:
    schema: str
    name: str
    rls_enabled: bool
    rls_forced: bool
    has_scope_column: bool
    policy_count: int

    @property
    def qualified(self) -> str:
        return f"{self.schema}.{self.name}"

    @property
    def verdict(self) -> str:
        """One of: protected, unforced, unpolicied, unprotected, global, orphan-policy.

        `unforced` is called out separately because it is the quiet one: RLS is
        on, policies exist, every test passes — and the table owner bypasses all
        of it. Without FORCE, isolation holds for everyone except the role most
        likely to be running a migration or a repair script.
        """
        if self.has_scope_column:
            if not self.rls_enabled:
                return "unprotected"
            if self.policy_count == 0:
                return "unpolicied"
            if not self.rls_forced:
                return "unforced"
            return "protected"
        return "orphan-policy" if self.rls_enabled else "global"


@dataclass
class Report:
    tables: list[Table] = field(default_factory=list)

    def of(self, verdict: str) -> list[Table]:
        return [t for t in self.tables if t.verdict == verdict]

    @property
    def scoped(self) -> list[Table]:
        return [t for t in self.tables if t.has_scope_column]

    @property
    def gaps(self) -> list[Table]:
        """Everything that carries the scope column but is not fully protected."""
        return [
            t
            for t in self.tables
            if t.verdict in ("unprotected", "unpolicied", "unforced")
        ]

    def render(self) -> str:
        lines: list[str] = []
        total, scoped = len(self.tables), len(self.scoped)
        prot = len(self.of("protected"))
        pct = f"{100 * prot / scoped:.1f}%" if scoped else "n/a"
        lines.append("RLS coverage against the live catalog")
        lines.append(f"  tables                    {total}")
        lines.append(f"  carrying {SCOPE_COLUMN:<16} {scoped}")
        lines.append(f"    fully protected         {prot}  ({pct} of scoped)")
        for verdict, blurb in (
            ("unprotected", "RLS not enabled"),
            ("unpolicied", "RLS enabled, NO policy — denies everything"),
            ("unforced", "RLS enabled but not FORCED — the owner bypasses it"),
        ):
            found = self.of(verdict)
            if found:
                lines.append(f"    {verdict:<23} {len(found)}  ({blurb})")
        lines.append(f"  global (no {SCOPE_COLUMN})   {len(self.of('global'))}")
        orphans = self.of("orphan-policy")
        if orphans:
            lines.append(
                f"  orphan-policy             {len(orphans)}  "
                f"(RLS on a table with no {SCOPE_COLUMN})"
            )
        for verdict in ("unprotected", "unpolicied", "unforced", "orphan-policy"):
            found = self.of(verdict)
            if not found:
                continue
            lines.append("")
            lines.append(f"  {verdict}:")
            lines.extend(f"    {t.qualified}" for t in found)
        return "\n".join(lines)

    def as_baseline(self) -> dict[str, list[str]]:
        return {"known_gaps": sorted(t.qualified for t in self.gaps)}


def collect(database_url: str) -> Report:
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                _CATALOG_SQL,
                {"scope_column": SCOPE_COLUMN, "system_schemas": list(SYSTEM_SCHEMAS)},
            ).all()
    finally:
        engine.dispose()
    return Report(
        [
            Table(
                schema=r.schema_name,
                name=r.table_name,
                rls_enabled=bool(r.rls_enabled),
                rls_forced=bool(r.rls_forced),
                has_scope_column=bool(r.has_scope_column),
                policy_count=int(r.policy_count),
            )
            for r in rows
        ]
    )


def resolve_database_url(explicit: str | None) -> str:
    for candidate in (
        explicit,
        os.getenv("TEST_DATABASE_URL"),
        os.getenv("DATABASE_URL"),
    ):
        if candidate:
            return candidate
    raise SystemExit(
        "No database URL. Pass --database-url, or set TEST_DATABASE_URL / DATABASE_URL.\n"
        "This audit reads the LIVE catalog — it cannot be answered from source."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--database-url")
    ap.add_argument("--json", action="store_true", help="emit the baseline shape")
    ap.add_argument("--enforce", action="store_true", help="exit non-zero on gaps")
    ap.add_argument(
        "--baseline",
        help="JSON file of already-known gaps; with --enforce, only NEW gaps fail",
    )
    args = ap.parse_args(argv)

    report = collect(resolve_database_url(args.database_url))

    if args.json:
        print(json.dumps(report.as_baseline(), indent=2, sort_keys=True))
    else:
        print(report.render())

    if not args.enforce:
        return 0

    gaps = {t.qualified for t in report.gaps}
    if args.baseline:
        with open(args.baseline) as fh:
            known = set(json.load(fh).get("known_gaps", []))
        # Only NEW gaps fail. A gap that disappears is progress, not a failure —
        # but it must be removed from the baseline so it cannot silently return.
        new, fixed = sorted(gaps - known), sorted(known - gaps)
        if fixed:
            print(f"\n{len(fixed)} baseline entries are now protected — remove them:")
            print("\n".join(f"  {q}" for q in fixed))
        if new:
            print(f"\nFAIL: {len(new)} newly unprotected {SCOPE_COLUMN} table(s):")
            print("\n".join(f"  {q}" for q in new))
            return 1
        return 0

    if gaps:
        print(f"\nFAIL: {len(gaps)} {SCOPE_COLUMN} table(s) are not fully protected.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
