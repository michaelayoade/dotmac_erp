"""Both authority policies, proved against a real PostgreSQL role graph.

Extends the proofs first written for PR #462
(`tests/integration/test_migration_executor_role_escalation.py` on
`feat/executor-refuses-an-escalating-role-graph`, never merged). That work
proved the membership walk; it could not prove the two gaps it left open:

1. an attribute held DIRECTLY by a subject — `ROLE_CONTRACT` reads
   `(rolbypassrls, rolsuper)` and never `rolcreaterole`, and no membership walk
   can help because a role is not a member of itself;
2. membership in `pg_read_server_files`, `pg_write_server_files` or
   `pg_execute_server_program` — these hold NONE of SUPERUSER, CREATEROLE or
   BYPASSRLS, so the superseded scanner's `WHERE` discarded the edges before any
   evaluator ran.

## Role grants and role attributes are CLUSTER-wide

`pg_auth_members` and `pg_authid` are not per-database. Anything planted here is
visible to every other test in the job, so each case restores in a `finally` AND
the fixture restores defensively on entry: a previously crashed run must not
leave a grant that makes a refusal case pass with nothing planted, nor an
attribute that makes the clean case fail for an unrelated reason.

The integration lane runs pytest without `-n`, so these are sequential.

## What no test here can prove

`system_user` is NULL under `trust` authentication, and this lane is
`POSTGRES_HOST_AUTH_METHOD: trust`. The `session_user`/`current_user` half of
the direct authentication proof is exercised below; the `system_user` half
cannot be, and is asserted in the password-authenticated
`migration-authentication-proof` CI job instead. A trust-authenticated tier
cannot be authentication evidence, and pretending otherwise here would be the
worst outcome.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import URL, create_engine, make_url, text

from app.migration_authority import (
    AUTHORITY_SUBJECTS,
    MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH,
    MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN,
    MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP,
    RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN,
    RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP,
    ROLE_AUTHORITY_SQL,
    SERVER_FILE_OR_PROGRAM_ROLES,
    MigrationExecutorAuthorityPolicyV1,
    RuntimeRoleAuthorityPolicyV1,
    observation_from_rows,
    role_authority_violations,
)
from app.migration_database_roles import MIGRATION_EXECUTOR

pytestmark = pytest.mark.integration

RUNTIME_SUBJECTS = sorted(RuntimeRoleAuthorityPolicyV1.subjects)
ALL_SUBJECTS = sorted(AUTHORITY_SUBJECTS)
#: Attributes a test may plant directly on a subject, in `ALTER ROLE` spelling.
PLANTABLE = ("SUPERUSER", "CREATEROLE", "CREATEDB", "REPLICATION", "BYPASSRLS")


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _psycopg_url(url: URL) -> str:
    return _render(url.set(drivername="postgresql"))


def _config(database_url: URL) -> Config:
    """ERP's real Alembic configuration, as checked in."""
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", _render(database_url))
    return config


def _superuser_url() -> URL:
    configured = os.getenv("TEST_DATABASE_URL")
    if not configured:
        raise pytest.UsageError("the authority proofs require TEST_DATABASE_URL")
    url = make_url(configured)
    if not url.drivername.startswith("postgresql"):
        raise pytest.UsageError("this proof requires PostgreSQL")
    return url


@contextmanager
def _admin() -> Iterator[psycopg.Connection]:
    with psycopg.connect(
        _psycopg_url(_superuser_url().set(database="postgres")), autocommit=True
    ) as connection:
        yield connection


def _restore_clean_graph(connection: psycopg.Connection) -> None:
    """Put every subject back to the posture a correct bootstrap leaves.

    Runs on fixture ENTRY as well as on exit. Defensive entry is the half that
    matters: without it a crashed earlier run leaves a grant behind, and a
    refusal case then passes having planted nothing at all.
    """
    for subject in ALL_SUBJECTS:
        for target in (MIGRATION_EXECUTOR, *sorted(SERVER_FILE_OR_PROGRAM_ROLES)):
            if target == subject:
                continue
            connection.execute(
                sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(target), sql.Identifier(subject)
                )
            )
        wanted = " ".join(
            attribute
            if (subject == MIGRATION_EXECUTOR and attribute == "BYPASSRLS")
            else f"NO{attribute}"
            for attribute in PLANTABLE
        )
        connection.execute(
            sql.SQL("ALTER ROLE {} {}").format(sql.Identifier(subject), sql.SQL(wanted))
        )


