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
#: The REAL migration executor. `alembic upgrade` runs this on every path,
#: including the ones that never reach the deploy preflight.
ENV_PY = REPO_ROOT / "alembic" / "env.py"


def _calls_in(path: Path, function: str) -> set[str]:
    """Names called inside one named function, read from the AST.

    AST rather than a substring scan: these functions carry docstrings that
    NAME the checks they run and explain why, and a text search happily matches
    the prose justifying a check instead of the call performing it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function
    )
    return {
        node.func.id
        for node in ast.walk(target)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


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
    """A contract no entry point evaluates is documentation."""
    called = _calls_in(BOOTSTRAP, "verify_migration_connection")
    assert "role_escalation_violations" in called
    assert "database_identity_violations" in called
    assert "migration_executor_violations" in called


def test_the_real_executor_asserts_which_database_it_reached() -> None:
    """The check has to run where the migration runs.

    The deploy preflight is ONE caller — `scripts/deploy.sh` step 3a. `make
    migrate`, `make docker-migrate` and CI's own `alembic upgrade heads` reach
    the database without it. A capability proven only on a path a caller may
    skip is available, not adopted.

    THE PREMISE THAT KEPT THIS OUT WAS FALSE. The recorded reason for pinning it
    to the preflight was that a refusal inside `alembic/env.py` "leaves a
    half-applied upgrade". It does not, at this call site:
    `run_migrations_online` invokes `verify_migration_connection` inside a
    read-only `connection.begin()` block that closes BEFORE `context.configure`,
    before `context.begin_transaction()` and before `context.run_migrations()`.
    `test_the_executor_check_runs_before_any_migration_is_applied` below pins
    that ordering, because the ordering is the entire argument.
    """
    called = _calls_in(ENV_PY, "verify_migration_connection")
    assert "database_identity_violations" in called, (
        "the real migration executor must assert WHERE it landed; role posture "
        "and object ownership are both satisfiable by the wrong cluster"
    )
    assert "unverified_database_identity_notice" in called, (
        "unset must be reported, never assumed — a green migration that checked "
        "nothing must not read as a checked one"
    )
    assert "migration_executor_violations" in called


def test_the_executor_check_runs_before_any_migration_is_applied() -> None:
    """The ordering that makes the check above safe, pinned so it cannot drift.

    This is the sensitivity proof for the claim in the docstring above. If
    someone moved `verify_migration_connection` after `context.run_migrations()`
    — or into a revision — a refusal really would leave a half-applied upgrade,
    and the reason the check was originally kept out would become true. The
    guard is on the ORDER, not on the presence, because presence is what the
    previous test already covers.
    """
    tree = ast.parse(ENV_PY.read_text(encoding="utf-8"))
    online = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_migrations_online"
    )
    order: list[tuple[int, str]] = [
        (node.lineno, node.func.id)
        for node in ast.walk(online)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"verify_migration_connection"}
    ]
    order += [
        (node.lineno, node.func.attr)
        for node in ast.walk(online)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"run_migrations", "configure", "begin_transaction"}
    ]
    positions = {name: line for line, name in order}
    for later in ("configure", "begin_transaction", "run_migrations"):
        assert later in positions, f"{later} vanished from run_migrations_online"
        assert positions["verify_migration_connection"] < positions[later], (
            f"the executor contract must be evaluated before {later}; a refusal "
            f"after it is the half-applied upgrade the check was once kept out "
            f"to avoid"
        )


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
