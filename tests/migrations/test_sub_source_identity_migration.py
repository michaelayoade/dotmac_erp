"""Focused contract for the Sub financial source-identity migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.sql.elements import TextClause

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "20260825_sub_source_identity.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "erp_sub_source_identity_migration", MIGRATION
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _postgres_bind(*, duplicate: tuple[object, str, int] | None = None) -> MagicMock:
    bind = MagicMock()
    bind.dialect = SimpleNamespace(name="postgresql")
    bind.execute.return_value.first.return_value = duplicate
    return bind


def test_duplicate_purchase_order_sources_fail_before_ddl() -> None:
    migration = _load_migration()
    duplicate = ("tenant-1", "sub-wo:duplicate", 2)
    bind = _postgres_bind(duplicate=duplicate)
    migration.op = MagicMock()
    migration.op.get_bind.return_value = bind

    with pytest.raises(
        RuntimeError,
        match="duplicate organization/correlation pair tenant-1/sub-wo:duplicate",
    ):
        migration.upgrade()

    migration.op.alter_column.assert_not_called()
    migration.op.create_index.assert_not_called()
    statement = bind.execute.call_args.args[0]
    assert isinstance(statement, TextClause)
    assert "HAVING count(*) > 1" in str(statement)
    assert "correlation_id LIKE 'sub-wo:%'" in str(statement)


def test_upgrade_lands_opaque_source_widths_and_partial_unique_index() -> None:
    migration = _load_migration()
    migration.op = MagicMock()
    migration.op.get_bind.return_value = _postgres_bind()

    migration.upgrade()

    altered = {
        (call.args[0], call.args[1]): call.kwargs["type_"].length
        for call in migration.op.alter_column.call_args_list
    }
    assert altered == {
        ("purchase_order", "correlation_id"): 139,
        ("purchase_order", "variation_id"): 120,
        ("supplier_invoice", "correlation_id"): 132,
    }

    fingerprint_columns = {
        (call.args[0], call.args[1].name): call.args[1]
        for call in migration.op.add_column.call_args_list
    }
    assert set(fingerprint_columns) == {
        ("purchase_order", "source_fingerprint"),
        ("supplier_invoice", "source_fingerprint"),
    }
    for column in fingerprint_columns.values():
        assert column.type.length == 64
        assert column.nullable is True

    migration.op.create_index.assert_called_once()
    index_call = migration.op.create_index.call_args
    assert index_call.args[:3] == (
        "uq_po_sub_source_correlation",
        "purchase_order",
        ["organization_id", "correlation_id"],
    )
    assert index_call.kwargs["schema"] == "ap"
    assert index_call.kwargs["unique"] is True
    predicate = index_call.kwargs["postgresql_where"]
    assert isinstance(predicate, TextClause)
    assert str(predicate) == "correlation_id LIKE 'sub-wo:%'"
