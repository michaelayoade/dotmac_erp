"""PostgreSQL proof for ERP's physical People-hub dependency ledger.

The static architecture gate inventories source declarations; it is deliberately
not a claim about migrated storage.  This gate asks PostgreSQL what the fully
migrated database actually enforces and compares that result to the separately
reviewed physical baseline in ``tests/integration/people_hub_fk_catalog.tsv``.
ORM metadata alone is not sufficient evidence: mixins expand into multiple
tables, overrides suppress inherited declarations, duplicate declarations can
collapse to one physical constraint, and a migration can drift from a model.

Both ledgers use the same six-column identity.  The physical ledger also
freezes update/delete actions, match mode and deferrability.  Their model-only
and physical-only differences are separately ratcheted by the static
architecture gate; neither ledger is allowed to impersonate the other.  A
composite constraint is rejected because one row cannot describe its full
semantics.

This proves the schema produced by ``alembic upgrade heads`` in the integration
lane.  It does not claim that a separately deployed database has applied those
migrations, and it does not infer a physical expectation from whichever ORM
models happened to import during the test.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PHYSICAL_DEPENDENCY_LEDGER = Path(__file__).parent / "people_hub_fk_catalog.tsv"

IDENTITY_FIELDS = (
    "source_schema",
    "source_table",
    "source_column",
    "target_schema",
    "target_table",
    "target_column",
)
CONTRACT_FIELDS = IDENTITY_FIELDS + (
    "on_update",
    "on_delete",
    "match_type",
    "deferrable",
    "initially_deferred",
)
CATALOG_SQL = text(
    """
SELECT
    source_namespace.nspname AS source_schema,
    source_relation.relname AS source_table,
    source_attribute.attname AS source_column,
    target_namespace.nspname AS target_schema,
    target_relation.relname AS target_table,
    target_attribute.attname AS target_column,
    CASE constraint_row.confupdtype
      WHEN 'a' THEN 'NO ACTION'
      WHEN 'r' THEN 'RESTRICT'
      WHEN 'c' THEN 'CASCADE'
      WHEN 'n' THEN 'SET NULL'
      WHEN 'd' THEN 'SET DEFAULT'
    END AS on_update,
    CASE constraint_row.confdeltype
      WHEN 'a' THEN 'NO ACTION'
      WHEN 'r' THEN 'RESTRICT'
      WHEN 'c' THEN 'CASCADE'
      WHEN 'n' THEN 'SET NULL'
      WHEN 'd' THEN 'SET DEFAULT'
    END AS on_delete,
    CASE constraint_row.confmatchtype
      WHEN 's' THEN 'SIMPLE'
      WHEN 'f' THEN 'FULL'
      WHEN 'p' THEN 'PARTIAL'
    END AS match_type,
    constraint_row.condeferrable AS deferrable,
    constraint_row.condeferred AS initially_deferred,
    constraint_row.oid::text AS constraint_oid,
    constraint_row.conname AS constraint_name,
    cardinality(constraint_row.conkey) AS constraint_column_count,
    constraint_row.convalidated AS validated
FROM pg_constraint AS constraint_row
JOIN pg_class AS source_relation
  ON source_relation.oid = constraint_row.conrelid
JOIN pg_namespace AS source_namespace
  ON source_namespace.oid = source_relation.relnamespace
JOIN pg_class AS target_relation
  ON target_relation.oid = constraint_row.confrelid
JOIN pg_namespace AS target_namespace
  ON target_namespace.oid = target_relation.relnamespace
JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY
  AS source_key(attnum, position) ON true
JOIN LATERAL unnest(constraint_row.confkey) WITH ORDINALITY
  AS target_key(attnum, position) ON target_key.position = source_key.position
JOIN pg_attribute AS source_attribute
  ON source_attribute.attrelid = source_relation.oid
 AND source_attribute.attnum = source_key.attnum
 AND NOT source_attribute.attisdropped
JOIN pg_attribute AS target_attribute
  ON target_attribute.attrelid = target_relation.oid
 AND target_attribute.attnum = target_key.attnum
 AND NOT target_attribute.attisdropped
WHERE constraint_row.contype = 'f'
  AND (
    (
      target_namespace.nspname = 'hr'
      AND target_relation.relname = 'employee'
      AND target_attribute.attname = 'employee_id'
    )
    OR (
      target_namespace.nspname = 'public'
      AND target_relation.relname = 'people'
      AND target_attribute.attname = 'id'
    )
  )
ORDER BY
    source_namespace.nspname,
    source_relation.relname,
    source_key.position,
    constraint_row.conname
