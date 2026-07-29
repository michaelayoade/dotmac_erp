import importlib.util
from pathlib import Path

from scripts.seed_rbac import ROLE_PERMISSIONS


MIGRATION_PATH = (
    Path(__file__).parents[2]
    / "alembic"
    / "versions"
    / "20260723_seed_driver_fleet_rbac.py"
)
spec = importlib.util.spec_from_file_location(
    "driver_fleet_rbac_migration", MIGRATION_PATH
)
assert spec and spec.loader
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


def test_driver_migration_matches_seed_permissions():
    migration_permissions = {key for key, _description in migration.DRIVER_PERMISSIONS}

    assert migration_permissions == set(ROLE_PERMISSIONS["driver"])


def test_driver_migration_is_idempotent(monkeypatch):
    statements = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert len(statements) == 1 + len(migration.DRIVER_PERMISSIONS)
    assert "ON CONFLICT (name) DO UPDATE" in str(statements[0])

    permission_statements = statements[1:]
    assert all(
        "ON CONFLICT (key) DO UPDATE" in str(statement)
        for statement in permission_statements
    )
    assert all(
        "ON CONFLICT (role_id, permission_id) DO NOTHING" in str(statement)
        for statement in permission_statements
    )
    assert {
        statement.compile().params["permission_key"]
        for statement in permission_statements
    } == set(ROLE_PERMISSIONS["driver"])
