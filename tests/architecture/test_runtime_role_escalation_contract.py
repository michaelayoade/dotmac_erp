"""A runtime role may not become a privileged one.

Removing `MIGRATION_DATABASE_URL` from `app`, `worker` and `beat` is worth
exactly nothing if the credential those services still hold can BECOME the one
that was taken away. PostgreSQL role attributes are not inherited through
membership — a member of a superuser role is not itself `rolsuper` — but
`SET ROLE` adopts the target's attributes for the session, and membership is
the only thing `SET ROLE` requires. So `GRANT app_admin TO app_user` reinstates
BYPASSRLS for `app_user` in one statement, invisibly to `ROLE_CONTRACT`, which
only reads each role's OWN `(rolbypassrls, rolsuper)`.

This module is the STATIC half: the contract is stated once, the observation
uses catalogues the preflight connection can actually read, the preflight wires
it, and the violation function can fail. The half that measures a real role
graph is `tests/integration/test_runtime_role_escalation.py`; neither
substitutes for the other, because a contract nothing observes is a claim and
an observation with no contract is a snapshot.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.migration_database_roles import (
    ESCALATING_ATTRIBUTES,
    NON_ESCALATING_ROLES,
    RELAY_DISPATCHER_CONTRACT,
    ROLE_CONTRACT,
    ROLE_ESCALATION_SQL,
    role_escalation_violations,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = REPO_ROOT / "scripts" / "bootstrap_database_roles.py"


def test_every_online_identity_is_a_subject() -> None:
    """The subject set is derived from the two role contracts, not hand-listed.

    `app_admin` is the one deliberate exclusion: it is BYPASSRLS by contract, so
    "can it reach BYPASSRLS" is not a question about it. Every OTHER role either
    contract names is an identity a long-running process connects as, and
    each must be a subject — otherwise a role can be added to a contract and
    silently escape this check.
    """
    online = (set(ROLE_CONTRACT) | set(RELAY_DISPATCHER_CONTRACT)) - {"app_admin"}
    assert online == NON_ESCALATING_ROLES, (
        "a role joined an online contract without joining the escalation "
        "subjects, or vice versa"
    )


def test_the_named_attributes_are_the_ones_the_ruling_names() -> None:
    assert ESCALATING_ATTRIBUTES == ("SUPERUSER", "CREATEROLE", "BYPASSRLS")


def test_the_observation_uses_catalogues_the_preflight_can_read() -> None:
    """`pg_has_role` is the trap this query exists to avoid.

    The deploy preflight connects as `app_admin`, which is NOSUPERUSER by
    contract. PostgreSQL restricts which roles a non-superuser may interrogate
    with `pg_has_role`, so a check spelled that way could answer "no edges"
    because it was refused rather than because none exist — a false clean, which
    is the worst outcome a security gate can produce. `pg_roles` and
    `pg_auth_members` are world-readable.
    """
    assert "pg_auth_members" in ROLE_ESCALATION_SQL
    assert "pg_roles" in ROLE_ESCALATION_SQL
    assert "pg_has_role" not in ROLE_ESCALATION_SQL
    assert "pg_authid" not in ROLE_ESCALATION_SQL, (
        "pg_authid holds password hashes and is superuser-only"
    )


def test_the_walk_is_transitive() -> None:
    """A two-hop grant is the same escalation as a one-hop grant.

    `GRANT app_admin TO middle; GRANT middle TO app_user` reaches BYPASSRLS just
    as directly. A non-recursive query would report the clean tree and miss it.
    """
    assert "WITH RECURSIVE" in ROLE_ESCALATION_SQL
    assert ROLE_ESCALATION_SQL.count("pg_auth_members") == 2, (
        "the recursive term is what walks past the first hop"
    )


def test_the_preflight_actually_runs_the_check() -> None:
    """A contract no entry point evaluates is documentation.

    Pinned on the deploy PREFLIGHT (`--verify-only`, scripts/deploy.sh step 3a)
    rather than inside `alembic/env.py`: the preflight stops before any DDL and
    prints a remedy, whereas a refusal mid-chain leaves a half-applied upgrade.
    That `alembic/env.py` does NOT carry this check is a stated unmonitored
    region, not an exemption — a migration run that skips the preflight is not
    covered.
    """
    source = BOOTSTRAP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    verifier = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "verify_migration_connection"
    )
    called = {
        node.func.id
        for node in ast.walk(verifier)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "role_escalation_violations" in called
    assert "database_identity_violations" in called
    assert "migration_executor_violations" in called


def test_a_direct_grant_into_the_migration_executor_is_named() -> None:
    """Sensitivity proof: the exact defect that would undo this whole change."""
    violations = role_escalation_violations(
        [("app_user", "app_admin", False, False, True)]
    )
    assert len(violations) == 1
    assert "app_user" in violations[0]
    assert "app_admin" in violations[0]
    assert "BYPASSRLS" in violations[0]


def test_a_superuser_reach_is_named_even_with_no_flags_set() -> None:
    """`rolsuper` alone is enough; a superuser bypasses RLS regardless of
    `rolbypassrls`, so a check keyed only on the flag would pass this."""
    violations = role_escalation_violations(
        [("platform_api", "postgres", True, False, False)]
    )
    assert len(violations) == 1
    assert "SUPERUSER" in violations[0]


def test_every_escalating_attribute_can_trigger_alone() -> None:
    """Each named attribute is load-bearing, not decorative."""
    for index, attribute in enumerate(ESCALATING_ATTRIBUTES):
        flags = [False, False, False]
        flags[index] = True
        violations = role_escalation_violations(
            [("outbox_dispatcher", "elevated", *flags)]  # type: ignore[arg-type]
        )
        assert len(violations) == 1, attribute
        assert attribute in violations[0]


def test_every_subject_and_every_reachable_role_is_reported() -> None:
    """One line per pair. A first-match detector hides the second GRANT, and an
    operator who revokes only what was printed believes they are finished."""
    violations = role_escalation_violations(
        [
            ("app_user", "app_admin", False, False, True),
            ("app_user", "postgres", True, False, False),
            ("platform_outbox_dispatcher", "app_admin", False, False, True),
        ]
    )
    assert len(violations) == 3


def test_an_unprivileged_reach_is_not_named() -> None:
    """Near-miss control: membership is not itself the defect.

    `GRANT pg_monitor TO claude_readonly` already exists in this repository
    (scripts/setup_pg_observability.sql:48). Membership in a role that carries
    none of the three attributes must stay quiet, or the gate fires on every
    ordinary group role and gets switched off.
    """
    assert (
        role_escalation_violations(
            [("app_user", "reporting_readers", False, False, False)]
        )
        == ()
    )


def test_a_non_subject_role_is_not_named() -> None:
    """Second near miss: `app_admin` reaching BYPASSRLS is its contract, not a
    finding. A gate that reported it would be noise on every clean run."""
    assert (
        role_escalation_violations(
            [("app_admin", "app_admin_group", False, False, True)]
        )
        == ()
    )
