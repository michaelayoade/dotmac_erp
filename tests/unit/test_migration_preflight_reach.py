"""The deploy preflight refuses a wrong database, a wrong graph, and a SET ROLE.

`verify_migration_connection` answered WHO the connection is (`current_user`),
and WHAT it owns (`MIGRATION_OWNERSHIP_SQL`). It answered neither WHERE it
landed, nor WHETHER the runtime roles can become it, nor whether the connection
AUTHENTICATED as the executor or merely `SET ROLE` into it. Every one of those
gaps is exploitable the same way: what the preflight checked was satisfiable by
a different, correctly shaped cluster, by a role that keeps clean attributes and
reaches dirty ones through membership, or by a superuser session wearing the
executor's name.

These are behavioural tests of the SCRIPT, not of the pure functions — those are
covered in `tests/unit/test_migration_authority_policies.py`, and the static
call sites in `tests/architecture/test_runtime_role_authority_contract.py`. What
is proved here is that the verifier actually issues the queries and actually
returns non-zero.
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

#: `(rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls)` as the
#: scanner reports them.
NO_ATTRIBUTES = (False, False, False, False, False)
EXECUTOR_ATTRIBUTES = (False, False, False, False, True)


def _scan_rows(
    *,
    session_user: str = "app_admin",
    current_user: str = "app_admin",
    system_user: str | None = None,
    subjects: dict[str, tuple[bool, ...]] | None = None,
    edges: tuple[tuple[str, str, bool, tuple[bool, ...]], ...] = (),
) -> list[tuple[Any, ...]]:
    """The rows `ROLE_AUTHORITY_SQL` returns, in its exact column order.

    Built here rather than canned, so a test can plant ONE fact and leave the
    rest correct — which is what makes a refusal attributable to the thing the
    test planted.
    """
    if subjects is None:
        subjects = {
            "app_admin": EXECUTOR_ATTRIBUTES,
            "app_user": NO_ATTRIBUTES,
            "platform_api": NO_ATTRIBUTES,
            "outbox_dispatcher": NO_ATTRIBUTES,
            "platform_outbox_dispatcher": NO_ATTRIBUTES,
        }
    rows: list[tuple[Any, ...]] = [
        ("session", session_user, current_user, None, None, None, None, None, None),
        ("system_user", system_user, None, None, None, None, None, None, None),
    ]
    rows += [
        ("subject", name, None, None, *attributes)
        for name, attributes in sorted(subjects.items())
    ]
    rows += [
        ("membership", subject, target, direct, *attributes)
        for subject, target, direct, attributes in edges
    ]
    return rows


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
    """Answers each of the verifier's queries by recognising it.

    Deliberately NOT a single canned result set: the whole point of the added
    checks is that they issue their own queries, and a fake that returned the
    same rows to everything would make a verifier that never asks them look
    identical to one that does.
    """

    def __init__(
        self,
        *,
        database: str = "dotmac_erp",
        roles: list[tuple[Any, ...]] | None = None,
        scan: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.database = database
        self.roles = CLEAN_ROLES if roles is None else roles
        self.scan = _scan_rows() if scan is None else scan
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
            return _Rows(list(self.scan))
        if "rolbypassrls, rolsuper FROM pg_roles" in text:
            return _Rows(list(self.roles))
        return _Rows([])  # the ownership inventory: nothing non-owned


def test_a_clean_connection_passes_and_names_what_it_did_not_check(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No expectation supplied is not silently 'fine'.

    An operator who reads exit 0 must not be able to believe the database
    identity or the authentication method was checked. Both bindings are
    optional, and both are loud about being optional.
    """
    monkeypatch.delenv("MIGRATION_EXPECTED_DATABASE", raising=False)
    monkeypatch.delenv("MIGRATION_EXPECTED_AUTHENTICATION", raising=False)
    module = _load_script()

    result = module.verify_migration_connection(_Connection())

    assert result == 0
    stderr = capsys.readouterr().err
    assert "database identity UNVERIFIED" in stderr
    assert "MIGRATION_EXPECTED_DATABASE" in stderr
    assert "authentication UNVERIFIED" in stderr
    assert "MIGRATION_EXPECTED_AUTHENTICATION" in stderr


def test_the_verifier_asks_where_it_landed_and_who_can_reach_whom() -> None:
    """Non-vacuity: the refusals must be backed by queries actually issued."""
    module = _load_script()
    connection = _Connection()

    module.verify_migration_connection(connection)

    assert any("current_database" in query for query in connection.asked)
    assert any("pg_auth_members" in query for query in connection.asked)
    assert any("SYSTEM_USER" in query for query in connection.asked), (
        "nothing asked the server who authenticated this connection"
    )


