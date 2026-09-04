"""The deploy preflight refuses a wrong database and an escalating role graph.

`verify_migration_connection` answered WHO the connection is (`current_user`),
and WHAT it owns (`MIGRATION_OWNERSHIP_SQL`). It answered neither WHERE it
landed nor WHETHER the runtime roles can become it. Both gaps are exploitable in
the same way: everything the preflight checked was satisfiable by a different,
correctly shaped cluster, or by a role that keeps clean attributes and reaches
dirty ones through membership.

These are behavioural tests of the script, not of the pure functions — the pure
functions are covered in `tests/architecture/test_runtime_role_escalation_
contract.py`. What is proved here is that the verifier actually issues the
queries and actually returns non-zero.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "bootstrap_database_roles.py"

CLEAN_ROLES = [
    ("app_admin", True, False),
    ("app_user", False, False),
    ("platform_api", False, False),
    ("outbox_dispatcher", False, False),
    ("platform_outbox_dispatcher", False, False),
]


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("database_role_preflight", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None


class _Connection:
    """Answers each of the verifier's five queries by recognising it.

    Deliberately NOT a single canned result set: the whole point of the two new
    checks is that they issue their own queries, and a fake that returns the
    same rows to everything would make a verifier that never asks them look
    identical to one that does.
    """

    def __init__(
        self,
        *,
        database: str = "dotmac_erp",
        roles: list[tuple[Any, ...]] | None = None,
        escalation: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.database = database
        self.roles = CLEAN_ROLES if roles is None else roles
        self.escalation = escalation or []
        self.asked: list[str] = []

    def execute(self, statement: object, params: object = None) -> _Rows:
        text = str(statement)
        self.asked.append(text)
        # Matched EXACTLY for the two scalar reads. `MIGRATION_OWNERSHIP_SQL`
        # mentions `current_user` a dozen times in its predicates; a substring
        # test would hand the ownership inventory a one-column row and this
        # fake would fail for a reason that has nothing to do with the property
        # under test.
        if text == "SELECT current_user":
            return _Rows([("app_admin",)])
        if text == "SELECT current_database()":
            return _Rows([(self.database,)])
        if "pg_auth_members" in text:
            return _Rows(list(self.escalation))
        if "rolbypassrls, rolsuper FROM pg_roles" in text:
            return _Rows(list(self.roles))
        return _Rows([])  # the ownership inventory: nothing non-owned


def test_a_clean_connection_passes_and_says_the_database_is_unverified(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No expectation supplied is not silently 'fine'.

    An operator who reads exit 0 must not be able to believe the database
    identity was checked. Optional, and loud about being optional.
    """
    monkeypatch.delenv("MIGRATION_EXPECTED_DATABASE", raising=False)
    module = _load_script()

    result = module.verify_migration_connection(_Connection())

    assert result == 0
    stderr = capsys.readouterr().err
    assert "database identity UNVERIFIED" in stderr
    assert "MIGRATION_EXPECTED_DATABASE" in stderr


def test_the_verifier_asks_where_it_landed_and_who_can_reach_whom() -> None:
    """Non-vacuity: the two new refusals must be backed by two real queries."""
    module = _load_script()
    connection = _Connection()

    module.verify_migration_connection(connection)

    assert any("current_database" in query for query in connection.asked)
    assert any("pg_auth_members" in query for query in connection.asked)


def test_the_wrong_database_is_refused_although_everything_else_is_right(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect the census named: a reconciliation aimed at the wrong
    database passed every check the script made."""
    monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", "dotmac_erp")
    module = _load_script()

    result = module.verify_migration_connection(_Connection(database="dotmac_erp_uat"))

    assert result == 1
    stderr = capsys.readouterr().err
    assert "dotmac_erp_uat" in stderr
    assert "authorised for 'dotmac_erp'" in stderr
    assert "UNVERIFIED" not in stderr


def test_the_right_database_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Near miss: a matching expectation must not refuse."""
    monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", "dotmac_erp")
    module = _load_script()

    assert module.verify_migration_connection(_Connection(database="dotmac_erp")) == 0


def test_a_runtime_role_that_can_become_the_executor_is_refused(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Role attributes stay clean; the membership is the defect."""
    monkeypatch.delenv("MIGRATION_EXPECTED_DATABASE", raising=False)
    module = _load_script()

    result = module.verify_migration_connection(
        _Connection(escalation=[("app_user", "app_admin", False, False, True)])
    )

    assert result == 1
    stderr = capsys.readouterr().err
    assert "app_user" in stderr
    assert "BYPASSRLS" in stderr


def test_an_empty_expectation_reads_as_absent(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`-e MIGRATION_EXPECTED_DATABASE` with nothing in the operator's shell
    arrives as an empty string, not as an absent variable. It must not become
    an expectation of a database named ''."""
    monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", "   ")
    module = _load_script()

    assert module.verify_migration_connection(_Connection()) == 0
    assert "UNVERIFIED" in capsys.readouterr().err
