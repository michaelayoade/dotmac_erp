"""PostgreSQL proof for the legacy Employee ownership boundary."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNERSHIP_LEDGER = (
    REPO_ROOT / "docs" / "inventories" / "people-employee-field-ownership.tsv"
)
OWNERSHIP_FIELDS = (
    "source_entity",
    "source_field",
    "current_authority",
    "target_disposition",
    "intended_owner",
    "decision_status",
    "notes",
)

EMPLOYEE_COLUMNS_SQL = text(
    """
SELECT column_name
FROM information_schema.columns
WHERE table_schema = 'hr'
  AND table_name = 'employee'
ORDER BY column_name
"""
)

HR_TABLES_SQL = text(
    """
SELECT table_schema || '.' || table_name AS entity
FROM information_schema.tables
WHERE table_schema = 'hr'
  AND table_type = 'BASE TABLE'
"""
)


def _ownership_rows() -> list[dict[str, str]]:
    with OWNERSHIP_LEDGER.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert tuple(reader.fieldnames or ()) == OWNERSHIP_FIELDS
        return list(reader)


def _format_delta(*, missing: set[str], stale: set[str]) -> str:
    return (
        f"Missing from the ownership ledger: {sorted(missing)}\n"
        f"No longer present in PostgreSQL: {sorted(stale)}"
    )


def _catalog_delta(expected: set[str], actual: set[str]) -> tuple[set[str], set[str]]:
    return actual - expected, expected - actual


def test_migrated_employee_columns_match_the_ownership_ledger(engine: Engine) -> None:
    expected = {
        row["source_field"]
        for row in _ownership_rows()
        if row["source_entity"] == "hr.employee"
    }
    with engine.connect() as connection:
        actual = set(connection.execute(EMPLOYEE_COLUMNS_SQL).scalars())

    missing, stale = _catalog_delta(expected, actual)
    assert not missing and not stale, _format_delta(missing=missing, stale=stale)


def test_every_extended_employee_entity_exists_after_migration(engine: Engine) -> None:
    expected = {
        row["source_entity"]
        for row in _ownership_rows()
        if row["source_entity"] != "hr.employee"
    }
    with engine.connect() as connection:
        catalog = set(connection.execute(HR_TABLES_SQL).scalars())

    missing = expected - catalog
    assert not missing, (
        f"Ownership-ledger entities absent from PostgreSQL: {sorted(missing)}"
    )


def test_employee_column_catalog_detector_is_sensitive() -> None:
    expected = {"employee_id", "status"}

    missing, stale = _catalog_delta(expected, {"employee_id", "status", "new"})
    assert missing == {"new"}
    assert not stale

    missing, stale = _catalog_delta(expected, {"employee_id"})
    assert not missing
    assert stale == {"status"}
