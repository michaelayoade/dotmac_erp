import importlib.util
from pathlib import Path
from typing import Any

import pytest

from app.authz.expense import EXPENSE_PERMISSION_DEFINITIONS
from app.authz.payment_execution import (
    EXPENSE_PAYOUT_PERMISSION_DEFINITIONS,
)
from app.authz.profile import (
    EXPENSE_BASELINE_ROLES,
    EXPENSE_PAYOUT_ROLE_GRANTS,
    EXPENSE_ROLE_GRANTS,
)
from scripts.seed_rbac import DEFAULT_ROLES, EXPENSE_PERMISSIONS, ROLE_PERMISSIONS


MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "20260826_provision_expense_permissions.py"
)
spec = importlib.util.spec_from_file_location(
    "expense_permission_provisioning_migration", MIGRATION_PATH
)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class _RowResult:
    def __init__(self, row: tuple[str, bool] | None = None) -> None:
        self._row = row

    def fetchone(self) -> tuple[str, bool] | None:
        return self._row


class _FakeConnection:
    def __init__(self) -> None:
        self.roles: dict[str, tuple[str, bool, str]] = {}
        self.permissions: dict[str, tuple[str, bool, str]] = {}
        self.grants: set[tuple[str, str]] = set()

    def exec_driver_sql(
        self,
        statement: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> _RowResult:
        sql = " ".join(statement.split())
        params = parameters or ()

        if sql.startswith("INSERT INTO roles"):
            name, description = params
            self.roles.setdefault(
                str(name),
                (f"role:{name}", True, str(description)),
            )
            return _RowResult()
        if sql.startswith("SELECT id, is_active FROM roles"):
            role = self.roles.get(str(params[0]))
            return _RowResult(None if role is None else role[:2])
        if sql.startswith("INSERT INTO permissions"):
            code, description = params
            self.permissions.setdefault(
                str(code),
                (f"permission:{code}", True, str(description)),
            )
            return _RowResult()
        if sql.startswith("SELECT id, is_active FROM permissions"):
            permission = self.permissions.get(str(params[0]))
            return _RowResult(None if permission is None else permission[:2])
        if sql.startswith("INSERT INTO role_permissions"):
            self.grants.add((str(params[0]), str(params[1])))
            return _RowResult()
        raise AssertionError(f"Unexpected migration SQL: {sql}")


def _seed_expense_role_grants() -> dict[str, tuple[str, ...]]:
    return {
        role: tuple(code for code in codes if code.startswith("expense:"))
        for role, codes in ROLE_PERMISSIONS.items()
        if any(code.startswith("expense:") for code in codes)
    }


def _seed_expense_payout_role_grants() -> dict[str, tuple[str, ...]]:
    payout_codes = {code for code, _ in EXPENSE_PAYOUT_PERMISSION_DEFINITIONS}
    return {
        role: tuple(code for code in codes if code in payout_codes)
        for role, codes in ROLE_PERMISSIONS.items()
        if any(code in payout_codes for code in codes)
    }


def _declared_role_grants() -> dict[str, tuple[str, ...]]:
    return {
        role: (
            *EXPENSE_ROLE_GRANTS.get(role, ()),
            *EXPENSE_PAYOUT_ROLE_GRANTS.get(role, ()),
        )
        for role in EXPENSE_BASELINE_ROLES
    }


def test_migration_is_a_frozen_copy_of_the_expense_seed_profile() -> None:
    assert len(EXPENSE_PERMISSION_DEFINITIONS) == 35  # non-vacuity
    assert tuple(EXPENSE_PERMISSIONS) == EXPENSE_PERMISSION_DEFINITIONS
    assert _seed_expense_role_grants() == EXPENSE_ROLE_GRANTS
    assert _seed_expense_payout_role_grants() == EXPENSE_PAYOUT_ROLE_GRANTS
    assert {
        role: description
        for role, description in DEFAULT_ROLES
        if role in EXPENSE_BASELINE_ROLES
    } == EXPENSE_BASELINE_ROLES
    assert migration.EXPENSE_PERMISSIONS == EXPENSE_PERMISSION_DEFINITIONS
    assert migration.EXPENSE_PAYOUT_PERMISSIONS == EXPENSE_PAYOUT_PERMISSION_DEFINITIONS
    assert migration.ROLE_DESCRIPTIONS == EXPENSE_BASELINE_ROLES
    assert migration.ROLE_EXPENSE_GRANTS == EXPENSE_ROLE_GRANTS
    assert migration.ROLE_EXPENSE_PAYOUT_GRANTS == EXPENSE_PAYOUT_ROLE_GRANTS
    assert _declared_role_grants() == migration.ROLE_GRANTS


def test_every_grant_references_a_declared_permission_and_role() -> None:
    permission_codes = {
        code
        for code, _ in (
            *migration.EXPENSE_PERMISSIONS,
            *migration.EXPENSE_PAYOUT_PERMISSIONS,
        )
    }

    assert set(migration.ROLE_GRANTS) == set(migration.ROLE_DESCRIPTIONS)
    assert permission_codes
    assert all(
        set(permission_codes_for_role) <= permission_codes
        for permission_codes_for_role in migration.ROLE_GRANTS.values()
    )


def test_upgrade_is_additive_idempotent_and_preserves_operator_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    connection.roles["admin"] = ("existing-admin", True, "Operator description")
    connection.permissions["expense:access"] = (
        "existing-access",
        True,
        "Operator description",
    )
    connection.permissions["custom:permission"] = (
        "custom-permission",
        True,
        "Custom",
    )
    connection.grants.add(("existing-admin", "custom-permission"))
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    migration.upgrade()
    first_state = (
        dict(connection.roles),
        dict(connection.permissions),
        set(connection.grants),
    )
    migration.upgrade()

    assert connection.roles["admin"][2] == "Operator description"
    assert connection.permissions["expense:access"][2] == "Operator description"
    assert ("existing-admin", "custom-permission") in connection.grants
    assert len(connection.roles) == len(migration.ROLE_DESCRIPTIONS)
    assert len(connection.permissions) == (
        len(migration.EXPENSE_PERMISSIONS)
        + len(migration.EXPENSE_PAYOUT_PERMISSIONS)
        + 1
    )
    assert first_state == (
        connection.roles,
        connection.permissions,
        connection.grants,
    )


@pytest.mark.parametrize(
    ("catalogue", "key", "message"),
    [
        ("roles", "employee", "Expense RBAC role is inactive: employee"),
        (
            "permissions",
            "expense:claims:create",
            "Expense RBAC permission is inactive: expense:claims:create",
        ),
        (
            "permissions",
            "payments:transfer:initiate",
            "Expense RBAC permission is inactive: payments:transfer:initiate",
        ),
    ],
)
def test_upgrade_refuses_to_reactivate_operator_disabled_state(
    monkeypatch: pytest.MonkeyPatch,
    catalogue: str,
    key: str,
    message: str,
) -> None:
    connection = _FakeConnection()
    target = getattr(connection, catalogue)
    target[key] = (f"inactive:{key}", False, "Operator disabled")
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    with pytest.raises(RuntimeError, match=message):
        migration.upgrade()
