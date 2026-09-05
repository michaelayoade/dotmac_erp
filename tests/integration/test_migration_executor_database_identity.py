"""The real migration executor proves which database it reached.

`app_admin`'s contract was evaluated in two places that answered different
questions, and only one of them ran on the path that migrates.

`scripts/bootstrap_database_roles.py --verify-only` — `scripts/deploy.sh` step
3a — asserted WHO the connection is, WHAT it owns, WHETHER the role graph lets a
runtime role become it, and WHERE it landed. `alembic/env.py` asserted the first
two and not the last. `make migrate`, `make docker-migrate` and CI's own
`alembic upgrade heads` never reach the preflight at all, so on three of the
four migration paths nothing said which database was being migrated.

Role posture and object ownership are BOTH satisfiable by the wrong cluster: a
staging database with its own correctly shaped `app_admin` owning its own
objects passes every check the executor made. That is the defect these measure.

## Why these are integration tests and not unit tests

`tests/architecture/test_runtime_role_escalation_contract.py` reads the AST and
proves the call sites exist and are ordered correctly. That is a claim about
source. THIS file drives `alembic upgrade` against a real PostgreSQL database
through `alembic/env.py` — the same entry point a deployment uses — so what is
proved is that the executor REFUSES, not that a function is referenced.

Neither substitutes for the other: a call site nothing executes is documentation,
and a passing run with no contract behind it is a snapshot.

## The ordering claim is MEASURED here, not asserted

The recorded reason for keeping this check out of `alembic/env.py` was that a
refusal there "leaves a half-applied upgrade". That premise was false —
`run_migrations_online` evaluates the contract inside a read-only
`connection.begin()` that closes before `context.configure`,
`context.begin_transaction()` and `context.run_migrations()`.

`test_a_refused_upgrade_applies_nothing_at_all` is what turns that from an
argument into an observation: after a refusal it asserts the database carries no
`alembic_version` table and no ERP tables. If the ordering ever regressed, that
test fails against a real database rather than a reading of the source.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy import URL, create_engine, make_url, text

pytestmark = pytest.mark.integration


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _psycopg_url(url: URL) -> str:
    return _render(url.set(drivername="postgresql"))


def _config(database_url: URL) -> Config:
    """ERP's real Alembic configuration, as checked in.

    Deliberately not a config assembled here: the question is whether the
    executor ERP ships refuses, and a hand-built config answers a different one.
    """
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", _render(database_url))
    return config


@pytest.fixture()
def isolated_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[URL, str]]:
    """A disposable database owned by `app_admin`, never a shared one.

    Every test here either refuses an upgrade or completes one, so a shared
    database would leak state between them and — more importantly — a test that
    asserts "nothing was applied" cannot run against a database something else
    already migrated.
    """
    configured = os.getenv("TEST_DATABASE_URL")
    if not configured:
        raise pytest.UsageError(
            "the migration executor identity proof requires TEST_DATABASE_URL"
        )
    base_url = make_url(configured)
    if not base_url.drivername.startswith("postgresql"):
        raise pytest.UsageError("this proof requires PostgreSQL")

    name = f"erp_executor_identity_{uuid4().hex}"
    maintenance = base_url.set(database="postgres")
    with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER app_admin").format(sql.Identifier(name))
        )
    try:
        database_url = base_url.set(database=name, username="app_admin", password=None)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", _render(database_url))
        yield database_url, name
    finally:
        with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
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


def test_the_executor_refuses_a_database_it_was_not_authorised_for(
    isolated_database: tuple[URL, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE DEFECT. The connection is a correctly shaped `app_admin` owning its
    own objects — everything the executor used to check — and it is the WRONG
    DATABASE.

    This is not a contrived shape. It is precisely a reconciliation aimed at
    staging while the operator believed they were on production, or the reverse.
    """
    database_url, name = isolated_database
    monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", f"{name}_not_this_one")

    with pytest.raises(RuntimeError) as raised:
        command.upgrade(_config(database_url), "heads")

    message = str(raised.value)
    assert "migration executor contract failed" in message
    # BOTH names, so the operator can see which way round the mistake is.
    assert name in message, "the refusal must name the database it reached"
    assert f"{name}_not_this_one" in message, (
        "the refusal must name the database it was authorised for"
    )


def test_a_refused_upgrade_applies_nothing_at_all(
    isolated_database: tuple[URL, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordering premise, MEASURED against a real database.

    The reason this check was originally kept out of `alembic/env.py` was that a
    refusal there would leave a half-applied upgrade. If that were true, this
    test would find tables. It finds none, because the contract is evaluated in
    a read-only block that closes before Alembic takes transaction authority.

    This is the sensitivity proof for overturning that premise. An assertion
    about source ordering can be read wrong; a count of tables cannot.
    """
    database_url, name = isolated_database
    monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", f"{name}_not_this_one")

    assert _table_count(database_url) == 0, "the fixture must start empty"

    with pytest.raises(RuntimeError):
        command.upgrade(_config(database_url), "heads")

    assert _table_count(database_url) == 0, (
        "a refused upgrade applied schema: the contract is now being evaluated "
        "after Alembic took transaction authority, which is the half-applied "
        "upgrade this check was once kept out to avoid"
    )


def test_the_matching_database_is_accepted(
    isolated_database: tuple[URL, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NEAR-MISS. Without this, a guard that refused every upgrade — or one that
    compared against a constant — would satisfy both tests above.

    It also proves the expectation is compared against `current_database()`
    rather than against something parsed out of the DSN, which would agree with
    itself and observe nothing.
    """
    database_url, name = isolated_database
    monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", name)

    command.upgrade(_config(database_url), "heads")

    assert _table_count(database_url) > 0, "the authorised upgrade did not apply"


def test_an_unbound_run_says_so_and_still_proceeds(
    isolated_database: tuple[URL, str],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Silence is not a pass, and absence is not a refusal (ADR-0018).

    Unset must NOT block — mandatory on day one is how a check gets deleted
    rather than adopted, and every existing caller reaches this path. But the
    run must say what it did not check, so a green migration cannot be read as a
    checked one.

    `capfd` rather than `capsys`: the notice is written to the process's file
    descriptor 2 and this asserts it reaches the operator's terminal, not merely
    that a Python-level object was written to.
    """
    database_url, _name = isolated_database
    monkeypatch.delenv("MIGRATION_EXPECTED_DATABASE", raising=False)

    command.upgrade(_config(database_url), "heads")

    stderr = capfd.readouterr().err
    assert "database identity UNVERIFIED" in stderr
    assert "MIGRATION_EXPECTED_DATABASE" in stderr, (
        "the notice must name the variable that turns it into an assertion"
    )


def test_a_blank_expectation_is_treated_as_absent_not_as_a_name(
    isolated_database: tuple[URL, str],
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """`-e MIGRATION_EXPECTED_DATABASE` with nothing in the operator's shell.

    Docker and CI both forward a variable that resolves to the empty string. If
    that were read as a database NAME, every such run would refuse against a
    database called `''` — and the fix would be to delete the check. It is
    whitespace-stripped to `None`, which is the unverified path above.
    """
    database_url, _name = isolated_database
    monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", "   ")

    command.upgrade(_config(database_url), "heads")

    assert "database identity UNVERIFIED" in capfd.readouterr().err
