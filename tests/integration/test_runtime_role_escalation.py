"""The escalation contract, measured against a real PostgreSQL role graph.

`tests/architecture/test_runtime_role_escalation_contract.py` is static: it
checks that the contract is stated, that the query reads readable catalogues,
and that the violation function can fail. Every one of its assertions is about
source text. This module opens a database whose roles were created by the real
`scripts/bootstrap_database_roles.py` (CI's "Bootstrap database roles" step,
run exactly as an operator would) and asks the graph itself.

Three things are measured here that static text cannot establish:

1. **The query runs, and returns nothing on a correct graph.** A SQL string that
   does not parse would pass every static check in the sibling module.
2. **The subjects EXIST.** "No escalation edges" is trivially true for a role
   that is not in the cluster, which is how this check would silently become
   vacuous if a role were renamed.
3. **A NOSUPERUSER connection can see the answer.** The whole reason
   `pg_has_role` was rejected is that the deploy preflight connects as
   `app_admin`, which is NOSUPERUSER by contract. That claim is measured by
   dropping into the role and re-running, not taken on trust.

Role membership DDL is transactional in PostgreSQL, so each planted defect is
granted, observed and rolled back inside one transaction. Nothing survives.
"""

from __future__ import annotations

import pytest
from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.engine import Connection, Engine

from app.migration_database_roles import (
    MIGRATION_EXECUTOR,
    NON_ESCALATING_ROLES,
    ROLE_ESCALATION_SQL,
    role_escalation_violations,
)

pytestmark = pytest.mark.integration

#: `ROLE_ESCALATION_SQL` is written for psycopg's `%(name)s` placeholders, which
#: is what `scripts/bootstrap_database_roles.py` executes it with. SQLAlchemy's
#: `text()` uses `:name`. One rewrite, in one place, so the two callers cannot
#: drift into two different queries.
_SQLALCHEMY_SQL = ROLE_ESCALATION_SQL.replace("%(subjects)s", ":subjects")


#: The bind is typed EXPLICITLY as `text[]`. `rolname = ANY(:subjects)` needs a
#: PostgreSQL array in one parameter; leaving SQLAlchemy to infer that from a
#: Python list is the kind of thing that works until a driver changes.
_ESCALATION_QUERY = text(_SQLALCHEMY_SQL).bindparams(
    bindparam("subjects", type_=ARRAY(String))
)


def _edges(connection: Connection) -> list[tuple[str, str, bool, bool, bool]]:
    rows = connection.execute(
        _ESCALATION_QUERY, {"subjects": sorted(NON_ESCALATING_ROLES)}
    ).all()
    return [(str(r[0]), str(r[1]), bool(r[2]), bool(r[3]), bool(r[4])) for r in rows]


@pytest.fixture()
def role_graph(engine: Engine):
    """A connection whose role DDL is discarded when the test ends."""
    connection = engine.connect()
    transaction = connection.begin()
    try:
        yield connection
    finally:
        transaction.rollback()
        connection.close()


def test_the_subjects_exist_so_a_clean_result_is_not_vacuous(
    role_graph: Connection,
) -> None:
    present = {
        str(row[0])
        for row in role_graph.execute(
            text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
            {"names": sorted(NON_ESCALATING_ROLES | {MIGRATION_EXECUTOR})},
        ).all()
    }
    missing = (NON_ESCALATING_ROLES | {MIGRATION_EXECUTOR}) - present
    assert missing == set(), (
        f"{sorted(missing)} are not in this cluster, so 'no escalation edges' "
        "says nothing about them — the bootstrap did not run, or a role was "
        "renamed without updating NON_ESCALATING_ROLES"
    )


def test_no_runtime_role_can_reach_a_privileged_one(role_graph: Connection) -> None:
    violations = role_escalation_violations(_edges(role_graph))
    assert violations == (), "\n".join(violations)


def test_the_migration_executor_is_reachable_by_nobody_at_runtime(
    role_graph: Connection,
) -> None:
    """Stated separately from the general check because it is THE edge that
    would undo removing the DSN from app, worker and beat."""
    reached = {(subject, target) for subject, target, *_ in _edges(role_graph)}
    assert not any(target == MIGRATION_EXECUTOR for _, target in reached), reached


def test_a_granted_membership_in_the_executor_is_named(
    role_graph: Connection,
) -> None:
    """Planted defect, against the live catalogue.

    This is the escalation that survives every other control in this
    repository: `app_user` keeps its own NOBYPASSRLS/NOSUPERUSER attributes, so
    `ROLE_CONTRACT` and `runtime_admission` both still pass, while `SET ROLE
    app_admin` hands it BYPASSRLS.
    """
    role_graph.execute(text(f'GRANT "{MIGRATION_EXECUTOR}" TO app_user'))

    violations = role_escalation_violations(_edges(role_graph))

    assert any(
        "app_user" in violation and MIGRATION_EXECUTOR in violation
        for violation in violations
    ), violations
    assert any("BYPASSRLS" in violation for violation in violations), violations

    posture = role_graph.execute(
        text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname='app_user'")
    ).one()
    assert posture == (False, False), (
        "app_user's own attributes are unchanged by the grant, which is exactly "
        "why ROLE_CONTRACT cannot see this and why this check exists"
    )


def test_a_two_hop_grant_is_named(role_graph: Connection) -> None:
    """Planted defect one hop further out; a non-recursive walk would miss it."""
    role_graph.execute(text("CREATE ROLE escalation_probe_middle NOLOGIN"))
    role_graph.execute(text(f'GRANT "{MIGRATION_EXECUTOR}" TO escalation_probe_middle'))
    role_graph.execute(text("GRANT escalation_probe_middle TO platform_api"))

    violations = role_escalation_violations(_edges(role_graph))

    assert any(
        "platform_api" in violation and MIGRATION_EXECUTOR in violation
        for violation in violations
    ), violations


def test_an_unprivileged_group_membership_is_not_named(
    role_graph: Connection,
) -> None:
    """Near miss: ordinary group membership must stay quiet.

    A detector that fired here would fire on `GRANT pg_monitor TO
    claude_readonly` (scripts/setup_pg_observability.sql) and on every future
    reporting group, and would be switched off rather than obeyed.
    """
    role_graph.execute(
        text(
            "CREATE ROLE escalation_probe_plain NOLOGIN NOSUPERUSER "
            "NOCREATEROLE NOBYPASSRLS"
        )
    )
    role_graph.execute(text("GRANT escalation_probe_plain TO app_user"))

    assert role_escalation_violations(_edges(role_graph)) == ()


def test_a_nosuperuser_connection_can_see_the_same_answer(
    role_graph: Connection,
) -> None:
    """The premise behind rejecting `pg_has_role`, measured.

    The deploy preflight runs this query as `app_admin`. If the catalogues were
    not readable there, the preflight would report a clean graph because it was
    refused — a false clean. Planting the defect first means the role's read is
    proved to return DATA, not merely to succeed on an empty set.
    """
    role_graph.execute(text(f'GRANT "{MIGRATION_EXECUTOR}" TO app_user'))
    role_graph.execute(text(f'SET LOCAL ROLE "{MIGRATION_EXECUTOR}"'))

    current_user = role_graph.execute(text("SELECT current_user")).scalar_one()
    assert current_user == MIGRATION_EXECUTOR

    superuser = role_graph.execute(
        text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
    ).scalar_one()
    assert superuser is False, (
        "this proof is only meaningful from a NOSUPERUSER connection"
    )

    violations = role_escalation_violations(_edges(role_graph))
    assert any("app_user" in violation for violation in violations), violations
