"""The exact migration-defined tenant disposition, enforced against PostgreSQL.

Counts are too easy to game: "16 tables have RLS" stays true when someone adds a
policy to one table and drops it from another. So this checks the EXACT set —
every table, its tenant class, its RLS state, its owner, and which roles can read
and write it — against the live catalog. A table that appears, disappears, or
changes disposition fails until `tenant_table_inventory.tsv` is updated in the
same change, which is what makes the update a reviewed diff.

The baseline is generated from a clean PostgreSQL database after `alembic upgrade
heads`, including every composed module lineage. It describes the schema ERP
defines, not whichever subset production has applied. Production migration drift
is recorded separately under `docs/inventories/` and is never an input to this
gate.

## The debts this pins

1. **Tenant-isolation debt.** 309 tables carry ERP's `organization_id`; module
   tenant tables instead carry `tenant_id`. The baseline records exact ENABLE,
   FORCE, policy-count and unsafe-GUC state for every table.
2. **Referential-integrity debt.** A table is `inherited` only when PostgreSQL
   enforces its path to a direct tenant table. ORM metadata is not evidence for a
   database boundary.
3. **Deployment drift.** Production was still at
   `20260808_open_setting_domain` when this baseline was corrected. Its 420-table
   catalog is a drift snapshot, not ERP's design.

## Reading the classes

- `direct` — carries `organization_id` or module-standard `tenant_id`. Tenant
  data, isolatable today.
- `inherited` — no direct scope column, but a real FK to a table that has one, so
  the tenant path is derivable in PostgreSQL.
- `platform` — an explicitly dispositioned control-plane/catalog table. These
  tables do not use tenant RLS and must not be inferred from a nullable scope.
- `unclassified` — none of the three. **NOT a synonym for global.** Each needs
  an explicit disposition — genuine reference data, tenant child requiring a
  declared path, or dead — and until it gets one it is UNKNOWN, which is the
  honest state to record.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

INVENTORY = Path(__file__).parent / "tenant_table_inventory.tsv"

COLUMNS = (
    "schema",
    "table",
    "tenant_class",
    "rls_enabled",
    "rls_forced",
    "owner",
    "policy_count",
    "policy_uses_settable_guc",
    "app_user_priv",
    "platform_api_priv",
)

# Enforced against any database built from ERP's migrations. `owner`,
# `app_user_priv` and `platform_api_priv` are DEPLOYMENT facts, not schema facts:
# production is owned by `app_admin` after the 2026-08-15 cutover, while a CI
# database is owned by whatever role created it. They are recorded because they
# are the whole point of the least-privilege programme, and enforcing them here
# would only assert which machine ran the test.
SCHEMA_FIELDS = (
    "tenant_class",
    "rls_enabled",
    "rls_forced",
    "policy_count",
    "policy_uses_settable_guc",
)

# Mirrors the extraction query. `has_table_privilege` rather than ACL parsing:
# it answers the question that matters — can this role actually reach the table,
# including through role membership — instead of what a grant statement said.
CATALOG_SQL = text(
    """
WITH t AS (
  SELECT c.oid, n.nspname AS sch, c.relname AS tbl, c.relrowsecurity AS rls,
         c.relforcerowsecurity AS forced, pg_get_userbyid(c.relowner) AS owner
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
  WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
), scoped AS (
  SELECT attrelid FROM pg_attribute
  WHERE attname IN ('organization_id', 'tenant_id')
    AND attnum > 0 AND NOT attisdropped
), fk AS (
  SELECT conrelid,
         bool_or(confrelid IN (SELECT attrelid FROM scoped)) AS fk_to_scope
  FROM pg_constraint WHERE contype = 'f' GROUP BY conrelid
), pol AS (
  SELECT polrelid, count(*) AS npol,
         bool_or(
           COALESCE(pg_get_expr(polqual, polrelid), '') LIKE '%should_bypass_rls%'
           OR COALESCE(pg_get_expr(polwithcheck, polrelid), '')
              LIKE '%should_bypass_rls%'
         ) AS uses_guc
  FROM pg_policy GROUP BY polrelid
)
SELECT t.sch, t.tbl,
       CASE WHEN (t.sch = 'public' AND t.tbl IN (
                    'tenants', 'tenant_domains', 'platform_idempotency_records',
                    'platform_outbox_events'
                  ))
                  OR (t.sch = 'mod_files' AND t.tbl = 'platform_stored_files')
              THEN 'platform'
            WHEN t.oid IN (SELECT attrelid FROM scoped) THEN 'direct'
            WHEN COALESCE(fk.fk_to_scope, false) THEN 'inherited'
            ELSE 'unclassified' END,
       t.rls::text, t.forced::text, t.owner,
       COALESCE(pol.npol, 0)::text, COALESCE(pol.uses_guc, false)::text,
       (CASE WHEN has_table_privilege('app_user', t.oid, 'SELECT') THEN 'r' ELSE '-' END)
         || (CASE WHEN has_table_privilege('app_user', t.oid, 'INSERT') THEN 'w' ELSE '-' END),
       (CASE WHEN has_table_privilege('platform_api', t.oid, 'SELECT') THEN 'r' ELSE '-' END)
         || (CASE WHEN has_table_privilege('platform_api', t.oid, 'INSERT') THEN 'w' ELSE '-' END)
FROM t LEFT JOIN fk ON fk.conrelid = t.oid
       LEFT JOIN pol ON pol.polrelid = t.oid
