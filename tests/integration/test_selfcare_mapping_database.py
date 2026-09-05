"""Standalone PostgreSQL migration proof, also runnable with --noconftest.

Uses an explicitly supplied test server and a new disposable database. No app
imports or Selfcare client; tests the migration against the pre-change shape.
"""

from concurrent.futures import ThreadPoolExecutor
import os
import time
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from tests.migrations.test_selfcare_mapping_unique import load_migration


@pytest.fixture
def migration_database():
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Explicit TEST_DATABASE_URL required for real PostgreSQL proof")
    url = make_url(raw_url)
    if url.get_backend_name() != "postgresql" or not any(
        marker in (url.database or "").lower() for marker in ("test", "ci")
    ):
        pytest.fail("An explicitly named PostgreSQL test database is required")
    name = "selfcare_test_" + uuid4().hex
    admin = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    database = sa.create_engine(url.set(database=name))
    try:
        with database.begin() as connection:
            connection.execute(sa.text("CREATE SCHEMA hr"))
            connection.execute(
                sa.text("""
                CREATE TABLE hr.employee (
                    employee_id uuid PRIMARY KEY,
                    organization_id uuid NOT NULL,
                    dotmac_sub_account_id varchar(36),
                    status text NOT NULL DEFAULT 'ACTIVE'
                )
            """)
            )
        yield database
    finally:
        database.dispose()
        with admin.connect() as connection:
            assert name.startswith("selfcare_test_") and len(name) == 46
            connection.execute(sa.text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def _insert(connection, org, account=None, status="ACTIVE"):
    employee_id = uuid4()
    connection.execute(
        sa.text("""
        INSERT INTO hr.employee VALUES (:employee, :org, :account, :status)
    """),
        {"employee": employee_id, "org": org, "account": account, "status": status},
    )
    return employee_id


def _upgrade(connection):
    migration = load_migration()
    migration.op = Operations(MigrationContext.configure(connection))
    migration.upgrade()


def test_postgres_migration_refuses_duplicates_without_changing_rows(
    migration_database,
):
    org, account = uuid4(), str(uuid4())
    with migration_database.begin() as connection:
        first = _insert(connection, org, account)
        second = _insert(connection, org, account, "TERMINATED")
    with (
        pytest.raises(RuntimeError, match="No data was changed"),
        migration_database.begin() as connection,
    ):
        _upgrade(connection)
    with migration_database.connect() as connection:
        assert set(
            connection.execute(
                sa.text("SELECT employee_id, dotmac_sub_account_id FROM hr.employee")
            )
        ) == {(first, account), (second, account)}


def test_postgres_migration_allows_nulls_and_scopes_unique_constraint(
    migration_database,
):
    org, account = uuid4(), str(uuid4())
    with migration_database.begin() as connection:
        _insert(connection, org)
        _insert(connection, org)
        _insert(connection, org, account, "TERMINATED")
        _insert(connection, uuid4(), account)
        _upgrade(connection)
    with (
        pytest.raises(IntegrityError) as error,
        migration_database.begin() as connection,
    ):
        _insert(connection, org, account)
    assert error.value.orig.diag.constraint_name == "uq_employee_org_selfcare_account"


@pytest.mark.parametrize("commit_owner", [True, False])
def test_postgres_concurrent_unique_claims(migration_database, commit_owner):
    from queue import Queue

    org, account = uuid4(), str(uuid4())
    with migration_database.begin() as connection:
        _upgrade(connection)
    pids = Queue()

    def contender():
        try:
            with migration_database.begin() as connection:
                pids.put(connection.scalar(sa.text("SELECT pg_backend_pid()")))
                _insert(connection, org, account)
            return "claimed"
        except IntegrityError as exc:
            assert exc.orig.diag.constraint_name == "uq_employee_org_selfcare_account"
            return "conflict"

    with (
        migration_database.connect() as first,
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        transaction = first.begin()
        _insert(first, org, account)
        future = pool.submit(contender)
        try:
            pid = pids.get(timeout=10)
            deadline = time.monotonic() + 10
            with migration_database.connect() as observer:
                while not observer.scalar(
                    sa.text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                    {"pid": pid},
                ):
                    assert time.monotonic() < deadline, "Contender did not block"
                    time.sleep(0.01)
        finally:
            if commit_owner:
                transaction.commit()
            else:
                transaction.rollback()
        assert future.result(timeout=10) == ("conflict" if commit_owner else "claimed")
    with migration_database.connect() as connection:
        assert connection.scalar(sa.text("SELECT count(*) FROM hr.employee")) == 1
