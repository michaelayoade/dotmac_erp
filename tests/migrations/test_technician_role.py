import importlib.util
from pathlib import Path
from typing import Any

import pytest


MIGRATION_PATH = (
    Path(__file__).parents[2] / "alembic" / "versions" / "20260914_technician_role.py"
)
spec = importlib.util.spec_from_file_location(
    "technician_role_migration", MIGRATION_PATH
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
        self.roles: dict[str, tuple[str, bool]] = {
            "employee": ("employee-id", True),
        }
        self.employee_permissions = {"self:access", "expense:claims:create"}
        self.technician_permissions: set[str] = set()

    def exec_driver_sql(
        self,
        statement: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> _RowResult:
        sql = " ".join(statement.split())
        params = parameters or ()

        if sql.startswith("SELECT id, is_active FROM roles"):
            return _RowResult(self.roles.get(str(params[0])))
        if sql.startswith("INSERT INTO roles"):
            self.roles.setdefault("technician", ("technician-id", True))
            return _RowResult()
        if sql.startswith("INSERT INTO role_permissions"):
            assert params == ("technician-id", "employee-id")
            self.technician_permissions.update(self.employee_permissions)
            return _RowResult()
        raise AssertionError(f"Unexpected migration SQL: {sql}")


def test_upgrade_copies_employee_permissions_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    migration.upgrade()
    migration.upgrade()

    assert connection.roles["technician"] == ("technician-id", True)
    assert connection.technician_permissions == connection.employee_permissions
    assert "expense:claims:create" in connection.technician_permissions


@pytest.mark.parametrize(
    ("roles", "message"),
    [
        ({}, "Required RBAC role is missing: employee"),
        ({"employee": ("employee-id", False)}, "RBAC role is inactive: employee"),
        (
            {
                "employee": ("employee-id", True),
                "technician": ("technician-id", False),
            },
            "RBAC role is inactive: technician",
        ),
    ],
)
def test_upgrade_refuses_missing_or_inactive_roles(
    monkeypatch: pytest.MonkeyPatch,
    roles: dict[str, tuple[str, bool]],
    message: str,
) -> None:
    connection = _FakeConnection()
    connection.roles = roles
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)

    with pytest.raises(RuntimeError, match=message):
        migration.upgrade()
