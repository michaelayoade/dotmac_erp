"""Both authority policies, measured against a real PostgreSQL role graph.

Was `test_runtime_role_escalation.py`, which asked one question — can a runtime
role BECOME privileged. It now asks two, because `app_admin` has its own
authority class with a deliberately different answer, and renaming it is the
honest thing to do: the old name no longer describes what is tested.

`tests/architecture/test_runtime_role_authority_contract.py` is the static
half. This module opens a database whose roles were created by the real
`scripts/bootstrap_database_roles.py` (CI's "Bootstrap database roles" step,
run exactly as an operator would) and asks the graph itself.

Four things are measured here that static text cannot establish:

1. **The query runs, and returns no violation on a correct graph.** A SQL string
   that does not parse would pass every static check in the sibling module.
2. **The subjects EXIST.** "No violations" is trivially true for a role that is
   not in the cluster, which is how this check would silently become vacuous if
   a role were renamed.
3. **A NOSUPERUSER connection can see the answer.** The whole reason
   `pg_has_role` was rejected is that the deploy preflight connects as
   `app_admin`, which is NOSUPERUSER by contract. That claim is measured by
   dropping into the role and re-running, not taken on trust.
4. **The three server file/program roles really hold no privileged attribute.**
   That is the premise that made them invisible to the superseded scanner, and
   it is read out of the live catalogue rather than asserted from memory.

Role DDL — `GRANT`, `CREATE ROLE` and `ALTER ROLE` alike — is transactional in
PostgreSQL, so each planted defect is applied, observed and rolled back inside
one transaction. Nothing survives, and nothing here is visible to another test.
That is why this module can plant defects the Alembic-driven proofs in
`test_migration_authority_graph.py` must instead revoke in a `finally`: those
drive a SEPARATE connection, which cannot see an uncommitted grant.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.migration_authority import (
    AUTHORITY_SUBJECTS,
    MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN,
    MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP,
    RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN,
    RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP,
    ROLE_AUTHORITY_SQL,
    SERVER_FILE_OR_PROGRAM_ROLES,
    MigrationExecutorAuthorityPolicyV1,
    RoleAuthorityObservationV1,
    RuntimeRoleAuthorityPolicyV1,
    observation_from_rows,
    role_authority_violations,
)
from app.migration_database_roles import MIGRATION_EXECUTOR

pytestmark = pytest.mark.integration


def _observe(connection: Connection) -> RoleAuthorityObservationV1:
    """`exec_driver_sql`, so this test runs the SAME BYTES the callers run.

    The superseded version of this file rewrote `%(subjects)s` into `:subjects`
    so SQLAlchemy's `text()` would accept it. That rewrite is exactly the drift
    the one-scanner rule exists to prevent: it made the test's query a THIRD
    spelling, and a defect introduced in the pyformat original could have been
    corrected by the rewrite without anyone noticing.
    """
    rows = connection.exec_driver_sql(
        ROLE_AUTHORITY_SQL, {"subjects": sorted(AUTHORITY_SUBJECTS)}
    ).all()
    return observation_from_rows(rows)


def _violations(connection: Connection) -> tuple[str, ...]:
    """Both policies, over one observation — as every real caller evaluates.

    The executor policy is stripped of its authentication clause: this
    connection is the test harness's, not a freshly authenticated `app_admin`,
    and the question here is about the GRAPH.
    """
    observation = _observe(connection)
    return tuple(
        f"{item.code} {item.message}"
        for item in (
            *role_authority_violations(RuntimeRoleAuthorityPolicyV1, observation),
            *role_authority_violations(
                MigrationExecutorAuthorityPolicyV1.without_direct_authentication(),
                observation,
            ),
        )
    )


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
    """Every subject of EITHER policy, derived — `app_admin` included now.

    It was excluded before because it was not a subject of anything. It is the
    migration executor policy's only subject today, so a cluster missing it
    would make that entire policy silently answer "nothing to report".
    """
    present = {
        str(row[0])
        for row in role_graph.execute(
            text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
            {"names": sorted(AUTHORITY_SUBJECTS)},
        ).all()
    }
    missing = set(AUTHORITY_SUBJECTS) - present
    assert missing == set(), (
        f"{sorted(missing)} are not in this cluster, so 'no violations' says "
        "nothing about them — the bootstrap did not run, or a role was renamed "
        "without updating the policy declarations"
    )


def test_a_correctly_bootstrapped_cluster_satisfies_both_policies(
    role_graph: Connection,
) -> None:
    """The near-miss for every planted defect below, and a parse check for the
    scanner: a query that did not compile would pass the static module."""
    violations = _violations(role_graph)
    assert violations == (), "\n".join(violations)


def test_the_migration_executor_is_reachable_by_no_runtime_role(
    role_graph: Connection,
) -> None:
    """Stated separately from the general check because it is THE edge that
    would undo removing the DSN from app, worker and beat."""
    observation = _observe(role_graph)
    reached = {
        (edge.subject, edge.target)
        for edge in observation.membership_edges
        if edge.subject in RuntimeRoleAuthorityPolicyV1.subjects
    }
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

    violations = _violations(role_graph)

    assert any(
        RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP in violation
        and "app_user" in violation
        and MIGRATION_EXECUTOR in violation
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
    role_graph.execute(text("CREATE ROLE authority_probe_middle NOLOGIN"))
    role_graph.execute(text(f'GRANT "{MIGRATION_EXECUTOR}" TO authority_probe_middle'))
    role_graph.execute(text("GRANT authority_probe_middle TO platform_api"))

    violations = _violations(role_graph)

    assert any(
        "platform_api" in violation and MIGRATION_EXECUTOR in violation
        for violation in violations
    ), violations


def test_a_createrole_held_directly_is_named(role_graph: Connection) -> None:
    """The gap no membership walk can close: a role is not a member of itself.

    `ROLE_CONTRACT` reads `(rolbypassrls, rolsuper)`, which this `ALTER ROLE`
    leaves untouched — asserted below so the finding is attributable to the new
    direct-posture check rather than to the older contract.
    """
    role_graph.execute(text("ALTER ROLE app_user CREATEROLE"))

    violations = _violations(role_graph)

    assert any(
        RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN in violation
        and "app_user" in violation
        and "NOCREATEROLE" in violation
        for violation in violations
    ), violations

    posture = role_graph.execute(
        text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname='app_user'")
    ).one()
    assert posture == (False, False), (
        "the frozen contract's two attributes are unchanged, so nothing older "
        "than this policy could have produced the finding above"
    )


def test_the_executor_holding_createrole_is_named(role_graph: Connection) -> None:
    """`app_admin` may hold BYPASSRLS and nothing else. `ROLE_CONTRACT` says
    nothing about CREATEROLE for it either."""
    role_graph.execute(text(f'ALTER ROLE "{MIGRATION_EXECUTOR}" CREATEROLE'))

    violations = _violations(role_graph)

    assert any(
        MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN in violation
        and "NOCREATEROLE" in violation
        for violation in violations
    ), violations


@pytest.mark.parametrize("predefined", sorted(SERVER_FILE_OR_PROGRAM_ROLES))
def test_a_server_file_or_program_membership_is_named(
    predefined: str, role_graph: Connection
) -> None:
    """Privileged with NO privileged attribute — read from the live catalogue.

    The premise is asserted first. If PostgreSQL ever gave one of these an
    attribute, this test would keep passing while no longer proving the gap it
    was written for.
    """
    held = role_graph.execute(
        text(
            "SELECT rolsuper OR rolcreaterole OR rolbypassrls FROM pg_roles "
            "WHERE rolname = :name"
        ),
        {"name": predefined},
    ).scalar_one()
    assert held is False, (
        f"{predefined} now holds a privileged ATTRIBUTE, so the superseded "
        "scanner would have seen it and this test no longer proves the gap"
    )

    role_graph.execute(text(f'GRANT "{predefined}" TO app_user'))
    assert any(
        RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP in violation and predefined in violation
        for violation in _violations(role_graph)
    )

    role_graph.execute(text(f'GRANT "{predefined}" TO "{MIGRATION_EXECUTOR}"'))
    assert any(
        MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP in violation
        and predefined in violation
        for violation in _violations(role_graph)
    )


def test_the_executor_may_reach_another_bypassrls_role(
    role_graph: Connection,
) -> None:
    """The ASYMMETRY, measured — and the near-miss for the executor cases above.

    Same edge, two subjects, two correct and opposite verdicts. If the executor
    policy refused every membership, its refusals above would prove nothing
    about what they claim to detect.
    """
    role_graph.execute(text("CREATE ROLE authority_probe_reader NOLOGIN BYPASSRLS"))
    role_graph.execute(text(f'GRANT authority_probe_reader TO "{MIGRATION_EXECUTOR}"'))
    assert _violations(role_graph) == ()

    role_graph.execute(text("GRANT authority_probe_reader TO app_user"))
    assert any(
        RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP in violation
        for violation in _violations(role_graph)
    ), "the same edge must be refused for a role that does not hold BYPASSRLS"


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
            "CREATE ROLE authority_probe_plain NOLOGIN NOSUPERUSER "
            "NOCREATEROLE NOBYPASSRLS"
        )
    )
    role_graph.execute(text("GRANT authority_probe_plain TO app_user"))

    assert _violations(role_graph) == ()


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

    assert any("app_user" in violation for violation in _violations(role_graph))