def test_the_wrong_database_is_refused_although_everything_else_is_right(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect the census named: a reconciliation aimed at the wrong
    database passed every check the script made."""
    monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", "dotmac_erp")
    monkeypatch.delenv("MIGRATION_EXPECTED_AUTHENTICATION", raising=False)
    module = _load_script()

    result = module.verify_migration_connection(_Connection(database="dotmac_erp_uat"))

    assert result == 1
    stderr = capsys.readouterr().err
    assert "dotmac_erp_uat" in stderr
    assert "authorised for 'dotmac_erp'" in stderr
    assert "database identity UNVERIFIED" not in stderr


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
        _Connection(
            scan=_scan_rows(
                edges=(("app_user", "app_admin", True, EXECUTOR_ATTRIBUTES),)
            )
        )
    )

    assert result == 1
    stderr = capsys.readouterr().err
    assert "RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP" in stderr
    assert "app_user" in stderr
    assert "BYPASSRLS" in stderr


def test_a_runtime_role_holding_createrole_on_itself_is_refused(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gap the frozen contract cannot see, driven through the real script.

    `ROLE_CONTRACT` still reports `app_user` as `(False, False)` — the `roles`
    rows below are the clean ones — so the refusal can only have come from the
    direct-posture check.
    """
    monkeypatch.delenv("MIGRATION_EXPECTED_DATABASE", raising=False)
    module = _load_script()

    result = module.verify_migration_connection(
        _Connection(
            scan=_scan_rows(
                subjects={
                    "app_admin": EXECUTOR_ATTRIBUTES,
                    "app_user": (False, True, False, False, False),
                }
            )
        )
    )

    assert result == 1
    stderr = capsys.readouterr().err
    assert "RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN" in stderr
    assert "NOCREATEROLE" in stderr


def test_a_server_program_membership_is_refused(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Privileged with NO privileged attribute — the edge the superseded
    scanner discarded before any evaluator could see it."""
    monkeypatch.delenv("MIGRATION_EXPECTED_DATABASE", raising=False)
    module = _load_script()

    result = module.verify_migration_connection(
        _Connection(
            scan=_scan_rows(
                edges=(("app_user", "pg_execute_server_program", True, NO_ATTRIBUTES),)
            )
        )
    )

    assert result == 1
    assert "pg_execute_server_program" in capsys.readouterr().err


def test_a_set_role_session_is_refused_although_current_user_is_the_executor(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`current_user` alone cannot tell this connection from an honest one.

    `migration_executor_violations` reads `current_user` and is satisfied here —
    which is exactly why it is not enough on its own.
    """
    monkeypatch.delenv("MIGRATION_EXPECTED_DATABASE", raising=False)
    module = _load_script()

    result = module.verify_migration_connection(
        _Connection(scan=_scan_rows(session_user="postgres"))
    )

    assert result == 1
    stderr = capsys.readouterr().err
    assert "MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH" in stderr
    assert "postgres" in stderr


def test_a_bound_authentication_expectation_is_asserted(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound and satisfied passes; bound and unmet refuses. Both, because a
    check that only ever refuses is not a check."""
    monkeypatch.delenv("MIGRATION_EXPECTED_DATABASE", raising=False)
    monkeypatch.setenv("MIGRATION_EXPECTED_AUTHENTICATION", "scram-sha-256:app_admin")
    module = _load_script()

    satisfied = _Connection(scan=_scan_rows(system_user="scram-sha-256:app_admin"))
    assert module.verify_migration_connection(satisfied) == 0
    assert "authentication UNVERIFIED" not in capsys.readouterr().err

    # NULL system_user is what PostgreSQL returns under trust authentication.
    assert module.verify_migration_connection(_Connection()) == 1
    assert "MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH" in capsys.readouterr().err


def test_a_malformed_authentication_expectation_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator who set the variable asked for an assertion. Silently turning
    a typo into 'unbound' is how a gate becomes decorative."""
    monkeypatch.setenv("MIGRATION_EXPECTED_AUTHENTICATION", "app_admin")
    module = _load_script()

    with pytest.raises(RuntimeError):
        module.verify_migration_connection(_Connection())


def test_an_empty_expectation_reads_as_absent(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`-e MIGRATION_EXPECTED_DATABASE` with nothing in the operator's shell
    arrives as an empty string, not as an absent variable. It must not become
    an expectation of a database named ''."""
    monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", "   ")
    monkeypatch.setenv("MIGRATION_EXPECTED_AUTHENTICATION", "   ")
    module = _load_script()

    assert module.verify_migration_connection(_Connection()) == 0
    stderr = capsys.readouterr().err
    assert "database identity UNVERIFIED" in stderr
    assert "authentication UNVERIFIED" in stderr


def test_the_bootstrap_refuses_to_report_success_over_a_dirty_graph(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Execution point 1, driven through the real function.

    The roles are all present and correctly shaped, so every `adopted:` line is
    printed and the frozen contract is satisfied — and the run still returns
    non-zero, because the graph it just re-read is not.
    """
    module = _load_script()

    connection = _Connection(
        scan=_scan_rows(
            session_user="postgres",
            current_user="postgres",
            edges=(("app_user", "app_admin", True, EXECUTOR_ATTRIBUTES),),
        )
    )
    result = module.bootstrap(connection, dry_run=False, repair=False)

    captured = capsys.readouterr()
    assert "adopted: app_user" in captured.out
    assert result == 1
    assert "RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP" in captured.err
    assert "does not report success" in captured.err


def test_the_bootstrap_does_not_claim_to_be_the_executor(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Near miss for the case above, and the point of
    `without_direct_authentication()`.

    The connection is `postgres`, which is exactly what the elevated bootstrap
    is. If the post-bootstrap check asserted direct authentication, this clean
    graph would refuse — and the bootstrap could never succeed at all.
    """
    module = _load_script()

    connection = _Connection(
        scan=_scan_rows(session_user="postgres", current_user="postgres")
    )
    assert module.bootstrap(connection, dry_run=False, repair=False) == 0
    assert "AUTHORITY" not in capsys.readouterr().err
