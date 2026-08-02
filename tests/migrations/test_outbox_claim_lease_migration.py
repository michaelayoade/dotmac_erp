"""Guards for the E3 outbox claim/lease migration.

The recurring defect class in this repo family is PG enum handling in
Alembic (``ALTER TYPE ... ADD VALUE`` autocommit blocks; ``sa.Enum``
columns re-emitting ``CREATE TYPE`` on incremental upgrades). E3
deliberately avoids touching any enum: claim visibility rides new
nullable columns and terminal reasons are plain VARCHAR.
"""

import ast
from pathlib import Path

MIGRATION = Path("alembic/versions/20260802_add_outbox_claim_lease_columns.py")


def _text() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _code_without_docstring() -> str:
    """Migration source with the module docstring removed (the docstring
    documents the enum gotcha, so enum keywords may appear there)."""
    text = _text()
    docstring = ast.get_docstring(ast.parse(text))
    return text.replace(docstring or "", "", 1)


def test_migration_exists_and_chains_from_previous_head() -> None:
    text = _text()
    assert 'revision = "20260802_add_outbox_claim_lease_columns"' in text
    assert 'down_revision = "20260723_driver_fleet_rbac"' in text


def test_migration_adds_claim_and_evidence_columns() -> None:
    text = _text()
    for column in (
        "error_class",
        "terminal_reason",
        "claimed_by",
        "claim_token",
        "claimed_at",
        "lease_expires_at",
    ):
        assert f'"{column}"' in text, f"missing column {column}"
    assert "idx_outbox_claim" in text


def test_migration_never_touches_the_status_enum() -> None:
    code = _code_without_docstring()
    assert "ALTER TYPE" not in code
    assert "sa.Enum" not in code
    assert "CREATE TYPE" not in code
    assert "event_status" not in code


def test_migration_is_guarded_for_incremental_reruns() -> None:
    """Upgrade must inspect existing columns so a partial/rerun apply is safe."""
    text = _text()
    assert "get_columns" in text
    assert "get_indexes" in text