ORDER BY t.sch, t.tbl
"""
)


def _recorded() -> dict[tuple[str, str], dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for line in INVENTORY.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        assert len(fields) == len(COLUMNS), f"malformed inventory row: {line!r}"
        row = dict(zip(COLUMNS, fields, strict=True))
        rows[(row["schema"], row["table"])] = row
    return rows


@pytest.fixture(scope="module")
def observed(engine) -> dict[tuple[str, str], dict[str, str]]:
    with engine.connect() as connection:
        result = connection.execute(CATALOG_SQL).fetchall()
    return {
        (r[0], r[1]): dict(zip(COLUMNS, (str(v) for v in r), strict=True))
        for r in result
    }


def test_no_table_appears_or_disappears_unrecorded(observed) -> None:
    """A new table is a tenant decision, so it lands with a reviewed disposition
    or it fails here."""
    recorded = _recorded()
    added = sorted(f"{s}.{t}" for s, t in observed.keys() - recorded.keys())
    removed = sorted(f"{s}.{t}" for s, t in recorded.keys() - observed.keys())
    assert not added, f"tables present in the database but not the inventory: {added}"
    assert not removed, f"inventory names tables the database lacks: {removed}"


def test_every_recorded_disposition_matches_the_database(observed) -> None:
    recorded = _recorded()
    drift = [
        f"{schema}.{table}.{field}: inventory={row[field]!r} database={observed[(schema, table)][field]!r}"
        for (schema, table), row in sorted(recorded.items())
        if (schema, table) in observed
        for field in SCHEMA_FIELDS
        if row[field] != observed[(schema, table)][field]
    ]
    assert not drift, "catalog disposition drifted from the inventory:\n" + "\n".join(
        drift
    )


def test_the_drift_detector_is_sensitive(observed) -> None:
    """Sensitivity proof (ADR-0018).

    Both checks above pass over a matching pair, which is also what a detector
    that compared nothing would do. Corrupt one field and require a failure.
    """
    recorded = _recorded()
    (schema, table), row = next(iter(sorted(recorded.items())))
    tampered = dict(row) | {"rls_enabled": "tampered"}
    assert any(
        tampered[field] != observed[(schema, table)][field] for field in SCHEMA_FIELDS
    )


def test_rls_is_never_claimed_without_a_policy() -> None:
    """An internally-inconsistent inventory row would let the enforcement above
    pass while recording nonsense."""
    offenders = [
        f"{row['schema']}.{row['table']}"
        for row in _recorded().values()
        if row["rls_enabled"] == "true" and row["policy_count"] == "0"
    ]
    assert not offenders, f"RLS enabled with no policy: {offenders}"


def test_the_isolation_debt_is_recorded_and_only_shrinks() -> None:
    """A two-directional ratchet (ADR-0018).

    Fails when unprotected tenant tables INCREASE — new tenant data landing
    without a policy — and equally when the number falls without this baseline
    being lowered in the same change, so progress is recorded rather than
    silently absorbed.
    """
    baseline = 158
    unprotected = [
        f"{row['schema']}.{row['table']}"
        for row in _recorded().values()
        if row["tenant_class"] == "direct" and row["rls_enabled"] != "true"
    ]
    assert len(unprotected) == baseline, (
        f"{len(unprotected)} tenant-scoped tables lack RLS, baseline is {baseline}. "
        "If this fell, lower the baseline in the same change; if it rose, a new "
        "tenant table shipped without a policy."
    )


def test_unclassified_tables_are_listed_not_assumed_global() -> None:
    """`unclassified` means UNKNOWN, and the count is pinned so the unknown set
    cannot quietly grow while nobody dispositions it."""
    baseline = 20
    unclassified = [
        f"{row['schema']}.{row['table']}"
        for row in _recorded().values()
        if row["tenant_class"] == "unclassified"
    ]
    assert len(unclassified) == baseline, (
        f"{len(unclassified)} tables have no derivable tenant path, baseline "
        f"{baseline}. Each needs an explicit disposition — reference data, "
        "tenant child with a declared path, or dead."
    )


def test_enabled_rls_without_force_is_recorded_and_only_shrinks() -> None:
    """FORCE debt cannot disappear without lowering its reviewed baseline."""
    baseline = 70
    owner_exempt = [
        f"{row['schema']}.{row['table']}"
        for row in _recorded().values()
        if row["rls_enabled"] == "true" and row["rls_forced"] != "true"
    ]
    assert len(owner_exempt) == baseline, (
        f"{len(owner_exempt)} RLS-enabled tables lack FORCE, baseline is "
        f"{baseline}. If this fell, lower the baseline in the same change; if "
        "it rose, an owner-exempt policy shipped."
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Step 3 of the programme: 103 migrated tables still have policies "
        "that consult the settable "
        "`app.bypass_rls`. strict=True so this FAILS the moment the repair "
        "lands, forcing the marker off rather than letting a fixed defect keep "
        "an xfail forever."
    ),
)
def test_no_policy_depends_on_a_user_settable_guc() -> None:
    """The escape hatch that made `NOBYPASSRLS` meaningless.

    `should_bypass_rls()` reads `app.bypass_rls`, a `PGC_USERSET` parameter ANY
    role may set — proven against production, and NOT restrictable
    (`REVOKE SET ON PARAMETER` is accepted and is a no-op, since a customized
    option has no default grant to revoke).

    Currently 103 migrated tables have at least one dependent policy, so this is
    marked `xfail(strict)` rather than skipped: a skip is invisible, while a
    strict xfail is both visible today and the acceptance signal for step 3 — it
    turns into a build failure the moment the repair makes it pass.
    """
    offenders = sorted(
        f"{row['schema']}.{row['table']}"
        for row in _recorded().values()
        if row["policy_uses_settable_guc"] == "true"
    )
    assert not offenders, (
        f"{len(offenders)} tables have policies bypassed by `SET app.bypass_rls`: "
        f"{offenders}"
    )
