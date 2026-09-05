"""Migration refusal and DDL contract; no application or database imports."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def load_migration():
    path = Path("alembic/versions/20260905_selfcare_mapping_unique.py")
    spec = spec_from_file_location("selfcare_mapping_migration", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_duplicates_refuse_ddl_with_remediation_guidance():
    migration = load_migration()
    migration.op = MagicMock()
    migration.op.get_bind.return_value.execute.return_value.first.return_value = (
        "organization",
        "account",
    )
    with pytest.raises(RuntimeError, match="administrator.*ALL conflicting records"):
        migration.upgrade()
    migration.op.create_unique_constraint.assert_not_called()
    sql = str(migration.op.get_bind.return_value.execute.call_args.args[0])
    assert "GROUP BY organization_id, dotmac_sub_account_id" in sql
    assert "status" not in sql
    assert "IS NOT NULL" in sql


def test_clean_data_installs_immediate_tenant_scoped_uniqueness():
    migration = load_migration()
    migration.op = MagicMock()
    migration.op.get_bind.return_value.execute.return_value.first.return_value = None
    migration.upgrade()
    migration.op.create_unique_constraint.assert_called_once_with(
        "uq_employee_org_selfcare_account",
        "employee",
        ["organization_id", "dotmac_sub_account_id"],
        schema="hr",
    )
    assert migration.down_revision == "20260902_staff_access_projection"
    assert len(migration.revision) <= 32
    assert [call.args[0] for call in migration.op.execute.call_args_list] == [
        "SET LOCAL row_security = off",
        "LOCK TABLE hr.employee IN SHARE ROW EXCLUSIVE MODE",
    ]


def test_downgrade_only_removes_constraint():
    migration = load_migration()
    migration.op = MagicMock()
    migration.downgrade()
    migration.op.drop_constraint.assert_called_once_with(
        "uq_employee_org_selfcare_account", "employee", schema="hr", type_="unique"
    )
    migration.op.execute.assert_not_called()