@contextmanager
def _planted_attributes(subject: str, attributes: str) -> Iterator[None]:
    """`ALTER ROLE <subject> <attributes>` for one test, then put it back."""
    with _admin() as admin:
        admin.execute(
            sql.SQL("ALTER ROLE {} {}").format(
                sql.Identifier(subject), sql.SQL(attributes)
            )
        )
    try:
        yield
    finally:
        with _admin() as admin:
            _restore_clean_graph(admin)


@contextmanager
def _membership(
    grants: list[tuple[str, str]],
    *,
    create: tuple[str, ...] = (),
    create_attributes: str = "",
    options: str = "",
) -> Iterator[None]:
    """GRANT for the duration of one test, then put the cluster back.

    `create` names intermediate roles that must exist first — the two-hop case
    needs a middle role, and it must not outlive the test either. `options` is
    appended to the `GRANT`, which is how the `WITH SET FALSE` proof is written.
    """

    def _revoke() -> None:
        with _admin() as admin:
            for member, target in reversed(grants):
                admin.execute(
                    sql.SQL("REVOKE {} FROM {}").format(
                        sql.Identifier(target), sql.Identifier(member)
                    )
                )
            for role in create:
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
                )
            _restore_clean_graph(admin)

    with _admin() as admin:
        for role in create:
            admin.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
            )
            admin.execute(
                sql.SQL("CREATE ROLE {} {}").format(
                    sql.Identifier(role), sql.SQL(create_attributes)
                )
            )
        for member, target in grants:
            admin.execute(
                sql.SQL("GRANT {} TO {} {}").format(
                    sql.Identifier(target), sql.Identifier(member), sql.SQL(options)
                )
            )
    try:
        yield
    finally:
        _revoke()


@pytest.fixture()
def isolated_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[URL]:
    """A disposable database owned by `app_admin`, with a clean role graph."""
    base_url = _superuser_url()
    with _admin() as admin:
        _restore_clean_graph(admin)

    name = f"erp_authority_{uuid4().hex}"
    with _admin() as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER app_admin").format(sql.Identifier(name))
        )
    try:
        database_url = base_url.set(database=name, username="app_admin", password=None)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", _render(database_url))
        # Scoped to THIS RUN. A job-level binding would name a different
        # database than the one this fixture just created.
        monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", name)
        yield database_url
    finally:
        with _admin() as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name))
            )
            _restore_clean_graph(admin)


def _table_count(database_url: URL) -> int:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema NOT IN "
                        "('pg_catalog', 'information_schema')"
                    )
                ).scalar_one()
            )
    finally:
        engine.dispose()


def _observe(database_url: URL, *, set_role: str | None = None):
    """Run the REAL scanner on a real connection and build the observation."""
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        if set_role is not None:
            connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(set_role)))
        rows = connection.execute(
            ROLE_AUTHORITY_SQL, {"subjects": ALL_SUBJECTS}
        ).fetchall()
    return observation_from_rows(rows)


def _codes(violations) -> set[str]:
    return {item.code for item in violations}


# ── PROOF 1: the clean cluster admits ───────────────────────────────────────


def test_a_clean_app_admin_holding_bypassrls_admits(isolated_database: URL) -> None:
    """The near-miss for every refusal below.

    `app_admin` is BYPASSRLS by contract, and the executor policy REQUIRES it.
    Without this proof, a policy that refused BYPASSRLS on the executor — the
    exact mistake the rejected one-policy shortcut would have made — would
    satisfy every refusal test here and block every migration ERP has.
    """
    command.upgrade(_config(isolated_database), "heads")
    assert _table_count(isolated_database) > 0


