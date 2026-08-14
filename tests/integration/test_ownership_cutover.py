"""Prove the ownership cutover against a real PostgreSQL estate.

The unit tests decide WHAT moves. Only a database can prove the generated SQL is
valid — `ALTER TABLE` is rejected for a sequence, a domain needs `ALTER DOMAIN`,
a function needs its full argument signature, and an identifier with a capital
letter needs quoting. Every one of those is a mid-transaction failure on a
production estate if it is wrong.

So this builds a database owned by someone else, containing one object of each
kind the plan claims to handle, runs the cutover, and asserts the migration
preflight's own inventory comes back empty.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from app.migration_database_roles import (
    MIGRATION_EXECUTOR,
    MIGRATION_OWNERSHIP_SQL,
    OWNERSHIP_PLAN_SQL,
)

pytestmark = pytest.mark.integration

#: One object of every kind the plan renders a statement for. A cutover that
#: handles tables and quietly mis-renders sequences is the failure this catches.
ESTATE = (
    'CREATE SCHEMA "Mixed Case Schema"',
    'CREATE TABLE "Mixed Case Schema"."select" (id integer PRIMARY KEY)',
    "CREATE TABLE plain_table (id integer PRIMARY KEY, note text)",
    "CREATE TABLE partitioned (id integer, tenant integer) PARTITION BY RANGE (id)",
    "CREATE TABLE partition_one PARTITION OF partitioned FOR VALUES FROM (0) TO (10)",
    "CREATE SEQUENCE plain_sequence",
    "CREATE TABLE zzz_owned_table (id integer PRIMARY KEY)",
    "CREATE SEQUENCE aaa_owned_sequence OWNED BY zzz_owned_table.id",
    "CREATE VIEW plain_view AS SELECT 1 AS one",
    "CREATE MATERIALIZED VIEW plain_matview AS SELECT 1 AS one",
    "CREATE TYPE plain_enum AS ENUM ('a', 'b')",
    "CREATE DOMAIN plain_domain AS text CHECK (VALUE <> '')",
    "CREATE FUNCTION plain_function(a integer, b text) RETURNS integer "
    "LANGUAGE sql IMMUTABLE AS $$ SELECT a $$",
    "CREATE PROCEDURE plain_procedure(a integer) LANGUAGE sql AS $$ SELECT 1 $$",
    "CREATE AGGREGATE plain_aggregate(integer) "
    "(SFUNC = int4pl, STYPE = integer, INITCOND = '0')",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CUTOVER_SCRIPT = PROJECT_ROOT / "scripts" / "cutover_database_ownership.py"
PLAN_TOKEN = re.compile(r"^PLAN_SHA256=([0-9a-f]{64})$", re.MULTILINE)


def _maintenance_url() -> str:
    configured = os.getenv("TEST_DATABASE_URL")
    if not configured:
        raise pytest.UsageError(
            "ownership cutover rehearsal requires TEST_DATABASE_URL"
        )
    url = configured.replace("postgresql+psycopg://", "postgresql://", 1)
    head, _, _ = url.rpartition("/")
    return f"{head}/postgres"


@pytest.fixture()
def foreign_owned_database() -> Iterator[str]:
    """A database and estate owned by the CONNECTING role, not by app_admin."""
    name = f"erp_ownership_cutover_{uuid4().hex}"
    maintenance = _maintenance_url()
    with psycopg.connect(maintenance, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    head, _, _ = maintenance.rpartition("/")
    target = f"{head}/{name}"
    try:
        with psycopg.connect(target, autocommit=True) as conn:
            for statement in ESTATE:
                conn.execute(statement)
        yield target
    finally:
        with psycopg.connect(maintenance, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name))
            )


def _residual(conn: psycopg.Connection) -> dict[str, int]:
    return {
        str(r[0]): int(r[1]) for r in conn.execute(MIGRATION_OWNERSHIP_SQL).fetchall()
    }


def _database_name(url: str) -> str:
    with psycopg.connect(url) as conn:
        row = conn.execute("SELECT current_database()").fetchone()
        assert row is not None
        return str(row[0])


def _source_owner(url: str) -> str:
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database "
            "WHERE datname = current_database()"
        ).fetchone()
        assert row is not None
        return str(row[0])


def _run_cli(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["OWNERSHIP_DATABASE_URL"] = url
    return subprocess.run(  # noqa: S603 - interpreter and script are constants
        [sys.executable, str(CUTOVER_SCRIPT), *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _token(result: subprocess.CompletedProcess[str]) -> str:
    match = PLAN_TOKEN.search(result.stdout)
    assert match, result.stdout + result.stderr
    return match.group(1)


def test_the_estate_starts_out_refusing_migrations(foreign_owned_database: str) -> None:
    """Non-vacuity. If the fixture were already app_admin-owned, the cutover
    test below would pass while proving nothing."""
    with psycopg.connect(foreign_owned_database) as conn:
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(MIGRATION_EXECUTOR)))
        residual = {k: v for k, v in _residual(conn).items() if v}
    assert residual, "fixture must start with objects the executor does not own"
    assert residual.get("relation", 0) >= 5


def test_the_cutover_makes_every_generated_statement_execute(
    foreign_owned_database: str,
) -> None:
    """The point of doing this against a real server: every rendered statement
    must be VALID for its object kind, and correctly quoted."""
    with psycopg.connect(foreign_owned_database, autocommit=False) as conn:
        plan = conn.execute(
            OWNERSHIP_PLAN_SQL, {"target": MIGRATION_EXECUTOR}
        ).fetchall()
        assert plan, "plan must not be empty for a foreign-owned estate"
        kinds = {str(row[0]) for row in plan}
        assert {"database", "schema", "relation", "type", "routine"} <= kinds

        for row in plan:
            conn.execute(str(row[3]))
        conn.commit()

        # The post-condition, asserted with the migration preflight's own query
        # rather than by trusting the plan that just ran.
        conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(MIGRATION_EXECUTOR)))
        assert {k: v for k, v in _residual(conn).items() if v} == {}


def test_the_real_cli_is_database_and_review_bound_and_checks_as_app_admin(
    foreign_owned_database: str,
) -> None:
    database = _database_name(foreign_owned_database)
    owner = _source_owner(foreign_owned_database)

    wrong_database = _run_cli(
        foreign_owned_database,
        "--expected-database",
        f"wrong_{database}",
    )
    assert wrong_database.returncode == 1
    assert "expected database" in wrong_database.stderr

    reviewed = _run_cli(
        foreign_owned_database,
        "--expected-database",
        database,
    )
    assert reviewed.returncode == 0, reviewed.stderr

    # A same-owner object created after review must invalidate the exact plan,
    # even though --approve-owner still names that owner.
    with psycopg.connect(foreign_owned_database) as conn:
        conn.execute("CREATE TABLE arrived_after_review (id integer)")
    stale = _run_cli(
        foreign_owned_database,
        "--expected-database",
        database,
        "--execute",
        "--approve-owner",
        owner,
        "--plan-sha256",
        _token(reviewed),
    )
    assert stale.returncode == 1
    assert "reviewed plan" in stale.stderr
    assert _source_owner(foreign_owned_database) == owner

    current = _run_cli(
        foreign_owned_database,
        "--expected-database",
        database,
    )
    applied = _run_cli(
        foreign_owned_database,
        "--expected-database",
        database,
        "--execute",
        "--approve-owner",
        owner,
        "--plan-sha256",
        _token(current),
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert "post-check: the migration ownership inventory is empty" in applied.stdout
    assert _source_owner(foreign_owned_database) == MIGRATION_EXECUTOR


def test_a_second_run_is_a_no_op(foreign_owned_database: str) -> None:
    """Idempotence. An operator who re-runs after a partial review must not be
    told there is still work, nor have anything re-transferred."""
    with psycopg.connect(foreign_owned_database, autocommit=False) as conn:
        for row in conn.execute(
            OWNERSHIP_PLAN_SQL, {"target": MIGRATION_EXECUTOR}
        ).fetchall():
            conn.execute(str(row[3]))
        conn.commit()
        again = conn.execute(
            OWNERSHIP_PLAN_SQL, {"target": MIGRATION_EXECUTOR}
        ).fetchall()
    assert again == []


def test_extension_objects_are_never_transferred(foreign_owned_database: str) -> None:
    """Transferring an extension's objects breaks the extension. The exclusion
    is shared with the migration preflight, and this proves it holds against a
    real extension rather than by reading the SQL."""
    with psycopg.connect(foreign_owned_database, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    with psycopg.connect(foreign_owned_database, autocommit=False) as conn:
        plan = conn.execute(
            OWNERSHIP_PLAN_SQL, {"target": MIGRATION_EXECUTOR}
        ).fetchall()
    names = {str(row[2]) for row in plan}
    assert not any("gtrgm" in name or "similarity" in name for name in names), (
        f"extension-owned objects appear in the cutover plan: {sorted(names)}"
    )
