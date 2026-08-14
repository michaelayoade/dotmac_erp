"""Behavioural proof for the privileged role bootstrap boundary."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.migration_database_roles import (
    ROLE_CONTRACT,
    migration_executor_violations,
    migration_ownership_violations,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap_database_roles.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("database_role_bootstrap", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


class _Connection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.executions: list[object] = []

    def execute(self, statement: object, params: object = None) -> _Rows:
        self.executions.append((statement, params))
        return _Rows(self.rows)


def test_unapproved_drift_refuses_before_any_role_ddl() -> None:
    """A failing bootstrap must not create the other missing roles first."""
    module = _load_script()
    connection = _Connection([("app_user", False, True)])

    result = module.bootstrap(connection, dry_run=False, repair=False)

    assert result == 1
    assert len(connection.executions) == 1, (
        "only the catalogue observation may run when existing drift was not approved"
    )


def test_a_superuser_connection_cannot_substitute_for_app_admin() -> None:
    violations = migration_executor_violations("postgres", ROLE_CONTRACT)

    assert any("required 'app_admin'" in violation for violation in violations)


def test_exact_app_admin_executor_and_role_posture_pass() -> None:
    assert migration_executor_violations("app_admin", ROLE_CONTRACT) == ()


def test_existing_objects_owned_by_the_old_executor_fail_closed() -> None:
    violations = migration_ownership_violations(
        {"database": 1, "relation": 424, "schema": 39}
    )

    assert violations == (
        "1 non-extension database object(s) are not owned by 'app_admin'",
        "424 non-extension relation object(s) are not owned by 'app_admin'",
        "39 non-extension schema object(s) are not owned by 'app_admin'",
    )


def test_an_app_admin_owned_catalog_passes() -> None:
    assert migration_ownership_violations({}) == ()


def test_documented_script_invocation_can_import_the_runtime_contract() -> None:
    env = os.environ.copy()
    env.pop("BOOTSTRAP_DATABASE_URL", None)
    env.pop("MIGRATION_DATABASE_URL", None)

    result = subprocess.run(  # noqa: S603 - interpreter and script are constants
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "BOOTSTRAP_DATABASE_URL is not set" in result.stderr
    assert "ModuleNotFoundError" not in result.stderr


def test_real_bootstrap_uses_one_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    called: dict[str, object] = {}

    class _Context:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    def connect(url: str, *, autocommit: bool) -> _Context:
        called.update(url=url, autocommit=autocommit)
        return _Context()

    monkeypatch.setattr(module.psycopg, "connect", connect)
    monkeypatch.setattr(module, "bootstrap", lambda *_args, **_kwargs: 0)
    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", "postgresql://bootstrap/db")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])

    assert module.main() == 0
    assert called == {
        "url": "postgresql://bootstrap/db",
        "autocommit": False,
    }


def test_verify_only_uses_the_migration_url_and_never_bootstraps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_script()
    called: dict[str, object] = {}

    class _Context:
        def __enter__(self) -> object:
            return object()

        def __exit__(self, *args: object) -> None:
            return None

    def connect(url: str, *, autocommit: bool) -> _Context:
        called.update(url=url, autocommit=autocommit)
        return _Context()

    monkeypatch.setattr(module.psycopg, "connect", connect)
    monkeypatch.setattr(
        module,
        "verify_migration_connection",
        lambda _connection: called.update(verified=True) or 0,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "bootstrap",
        lambda *_args, **_kwargs: pytest.fail("verify-only must not mutate roles"),
    )
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "postgresql://app_admin/db")
    monkeypatch.delenv("BOOTSTRAP_DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--verify-only"])

    assert module.main() == 0
    assert called == {
        "url": "postgresql://app_admin/db",
        "autocommit": False,
        "verified": True,
    }