# ── PROOF 2: gap 1, an attribute held DIRECTLY ──────────────────────────────


@pytest.mark.parametrize("subject", RUNTIME_SUBJECTS)
def test_direct_createrole_on_every_derived_runtime_subject_refuses(
    subject: str, isolated_database: URL
) -> None:
    """DERIVED from the policy, not a hand list of four names.

    A role added to `RuntimeRoleAuthorityPolicyV1.subjects` tomorrow is planted
    and asserted here with no edit to this file. `ROLE_CONTRACT` cannot see this
    finding at all: it reads `(rolbypassrls, rolsuper)`, both of which the
    `ALTER ROLE` below leaves untouched.
    """
    with _planted_attributes(subject, "CREATEROLE"):
        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")

    message = str(raised.value)
    assert RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN in message
    assert subject in message
    assert "NOCREATEROLE" in message, (
        "the refusal must name the repair, so an operator knows which ALTER ROLE to run"
    )


@pytest.mark.parametrize("attributes", ["CREATEROLE", "SUPERUSER"])
def test_direct_createrole_or_superuser_on_the_executor_refuses(
    attributes: str, isolated_database: URL
) -> None:
    """`app_admin` may hold BYPASSRLS and nothing else.

    `ROLE_CONTRACT` catches the SUPERUSER case and says nothing about
    CREATEROLE. Both are asserted through the executor policy's own code, so
    the CREATEROLE case cannot pass on the older check's behalf.
    """
    with _planted_attributes(MIGRATION_EXECUTOR, attributes):
        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")

    message = str(raised.value)
    assert MIGRATION_EXECUTOR_DIRECT_ATTRIBUTE_FORBIDDEN in message
    assert MIGRATION_EXECUTOR in message
    assert f"NO{attributes}" in message


# ── PROOF 3: membership, direct and two-hop ─────────────────────────────────


@pytest.mark.parametrize("target_attributes", ["SUPERUSER", "CREATEROLE"])
def test_a_direct_membership_into_a_privileged_role_refuses(
    target_attributes: str, isolated_database: URL
) -> None:
    target = "erp_authority_target"
    with _membership(
        [("app_user", target)], create=(target,), create_attributes=target_attributes
    ):
        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")

    message = str(raised.value)
    assert RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP in message
    assert "app_user" in message
    assert target in message
    assert target_attributes in message


@pytest.mark.parametrize("target_attributes", ["SUPERUSER", "CREATEROLE"])
def test_a_two_hop_membership_into_a_privileged_role_refuses(
    target_attributes: str, isolated_database: URL
) -> None:
    """Two hops reach the attribute as surely as one.

    A non-recursive query would report the clean first hop and miss this
    entirely, which is why the walk is `WITH RECURSIVE`.
    """
    middle = "erp_authority_middle"
    target = "erp_authority_target"
    with _membership(
        [(middle, target), ("app_user", middle)],
        create=(target, middle),
        create_attributes="",
    ):
        with _admin() as admin:
            admin.execute(
                sql.SQL("ALTER ROLE {} {}").format(
                    sql.Identifier(target), sql.SQL(target_attributes)
                )
            )
        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")

    message = str(raised.value)
    assert RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP in message
    assert target in message, (
        "the transitive target must be named, not just the intermediate hop"
    )


def test_the_executor_refuses_a_membership_reaching_createrole(
    isolated_database: URL,
) -> None:
    """The executor's OWN closure, which no runtime policy would examine.

    Its subject is `app_admin`, which is deliberately absent from the runtime
    subject set — so before this policy existed, this edge was unmonitored.
    """
    target = "erp_authority_target"
    with _membership(
        [(MIGRATION_EXECUTOR, target)], create=(target,), create_attributes="CREATEROLE"
    ):
        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")

    message = str(raised.value)
    assert MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP in message
    assert target in message


