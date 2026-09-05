"""The authority contract is stated once, observable, and actually evaluated.

Was `test_runtime_role_escalation_contract.py`. It asked one question — can a
runtime role BECOME privileged. There are two authority classes now, with
deliberately different answers, so the name no longer described the subject.

This module is the STATIC half: the subjects are derived from the role
contracts rather than hand-listed, the observation uses catalogues the
preflight connection can actually read, the walk is transitive, and every entry
point that must evaluate the policies does. The half that measures a real role
graph is `tests/integration/test_runtime_role_authority.py`; neither
substitutes for the other, because a contract nothing observes is a claim and
an observation with no contract is a snapshot.

Scanner identity, policy asymmetry and the pure evaluator's verdicts are owned
elsewhere and deliberately not restated here:
`tests/architecture/test_migration_authority_callers.py` and
`tests/unit/test_migration_authority_policies.py`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.migration_authority import (
    ATTRIBUTE_SPELLING,
    AUTHORITY_SUBJECTS,
    ROLE_AUTHORITY_SQL,
    MigrationExecutorAuthorityPolicyV1,
    RuntimeRoleAuthorityPolicyV1,
)
from app.migration_database_roles import (
    MIGRATION_EXECUTOR,
    RELAY_DISPATCHER_CONTRACT,
    ROLE_CONTRACT,
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


def _source_of(path: Path, function: str) -> str:
    """The source text of one named function."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    target = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function
    )
    segment = ast.get_source_segment(source, target)
    assert segment is not None
    return segment


def test_every_online_identity_is_a_subject_of_some_policy() -> None:
    """The subject sets are derived from the two role contracts, not hand-listed.

    `app_admin` used to be the one deliberate EXCLUSION, and that exclusion was
    recorded as an unmonitored region: it is BYPASSRLS by contract, so "can it
    reach BYPASSRLS" is not a question about it — but "can it reach SUPERUSER or
    CREATEROLE" always was, and nothing asked. It is now the migration executor
    policy's subject, so the equality below is EXACT with no carve-out, and the
    unmonitored region is closed rather than restated.

    A role added to either contract without joining a policy fails here, which
    is the only thing keeping the coverage true as the role set grows.
    """
    online = set(ROLE_CONTRACT) | set(RELAY_DISPATCHER_CONTRACT)
    assert online == set(AUTHORITY_SUBJECTS), (
        "a role joined an online contract without joining an authority policy, "
        "or vice versa"
    )
    assert MIGRATION_EXECUTOR in MigrationExecutorAuthorityPolicyV1.subjects
    assert MIGRATION_EXECUTOR not in RuntimeRoleAuthorityPolicyV1.subjects, (
        "folding the executor into the runtime subject set is the rejected "
        "shortcut: it makes one policy answer two different questions"
    )


def test_the_named_attributes_are_the_ones_the_ruling_names() -> None:
    """Named individually, because a category is not checkable.

    The ruling names five: the three that make a reachable role privileged, and
    the two more (`CREATEDB`, `REPLICATION`) the executor must also not hold.
    """
    assert set(ATTRIBUTE_SPELLING.values()) == {
        "SUPERUSER",
        "CREATEROLE",
        "CREATEDB",
        "REPLICATION",
        "BYPASSRLS",
    }
    assert set(RuntimeRoleAuthorityPolicyV1.forbidden_target_attributes) == {
        "superuser",
        "createrole",
        "bypassrls",
    }


def test_the_observation_uses_catalogues_the_preflight_can_read() -> None:
    """`pg_has_role` is the trap this query exists to avoid.

    The deploy preflight connects as `app_admin`, which is NOSUPERUSER by
    contract. PostgreSQL restricts which roles a non-superuser may interrogate
    with `pg_has_role`, so a check spelled that way could answer "no edges"
    because it was refused rather than because none exist — a false clean, which
    is the worst outcome a security gate can produce. `pg_roles` and
    `pg_auth_members` are world-readable.
    """
    assert "pg_auth_members" in ROLE_AUTHORITY_SQL
    assert "pg_roles" in ROLE_AUTHORITY_SQL
    assert "pg_has_role" not in ROLE_AUTHORITY_SQL
    assert "pg_authid" not in ROLE_AUTHORITY_SQL, (
        "pg_authid holds password hashes and is superuser-only"
    )


def test_the_walk_is_transitive() -> None:
    """A two-hop grant is the same escalation as a one-hop grant.

    `GRANT app_admin TO middle; GRANT middle TO app_user` reaches BYPASSRLS just
    as directly. A non-recursive query would report the clean tree and miss it.
    """
    assert "WITH RECURSIVE" in ROLE_AUTHORITY_SQL
    assert ROLE_AUTHORITY_SQL.count("pg_auth_members") == 3, (
        "three references: the walk's base term, the recursive term that goes "
        "past the first hop, and the direct-edge CTE that lets a refusal say "
        "whether there is one grant to revoke or a chain to unpick"
    )


def test_the_scanner_does_not_read_set_option() -> None:
    """Conservative on every edge, by construction rather than by intention.

    `set_option` tells you whether `SET ROLE` is executable along an edge RIGHT
    NOW. The invariant is prohibited privileged membership closure, not present
    executability: a later `GRANT ... WITH SET TRUE` flips that column with no
    migration running and nothing here observing it again. Not reading the
    column is what makes the conservatism impossible to erode by accident.
    """
    assert "set_option" not in ROLE_AUTHORITY_SQL
    assert "inherit_option" not in ROLE_AUTHORITY_SQL


def test_the_preflight_actually_runs_both_policies() -> None:
    """A contract no entry point evaluates is documentation."""
    called = _calls_in(BOOTSTRAP, "verify_migration_connection")
    assert "role_authority_violations" in called
    assert "database_identity_violations" in called
    assert "migration_executor_violations" in called
    assert "parse_authentication_expectation" in called, (
        "the preflight connects as a freshly authenticated app_admin and is one "
        "of only two places that can bind an authentication expectation"
    )


def test_the_elevated_bootstrap_validates_after_repairing() -> None:
    """Execution point 1: after creating or repairing, BEFORE reporting success.

    And it must drop the authentication clause while doing so — that connection
    is the elevated bootstrap identity, and letting it assert it authenticated
    as `app_admin` would turn the strongest proof in this design into one a
    superuser produces for itself.
    """
    called = _calls_in(BOOTSTRAP, "_authority_after_bootstrap")
    assert "role_authority_violations" in called

    source = BOOTSTRAP.read_text(encoding="utf-8")
    assert "_authority_after_bootstrap(conn)" in source, (
        "nothing calls the post-bootstrap check, so it is documentation"
    )
    # Read the FUNCTION's own source, not the whole file: `bootstrap` and
    # `verify_migration_connection` legitimately mention both spellings, and a
    # file-wide search would pass whichever one this function used.
    region = _source_of(BOOTSTRAP, "_authority_after_bootstrap")
    assert "without_direct_authentication()" in region
    assert "binding_authentication" not in region, (
        "the elevated bootstrap bound an authentication expectation; it "
        "connects as the privileged identity and must not be able to assert it "
        "authenticated as the migration executor"
    )


def test_the_real_executor_asserts_identity_authority_and_database() -> None:
    """The checks have to run where the migration runs.

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
    assert "unverified_authentication_notice" in called, (
        "the same rule for the authentication binding: absent is reported"
    )
    assert "migration_executor_violations" in called
    assert "role_authority_violations" in called, (
        "a dirty role graph is not an unrelated cluster condition; the subjects "
        "are this deployment's own identities"
    )


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
