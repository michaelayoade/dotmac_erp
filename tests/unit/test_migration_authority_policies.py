"""The two authority policies disagree, on purpose, over one observation.

These exercise the PURE evaluator. No database, no environment, no clock — if
a verdict here needed any of those, the evaluator would not be extractable and
the same rules could not later be applied to a snapshot taken hours earlier.

The real-PostgreSQL proofs live in
`tests/integration/test_migration_authority_graph.py`; these fix the SEMANTICS
those proofs then confirm the catalogue really produces.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from app.migration_authority import (
    MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH,
    MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN,
    MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP,
    RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN,
    RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP,
    AuthenticationExpectationV1,
    AuthorityViolationV1,
    MembershipEdgeV1,
    MigrationExecutorAuthorityPolicyV1,
    RoleAttributesV1,
    RoleAuthorityObservationV1,
    RuntimeRoleAuthorityPolicyV1,
    observation_from_rows,
    parse_authentication_expectation,
    role_authority_violations,
)

CLEAN = RoleAttributesV1(
    superuser=False,
    createrole=False,
    createdb=False,
    replication=False,
    bypassrls=False,
)
EXECUTOR = RoleAttributesV1(
    superuser=False,
    createrole=False,
    createdb=False,
    replication=False,
    bypassrls=True,
)


def _observation(
    *,
    session_user: str = "app_admin",
    current_user: str = "app_admin",
    system_user: str | None = None,
    subjects: dict[str, RoleAttributesV1] | None = None,
    edges: tuple[MembershipEdgeV1, ...] = (),
) -> RoleAuthorityObservationV1:
    return RoleAuthorityObservationV1(
        session_user=session_user,
        current_user=current_user,
        system_user=system_user,
        subject_attributes=subjects
        if subjects is not None
        else {
            "app_admin": EXECUTOR,
            "app_user": CLEAN,
            "platform_api": CLEAN,
            "outbox_dispatcher": CLEAN,
            "platform_outbox_dispatcher": CLEAN,
        },
        membership_edges=edges,
    )


def _codes(violations: Sequence[AuthorityViolationV1]) -> list[str]:
    return [item.code for item in violations]


# ── the near-miss that makes every refusal below attributable ───────────────


def test_a_clean_cluster_satisfies_both_policies() -> None:
    """Without this, an evaluator that refused everything would pass the rest."""
    observation = _observation()
    assert role_authority_violations(RuntimeRoleAuthorityPolicyV1, observation) == ()
    assert (
        role_authority_violations(MigrationExecutorAuthorityPolicyV1, observation) == ()
    )


# ── the asymmetry that is the whole reason there are two policies ───────────


def test_a_bypassrls_only_membership_refuses_for_runtime_and_admits_for_the_executor() -> (
    None
):
    """One membership, two correct and OPPOSITE verdicts.

    `app_admin` holds BYPASSRLS by contract, so reaching another role that also
    holds it gains it nothing — refusing would refuse a correctly bootstrapped
    cluster. `app_user` holds none of it, so the same edge is one `SET ROLE`
    from bypassing every row-level security policy ERP has.

    This is exactly what a single policy with `app_admin` added to the subject
    set could not express, and is why that shortcut was rejected.
    """
    bystander = MembershipEdgeV1(
        subject="app_user",
        target="some_bypassrls_role",
        direct=True,
        target_attributes=EXECUTOR,
    )
    runtime = role_authority_violations(
        RuntimeRoleAuthorityPolicyV1, _observation(edges=(bystander,))
    )
    assert _codes(runtime) == [RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP]

    executor_edge = MembershipEdgeV1(
        subject="app_admin",
        target="some_bypassrls_role",
        direct=True,
        target_attributes=EXECUTOR,
    )
    executor = role_authority_violations(
        MigrationExecutorAuthorityPolicyV1, _observation(edges=(executor_edge,))
    )
    assert executor == (), (
        "a membership reaching only another BYPASSRLS role is not an escalation "
        "for a subject that already holds BYPASSRLS"
    )


def test_the_executor_still_refuses_superuser_and_createrole_closure() -> None:
    """The near-miss above must not have disarmed the executor policy."""
    for attributes in (
        RoleAttributesV1(True, False, False, False, False),
        RoleAttributesV1(False, True, False, False, False),
    ):
        edge = MembershipEdgeV1(
            subject="app_admin",
            target="reachable",
            direct=False,
            target_attributes=attributes,
        )
        violations = role_authority_violations(
            MigrationExecutorAuthorityPolicyV1, _observation(edges=(edge,))
        )
        assert _codes(violations) == [MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP]


# ── gap 1: an attribute held DIRECTLY, which the walk can never see ─────────


def test_a_runtime_role_holding_createrole_on_itself_refuses() -> None:
    """`ROLE_CONTRACT` reads `(rolbypassrls, rolsuper)` and cannot see this.

    The membership walk cannot see it either: a role is not a member of itself,
    so there is no edge to find. Before the direct posture check existed, this
    observation passed every gate ERP had.
    """
    observation = _observation(
        subjects={
            "app_user": RoleAttributesV1(False, True, False, False, False),
            "platform_api": CLEAN,
            "outbox_dispatcher": CLEAN,
            "platform_outbox_dispatcher": CLEAN,
        }
    )
    violations = role_authority_violations(RuntimeRoleAuthorityPolicyV1, observation)
    assert _codes(violations) == [RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN]
    assert "app_user" in violations[0].message
    assert "NOCREATEROLE" in violations[0].message


def test_the_executor_must_hold_bypassrls_and_nothing_else() -> None:
    """Both directions of the executor's direct posture, in one observation.

    A missing REQUIRED attribute and a held FORBIDDEN one both use
    `MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN`, because the failure-code
    set is closed. The message carries the direction.
    """
    observation = _observation(
        subjects={"app_admin": RoleAttributesV1(False, True, True, True, False)}
    )
    violations = role_authority_violations(
        MigrationExecutorAuthorityPolicyV1, observation
    )
    assert set(_codes(violations)) == {MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN}
    # The observed posture is echoed in full in every message, so asserting a
    # bare substring would pass on any of them. Read the REQUIRED spelling out
    # of the sentence instead, which is the part that differs per finding.
    required = {
        item.message.split("this authority class requires ", 1)[1].split(".", 1)[0]
        for item in violations
    }
    assert required == {"NOCREATEROLE", "NOCREATEDB", "NOREPLICATION", "BYPASSRLS"}


# ── gap 2: privileged WITHOUT a privileged attribute ────────────────────────


@pytest.mark.parametrize(
    "target",
    ["pg_read_server_files", "pg_write_server_files", "pg_execute_server_program"],
)
def test_a_server_file_or_program_role_refuses_for_both_policies(target: str) -> None:
    """These hold none of SUPERUSER/CREATEROLE/BYPASSRLS.

    The old scanner's `WHERE target_role.rolsuper OR rolcreaterole OR
    rolbypassrls` discarded them before any evaluator ran, which is why naming
    them in a policy was not, by itself, enough.
    """
    for subject, policy, code in (
        (
            "app_user",
            RuntimeRoleAuthorityPolicyV1,
            RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP,
        ),
        (
            "app_admin",
            MigrationExecutorAuthorityPolicyV1,
            MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP,
        ),
    ):
        edge = MembershipEdgeV1(
            subject=subject, target=target, direct=True, target_attributes=CLEAN
        )
        violations = role_authority_violations(policy, _observation(edges=(edge,)))
        assert _codes(violations) == [code]
        assert target in violations[0].message


def test_an_ordinary_unprivileged_membership_is_not_a_violation() -> None:
    """The sensitivity proof for the case above.

    If membership alone were refused, the server-file test would pass with
    nothing specific to those three roles having been detected.
    """
    edge = MembershipEdgeV1(
        subject="app_user",
        target="some_reporting_group",
        direct=True,
        target_attributes=CLEAN,
    )
    assert (
        role_authority_violations(
            RuntimeRoleAuthorityPolicyV1, _observation(edges=(edge,))
        )
        == ()
    )


# ── direct authentication ───────────────────────────────────────────────────


def test_set_role_into_the_executor_is_not_authentication_as_it() -> None:
    observation = _observation(session_user="postgres", current_user="app_admin")
    violations = role_authority_violations(
        MigrationExecutorAuthorityPolicyV1, observation
    )
    assert MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH in _codes(violations)
    assert "postgres" in violations[0].message


def test_a_bound_authentication_method_is_asserted_and_trust_cannot_satisfy_it() -> (
    None
):
    """`system_user` is NULL under trust, so trust can never supply this proof."""
    policy = MigrationExecutorAuthorityPolicyV1.binding_authentication(
        AuthenticationExpectationV1(method="scram-sha-256", identity="app_admin")
    )
    assert (
        role_authority_violations(
            policy, _observation(system_user="scram-sha-256:app_admin")
        )
        == ()
    )
    under_trust = role_authority_violations(policy, _observation(system_user=None))
    assert _codes(under_trust) == [MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH]
    assert "trust" in under_trust[0].message

    wrong_method = role_authority_violations(
        policy, _observation(system_user="md5:app_admin")
    )
    assert _codes(wrong_method) == [MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH]


def test_the_runtime_policy_makes_no_claim_about_who_is_connected() -> None:
    """Runtime roles are OBSERVED, never the connection. A `postgres` bootstrap
    session reading the graph must not be reported as a runtime-role failure."""
    observation = _observation(session_user="postgres", current_user="postgres")
    assert role_authority_violations(RuntimeRoleAuthorityPolicyV1, observation) == ()


def test_the_elevated_bootstrap_may_inspect_but_not_claim_the_executor() -> None:
    policy = MigrationExecutorAuthorityPolicyV1.without_direct_authentication()
    observation = _observation(session_user="postgres", current_user="postgres")
    assert role_authority_violations(policy, observation) == ()

    still_checks_attributes = role_authority_violations(
        policy,
        _observation(
            session_user="postgres",
            current_user="postgres",
            subjects={"app_admin": RoleAttributesV1(True, False, False, False, True)},
        ),
    )
    assert _codes(still_checks_attributes) == [
        MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN
    ], "dropping the authentication clause must not drop the posture check"


def test_a_malformed_authentication_expectation_raises_rather_than_unbinding() -> None:
    assert parse_authentication_expectation(None) is None
    assert parse_authentication_expectation("  ") is None
    assert parse_authentication_expectation(" scram-sha-256 : app_admin ") == (
        AuthenticationExpectationV1(method="scram-sha-256", identity="app_admin")
    )
    with pytest.raises(RuntimeError):
        parse_authentication_expectation("app_admin")


# ── the observation reader ──────────────────────────────────────────────────


def test_the_reader_turns_scanner_rows_into_the_shared_observation() -> None:
    rows = [
        ("session", "app_admin", "app_admin", None, None, None, None, None, None),
        ("system_user", None, None, None, None, None, None, None, None),
        ("subject", "app_admin", None, None, False, False, False, False, True),
        (
            "membership",
            "app_user",
            "pg_execute_server_program",
            True,
            False,
            False,
            False,
            False,
            False,
        ),
    ]
    observation = observation_from_rows(rows)
    assert observation.session_user == "app_admin"
    assert observation.system_user is None
    assert observation.subject_attributes["app_admin"].bypassrls is True
    assert observation.membership_edges[0].target == "pg_execute_server_program"
    assert observation.membership_edges[0].direct is True


def test_a_scan_with_no_session_row_raises_rather_than_inventing_one() -> None:
    with pytest.raises(RuntimeError):
        observation_from_rows(
            [("subject", "app_admin", None, None, False, False, False, False, True)]
        )