def test_the_executor_admits_a_membership_reaching_only_bypassrls(
    isolated_database: URL,
) -> None:
    """The ASYMMETRY, measured — and the sensitivity proof for the case above.

    `app_admin` already holds BYPASSRLS legitimately, so an edge that reaches
    only BYPASSRLS gains it nothing and must be admitted. If the executor
    policy refused every membership, the CREATEROLE case above would pass
    without CREATEROLE having been detected at all.

    The same edge from `app_user` is refused — that is PROOF 5's job, and it is
    why one policy cannot answer both questions.
    """
    target = "erp_authority_target"
    with _membership(
        [(MIGRATION_EXECUTOR, target)], create=(target,), create_attributes="BYPASSRLS"
    ):
        command.upgrade(_config(isolated_database), "heads")
    assert _table_count(isolated_database) > 0


# ── PROOF 4: gap 2, privileged WITHOUT a privileged attribute ───────────────


@pytest.mark.parametrize("predefined", sorted(SERVER_FILE_OR_PROGRAM_ROLES))
def test_membership_in_each_server_file_or_program_role_refuses(
    predefined: str, isolated_database: URL
) -> None:
    """None of these holds SUPERUSER, CREATEROLE or BYPASSRLS.

    PostgreSQL documents them as reaching server-side files or executing
    programs as the server's operating-system account, which may yield
    superuser-level access while the role holds no privileged attribute. The
    superseded scanner discarded these edges before any evaluator saw them, so
    naming the roles in a policy would have changed nothing.
    """
    with _admin() as admin:
        held = admin.execute(
            "SELECT rolsuper OR rolcreaterole OR rolbypassrls FROM pg_roles "
            "WHERE rolname = %s",
            (predefined,),
        ).fetchone()
    assert held is not None and held[0] is False, (
        f"{predefined} now holds a privileged ATTRIBUTE; if that ever becomes "
        "true this test stops proving the gap it was written for"
    )

    with _membership([("app_user", predefined)]):
        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")

    message = str(raised.value)
    assert RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP in message
    assert predefined in message


def test_the_executor_also_refuses_a_server_file_role(
    isolated_database: URL,
) -> None:
    with _membership([(MIGRATION_EXECUTOR, "pg_execute_server_program")]):
        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")
    assert MIGRATION_EXECUTOR_PRIVILEGED_MEMBERSHIP in str(raised.value)


# ── PROOF 5: SET is not the question ────────────────────────────────────────


def test_a_membership_granted_with_set_false_still_refuses(
    isolated_database: URL,
) -> None:
    """The invariant is prohibited privileged membership closure.

    PostgreSQL 16 records `set_option`, and `WITH SET FALSE` means `SET ROLE`
    is not presently executable along this edge. The policy does not read that
    column and must not: `set_option` is mutable cluster state that a later
    `GRANT ... WITH SET TRUE` flips without any migration running, so resting
    the gate on its present value would rest it on something nothing here
    observes again. The membership must not exist.
    """
    target = "erp_authority_target"
    with _membership(
        [("app_user", target)],
        create=(target,),
        create_attributes="CREATEROLE",
        options="WITH SET FALSE",
    ):
        with _admin() as admin:
            observed = admin.execute(
                "SELECT bool_or(NOT membership.set_option) "
                "FROM pg_auth_members AS membership "
                "JOIN pg_roles AS member ON member.oid = membership.member "
                "JOIN pg_roles AS reached ON reached.oid = membership.roleid "
                "WHERE member.rolname = 'app_user' AND reached.rolname = %s",
                (target,),
            ).fetchone()
        assert observed is not None and observed[0] is True, (
            "the grant did not actually carry SET FALSE, so this test would "
            "pass without exercising the conservative rule"
        )

        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")

    assert RUNTIME_ROLE_PRIVILEGED_MEMBERSHIP in str(raised.value)


# ── PROOF 6: SET ROLE is not authentication ─────────────────────────────────