"""
)


@dataclass(frozen=True, order=True)
class ForeignKeyIdentity:
    source_schema: str
    source_table: str
    source_column: str
    target_schema: str
    target_table: str
    target_column: str

    def render(self) -> str:
        return (
            f"{self.source_schema}.{self.source_table}.{self.source_column} -> "
            f"{self.target_schema}.{self.target_table}.{self.target_column}"
        )


@dataclass(frozen=True, order=True)
class ForeignKeyContract:
    identity: ForeignKeyIdentity
    on_update: str
    on_delete: str
    match_type: str
    deferrable: bool
    initially_deferred: bool

    def render(self) -> str:
        return (
            f"{self.identity.render()} "
            f"[ON UPDATE {self.on_update}; ON DELETE {self.on_delete}; "
            f"MATCH {self.match_type}; DEFERRABLE {self.deferrable}; "
            f"INITIALLY DEFERRED {self.initially_deferred}]"
        )


@dataclass(frozen=True)
class CatalogForeignKey:
    contract: ForeignKeyContract
    constraint_oid: str
    constraint_name: str
    constraint_column_count: int
    validated: bool


def _identity(values: dict[str, object]) -> ForeignKeyIdentity:
    return ForeignKeyIdentity(*(str(values[field]) for field in IDENTITY_FIELDS))


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    assert value in {"true", "false"}, f"invalid PostgreSQL boolean: {value!r}"
    return value == "true"


def _contract(values: dict[str, object]) -> ForeignKeyContract:
    return ForeignKeyContract(
        identity=_identity(values),
        on_update=str(values["on_update"]),
        on_delete=str(values["on_delete"]),
        match_type=str(values["match_type"]),
        deferrable=_bool(values["deferrable"]),
        initially_deferred=_bool(values["initially_deferred"]),
    )


def _physical_declared() -> set[ForeignKeyContract]:
    with PHYSICAL_DEPENDENCY_LEDGER.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == list(CONTRACT_FIELDS), (
            f"{PHYSICAL_DEPENDENCY_LEDGER.relative_to(REPO_ROOT)} has columns "
            f"{reader.fieldnames!r}; expected {list(CONTRACT_FIELDS)!r}"
        )
        contracts = [_contract(row) for row in reader]

    assert contracts, (
        f"{PHYSICAL_DEPENDENCY_LEDGER.relative_to(REPO_ROOT)} cannot be empty"
    )
    identities = [contract.identity for contract in contracts]
    assert len(identities) == len(set(identities)), (
        f"{PHYSICAL_DEPENDENCY_LEDGER.relative_to(REPO_ROOT)} repeats a physical "
        "six-tuple; each identity must have exactly one canonical row"
    )
    return set(contracts)


@pytest.fixture(scope="module")
def observed(engine: Engine) -> tuple[CatalogForeignKey, ...]:
    with engine.connect() as connection:
        rows = connection.execute(CATALOG_SQL).mappings().all()
    return tuple(
        CatalogForeignKey(
            contract=_contract(row),
            constraint_oid=str(row["constraint_oid"]),
            constraint_name=str(row["constraint_name"]),
            constraint_column_count=int(row["constraint_column_count"]),
            validated=bool(row["validated"]),
        )
        for row in rows
    )


def _drift(
    expected: set[ForeignKeyContract], actual: set[ForeignKeyContract]
) -> tuple[list[str], list[str]]:
    missing = sorted(item.render() for item in expected - actual)
    undeclared = sorted(item.render() for item in actual - expected)
    return missing, undeclared


def test_migrated_people_hub_foreign_keys_match_the_dependency_ledger(
    observed: tuple[CatalogForeignKey, ...],
) -> None:
    expected = _physical_declared()
    actual = {row.contract for row in observed}
    missing, undeclared = _drift(expected, actual)

    assert not missing, (
        "the physical People-hub ledger names foreign keys the migrated database "
        "does not enforce:\n" + "\n".join(missing)
    )
    assert not undeclared, (
        "the migrated database has People-hub foreign keys absent from the "
        "reviewed dependency ledger:\n" + "\n".join(undeclared)
    )


def test_people_hub_foreign_keys_are_unique_and_validated(
    observed: tuple[CatalogForeignKey, ...],
) -> None:
    by_identity: dict[ForeignKeyIdentity, list[CatalogForeignKey]] = defaultdict(list)
    for row in observed:
        by_identity[row.contract.identity].append(row)

    duplicates = [
        f"{identity.render()}: "
        + ", ".join(f"{row.constraint_name} (oid={row.constraint_oid})" for row in rows)
        for identity, rows in sorted(by_identity.items())
        if len(rows) > 1
    ]
    unvalidated = sorted(
        f"{row.contract.identity.render()}: {row.constraint_name} "
        f"(oid={row.constraint_oid})"
        for row in observed
        if not row.validated
    )
    composite = sorted(
        f"{row.contract.identity.render()}: {row.constraint_name} has "
        f"{row.constraint_column_count} column pairs"
        for row in observed
        if row.constraint_column_count != 1
    )

    assert not duplicates, (
        "multiple physical constraints enforce the same People-hub column pair:\n"
        + "\n".join(duplicates)
    )
    assert not unvalidated, (
        "People-hub foreign keys exist but are NOT VALID:\n" + "\n".join(unvalidated)
    )
    assert not composite, (
        "the six-column People dependency ledger cannot fully describe these "
        "composite foreign-key constraints:\n" + "\n".join(composite)
    )


def test_people_hub_catalog_drift_detector_is_sensitive() -> None:
    """Prove both sides of the exact comparison can fail (ADR-0018)."""
    expected = _physical_declared()
    sample = min(expected)

    missing, undeclared = _drift(expected, expected - {sample})
    assert missing == [sample.render()]
    assert not undeclared

    missing, undeclared = _drift(expected - {sample}, expected)
    assert not missing
    assert undeclared == [sample.render()]

    changed_action = replace(
        sample,
        on_delete="CASCADE" if sample.on_delete != "CASCADE" else "RESTRICT",
    )
    missing, undeclared = _drift(
        expected,
        (expected - {sample}) | {changed_action},
    )
    assert missing == [sample.render()]
    assert undeclared == [changed_action.render()]
