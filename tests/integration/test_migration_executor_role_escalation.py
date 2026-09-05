"""The real migration executor refuses an escalating role graph.

Withholding `MIGRATION_DATABASE_URL` from `app`, `worker` and `beat` is worth
nothing if the credential those services still hold can BECOME the one taken
away. PostgreSQL role ATTRIBUTES are not inherited through membership — a member
of a superuser role is not itself `rolsuper` — but `SET ROLE` adopts the
target's attributes for the session, and membership is all `SET ROLE` requires.
So `GRANT app_admin TO app_user` reinstates BYPASSRLS in one statement, and
`ROLE_CONTRACT` cannot see it: that contract reads each role's OWN
`(rolbypassrls, rolsuper)`, which the grant leaves untouched.

## Why this belongs in the executor and not only the preflight

A dirty graph is not an unrelated cluster condition. The subjects are THIS
deployment's runtime identities. Advancing a migration while `app_user` can
`SET ROLE` into SUPERUSER, CREATEROLE or BYPASSRLS admits an unsafe runtime, and
the deploy preflight is one caller of four — `make migrate`,
`make docker-migrate` and CI's own `alembic upgrade heads` never reach it.

## One owner, a second caller

These drive `alembic upgrade` through `alembic/env.py`. The executor issues the
existing `ROLE_ESCALATION_SQL` and hands the rows to the existing
`role_escalation_violations`. It re-decides nothing: if the policy changes, it
changes in one place and both callers follow.

## Role grants are CLUSTER-wide, so every test here restores the graph

`pg_auth_members` is not per-database. A grant made here is visible to every
other test in the job, so each case revokes in a `finally` and the fixture also
revokes defensively on entry — a previous crashed run must not silently poison
the rest of the suite into a state where these tests pass for the wrong reason.

The integration lane runs pytest without `-n`, so these are sequential.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import URL, create_engine, make_url, text

from app.migration_database_roles import MIGRATION_EXECUTOR, NON_ESCALATING_ROLES

pytestmark = pytest.mark.integration


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
        raise pytest.UsageError("the escalation proof requires TEST_DATABASE_URL")
    url = make_url(configured)
    if not url.drivername.startswith("postgresql"):
        raise pytest.UsageError("this proof requires PostgreSQL")
    return url


@contextmanager
def _membership(
    grants: list[tuple[str, str]], *, create: tuple[str, ...] = ()
) -> Iterator[None]:
    """GRANT for the duration of one test, then put the cluster back.

    `create` names intermediate roles that must exist first — the transitive
    case needs a middle role, and it must not outlive the test either.
    """
    admin_url = _psycopg_url(_superuser_url().set(database="postgres"))

    def _revoke() -> None:
        with psycopg.connect(admin_url, autocommit=True) as admin:
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

    with psycopg.connect(admin_url, autocommit=True) as admin:
        for role in create:
            admin.execute(
                sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role))
            )
            admin.execute(sql.SQL("CREATE ROLE {}").format(sql.Identifier(role)))
        for member, target in grants:
            admin.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(target), sql.Identifier(member)
                )
            )
    try:
        yield
    finally:
        _revoke()


@pytest.fixture()
def isolated_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[URL]:
    """A disposable database owned by `app_admin`, with a clean role graph.

    The defensive revoke on entry matters: role grants are cluster-wide, so a
    previously crashed test could leave `app_user` a member of `app_admin`. That
    would make the clean-graph case below fail for a reason that has nothing to
    do with it, and — worse — could make a refusal case pass without the test
    having planted anything.
    """
    base_url = _superuser_url()
    admin_url = _psycopg_url(base_url.set(database="postgres"))
    with psycopg.connect(admin_url, autocommit=True) as admin:
        for role in sorted(NON_ESCALATING_ROLES):
            admin.execute(
                sql.SQL("REVOKE {} FROM {}").format(
                    sql.Identifier(MIGRATION_EXECUTOR), sql.Identifier(role)
                )
            )

    name = f"erp_escalation_{uuid4().hex}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
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
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name))
            )


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


# ── PROOF 1: a clean graph admits ───────────────────────────────────────────


def test_a_clean_membership_graph_admits(isolated_database: URL) -> None:
    """The near-miss for every refusal below.

    Without this, a check that refused unconditionally would satisfy all three
    escalation tests and block every migration ERP has. It is also what makes
    the refusals attributable: they fail because of the grant, not because the
    executor cannot complete an upgrade at all.
    """
    command.upgrade(_config(isolated_database), "heads")
    assert _table_count(isolated_database) > 0


# ── PROOF 2: a direct grant refuses ─────────────────────────────────────────


def test_a_direct_grant_into_the_migration_executor_refuses(
    isolated_database: URL,
) -> None:
    """`GRANT app_admin TO app_user` — one statement, BYPASSRLS reinstated.

    `ROLE_CONTRACT` cannot see this: `app_user`'s own `rolbypassrls` is still
    false. Only the membership walk finds it.
    """
    with _membership([("app_user", MIGRATION_EXECUTOR)]):
        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")

    message = str(raised.value)
    assert "migration executor contract failed" in message
    assert "app_user" in message
    assert MIGRATION_EXECUTOR in message
    assert "BYPASSRLS" in message, (
        "the refusal must name the attribute reached, so an operator knows "
        "which grant to revoke"
    )


# ── PROOF 3: a transitive grant refuses ─────────────────────────────────────


def test_a_transitive_grant_refuses(isolated_database: URL) -> None:
    """Two hops reach BYPASSRLS just as directly as one.

    `GRANT app_admin TO erp_escalation_middle; GRANT erp_escalation_middle TO
    app_user`. A non-recursive query would report the clean first hop and miss
    this entirely, which is why `ROLE_ESCALATION_SQL` is `WITH RECURSIVE`.
    """
    middle = "erp_escalation_middle"
    with _membership(
        [(middle, MIGRATION_EXECUTOR), ("app_user", middle)], create=(middle,)
    ):
        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")

    message = str(raised.value)
    assert "app_user" in message
    assert MIGRATION_EXECUTOR in message, (
        "the transitive target must be named, not just the intermediate hop"
    )


# ── PROOF 4: every declared runtime role is covered ──────────────────────────


def test_every_declared_runtime_role_is_covered(isolated_database: URL) -> None:
    """COVERAGE, DERIVED — not a hand-written list of four names.

    Both the grants and the assertions iterate `NON_ESCALATING_ROLES`. A role
    added to that contract tomorrow is granted, refused and asserted here with
    no edit to this file. A hand list would silently stop covering it, and the
    test would keep passing — which is the failure this shape exists to prevent.
    """
    grants = [(role, MIGRATION_EXECUTOR) for role in sorted(NON_ESCALATING_ROLES)]
    with _membership(grants):
        with pytest.raises(RuntimeError) as raised:
            command.upgrade(_config(isolated_database), "heads")

    message = str(raised.value)
    for role in sorted(NON_ESCALATING_ROLES):
        assert role in message, (
            f"{role} is a declared runtime identity and its escalation was not "
            f"reported; every subject must be named, not just the first"
        )


# ── PROOF 5: a refusal applies nothing ──────────────────────────────────────


def test_a_refused_upgrade_applies_zero_migrations_and_zero_tables(
    isolated_database: URL,
) -> None:
    """The ordering premise, measured against a real database.

    The executor contract is evaluated inside a read-only `connection.begin()`
    that closes before `context.configure`, `context.begin_transaction()` and
    `context.run_migrations()`. If that ever regressed, this finds tables — and
    `alembic_version` is checked separately because a chain can stamp a revision
    without creating anything an operator would notice.
    """
    assert _table_count(isolated_database) == 0, "the fixture must start empty"

    with _membership([("app_user", MIGRATION_EXECUTOR)]):
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