def test_a_superuser_session_that_set_role_into_the_executor_refuses(
    isolated_database: URL,
) -> None:
    """`current_user` alone cannot tell these two connections apart.

    A superuser that runs `SET ROLE app_admin` reports `current_user =
    app_admin` and satisfies every check that reads it. `session_user` still
    reports the role that actually authenticated, and the policy reads both.

    Driven against the real scanner on a real connection rather than through
    Alembic, because Alembic never issues `SET ROLE` — the hostile shape is an
    operator or a compromised process, not the migration runner.
    """
    superuser = _superuser_url().set(database=isolated_database.database)
    observation = _observe(superuser, set_role=MIGRATION_EXECUTOR)
    assert observation.current_user == MIGRATION_EXECUTOR
    assert observation.session_user != MIGRATION_EXECUTOR

    violations = role_authority_violations(
        MigrationExecutorAuthorityPolicyV1, observation
    )
    assert _codes(violations) == {MIGRATION_AUTHENTICATION_IDENTITY_MISMATCH}

    honest = _observe(isolated_database)
    assert honest.session_user == honest.current_user == MIGRATION_EXECUTOR
    assert (
        role_authority_violations(MigrationExecutorAuthorityPolicyV1, honest) == ()
    ), (
        "the fresh app_admin connection must be admitted, or the case above "
        "proves only that the policy refuses everything"
    )


def test_system_user_is_null_under_trust_authentication(
    isolated_database: URL,
) -> None:
    """Recorded as a measured FACT, not an assumption in a docstring.

    This lane authenticates with `trust`, so `system_user` is NULL and no test
    in it can serve as authentication proof. The bound-identity half of the
    policy is exercised in the password-authenticated
    `migration-authentication-proof` CI job.
    """
    assert _observe(isolated_database).system_user is None


# ── PROOF 7: bootstrap validates after repair ───────────────────────────────


def test_bootstrap_validates_the_graph_after_repairing_and_before_success(
    isolated_database: URL,
) -> None:
    """Bootstrap creates identities and then re-reads what it built.

    `app_user` is planted with CREATEROLE, which `ROLE_CONTRACT` does NOT see —
    the posture it checks, `(rolbypassrls, rolsuper)`, is untouched — so
    `--repair` adopts the role and reports it correct. The post-bootstrap
    authority check is the only thing standing between that and an exit code of
    zero.
    """
    environment = dict(os.environ)
    environment["BOOTSTRAP_DATABASE_URL"] = _psycopg_url(
        _superuser_url().set(database=isolated_database.database)
    )

    clean = subprocess.run(  # noqa: S603
        [sys.executable, "scripts/bootstrap_database_roles.py", "--repair"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert clean.returncode == 0, clean.stderr

    with _planted_attributes("app_user", "CREATEROLE"):
        refused = subprocess.run(  # noqa: S603
            [sys.executable, "scripts/bootstrap_database_roles.py", "--repair"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert RUNTIME_ROLE_DIRECT_ATTRIBUTE_FORBIDDEN in refused.stderr
    assert "adopted: app_user" in refused.stdout, (
        "the older contract must still have reported the role as correct; "
        "otherwise this proves nothing about the gap"
    )


# ── PROOF 8: a refusal applies nothing ──────────────────────────────────────


def test_a_refused_upgrade_applies_zero_tables_and_stamps_no_revision(
    isolated_database: URL,
) -> None:
    """The ordering premise, measured against a real database.

    The contract is evaluated inside a read-only `connection.begin()` that
    closes before `context.configure`, `context.begin_transaction()` and
    `context.run_migrations()`. If that ever regressed, this finds tables — and
    `alembic_version` is checked separately, because a chain can stamp a
    revision without creating anything an operator would notice.
    """
    assert _table_count(isolated_database) == 0, "the fixture must start empty"

    with _planted_attributes("app_user", "CREATEROLE"):
        with pytest.raises(RuntimeError):
            command.upgrade(_config(isolated_database), "heads")

    assert _table_count(isolated_database) == 0, (
        "a refused upgrade applied schema: the contract is being evaluated "
        "after Alembic took transaction authority"
    )

    engine = create_engine(isolated_database)
    try:
        with engine.connect() as connection:
            stamped = connection.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL")
            ).scalar_one()
    finally:
        engine.dispose()
    assert not stamped, "a refused upgrade stamped a revision"
