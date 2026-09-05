"""Real PostgreSQL ownership, races, savepoints and duplicate-data migration.

Requires explicit TEST_DATABASE_URL pointing at a disposable test/CI server,
with CREATEDB permission. Never falls back to the application's DATABASE_URL.
Each test gets its own database and the real Employee/Person columns and unique
constraints. Unrelated foreign keys are omitted to avoid bootstrapping the ERP
assembly; this is a focused ownership test, not a full migration-chain test.
Selfcare is always a fake client.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date
import os
import time
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session_context import prime_tenant_context
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person
from app.services.dotmac_sub import staff_sync
from tests.migrations.test_selfcare_mapping_unique import load_migration
from tests.services.test_staff_sync import FakeClient


@pytest.fixture
def mapping_engine(engine, monkeypatch):
    # `engine` explicitly restores native PostgreSQL types patched by the root
    # conftest. We do not connect to it or rely on collection order for repair.
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Explicit TEST_DATABASE_URL is required")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or not any(
        marker in (parsed.database or "").lower() for marker in ("test", "ci")
    ):
        pytest.fail(
            "Selfcare tests require an explicitly named PostgreSQL test database"
        )
    name = "selfcare_test_" + uuid4().hex
    admin = sa.create_engine(parsed, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    isolated = sa.create_engine(parsed.set(database=name))
    try:
        with isolated.begin() as connection:
            connection.execute(sa.schema.CreateSchema("hr"))
            for source in (Person.__table__, Employee.__table__):
                table = source.to_metadata(sa.MetaData())
                for constraint in list(table.constraints):
                    if isinstance(constraint, sa.ForeignKeyConstraint):
                        table.constraints.remove(constraint)
                table.foreign_keys.clear()
                for column in table.columns:
                    column.foreign_keys.clear()
                table.create(connection)
        monkeypatch.setattr(
            settings, "dotmac_sub_staff_sync_enabled", True, raising=False
        )
        monkeypatch.setattr(
            settings, "dotmac_sub_staff_default_role", "staff", raising=False
        )
        monkeypatch.setattr(
            staff_sync, "_refresh_staff_access_projection", lambda *a: None
        )
        yield isolated
    finally:
        isolated.dispose()
        with admin.connect() as connection:
            # The generated name is never derived from a configured database.
            assert name.startswith("selfcare_test_") and len(name) == 46
            connection.execute(sa.text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def employee(db, org_id, *, account_id=None, status=EmployeeStatus.ACTIVE):
    prime_tenant_context(db, org_id)
    person = Person(
        organization_id=org_id,
        first_name="Synthetic",
        last_name="Employee",
        email=f"{uuid4()}@example.test",
    )
    record = Employee(
        organization_id=org_id,
        person=person,
        employee_code=uuid4().hex[:20],
        date_of_joining=date(2026, 1, 1),
        status=status,
        dotmac_sub_account_id=account_id,
        dotmac_sub_access_enabled=True,
    )
    db.add(record)
    db.flush()
    return record


def test_nulls_cross_org_and_own_mapping_are_allowed(mapping_engine):
    account_id = str(uuid4())
    with Session(mapping_engine) as db:
        org = uuid4()
        employee(db, org)
        employee(db, org)
        owner = employee(db, org, account_id=account_id)
        assert staff_sync._claim_account(db, owner, account_id) == account_id
        client = FakeClient(existing={"id": account_id, "is_active": True})
        assert staff_sync.sync_employee(db, owner, client=client)["action"] == "noop"
        assert len(client.role_calls) == 1
        employee(db, uuid4(), account_id=account_id)
        db.commit()


@pytest.mark.parametrize("status", [EmployeeStatus.ACTIVE, EmployeeStatus.TERMINATED])
def test_database_rejects_duplicate_without_service_precheck(mapping_engine, status):
    with Session(mapping_engine) as db:
        org, account_id = uuid4(), str(uuid4())
        employee(db, org, account_id=account_id, status=status)
        with pytest.raises(IntegrityError) as error, db.begin_nested():
            employee(db, org, account_id=account_id)
        assert (
            error.value.orig.diag.constraint_name == "uq_employee_org_selfcare_account"
        )
        assert (
            db.scalar(
                sa.select(sa.func.count())
                .select_from(Employee)
                .where(Employee.organization_id == org)
            )
            == 1
        )


@pytest.mark.parametrize("status", [EmployeeStatus.ACTIVE, EmployeeStatus.TERMINATED])
def test_inactive_ownership_never_transfers_or_mutates(mapping_engine, status):
    org, account_id = uuid4(), str(uuid4())
    with Session(mapping_engine) as db:
        employee(db, org, account_id=account_id, status=EmployeeStatus.TERMINATED)
        claimant = employee(db, org, status=status)
        client = FakeClient(existing={"id": account_id, "is_active": True})
        with pytest.raises(staff_sync.SelfcareMappingConflict):
            staff_sync.sync_employee(db, claimant, client=client)
        assert claimant.dotmac_sub_account_id is None
        assert claimant.dotmac_sub_staff_synced_at is None
        assert (
            not client.role_calls
            and not client.department_calls
            and not client.active_calls
        )
        # The savepoint left the outer transaction usable.
        db.commit()


def _wait_until_blocked(engine, pid):
    deadline = time.monotonic() + 10
    with engine.connect() as connection:
        while time.monotonic() < deadline:
            if connection.scalar(
                sa.text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
            ):
                return
            time.sleep(0.01)
    pytest.fail("Contender never reached a PostgreSQL lock wait")


@pytest.mark.parametrize("cooperative", [True, False])
def test_concurrent_claim_loser_cannot_mutate_selfcare(mapping_engine, cooperative):
    from queue import Queue

    org, account_id = uuid4(), str(uuid4())
    with Session(mapping_engine) as setup:
        first_id = employee(setup, org).employee_id
        second_id = employee(setup, org, status=EmployeeStatus.TERMINATED).employee_id
        setup.commit()
    client = FakeClient(existing={"id": account_id, "is_active": True})
    pids = Queue()

    def contender():
        with Session(mapping_engine) as db:
            prime_tenant_context(db, org)
            pids.put(db.scalar(sa.text("SELECT pg_backend_pid()")))
            candidate = db.get(Employee, second_id)
            with pytest.raises(staff_sync.SelfcareMappingConflict):
                staff_sync.sync_employee(db, candidate, client=client)
            assert candidate.dotmac_sub_account_id is None
            assert candidate.dotmac_sub_staff_synced_at is None
            db.commit()

    with Session(mapping_engine) as first, ThreadPoolExecutor(max_workers=1) as pool:
        prime_tenant_context(first, org)
        owner = first.get(Employee, first_id)
        if cooperative:
            staff_sync.sync_employee(
                first,
                owner,
                client=FakeClient(existing={"id": account_id, "is_active": True}),
            )
        else:
            # An independent writer bypasses both the advisory lock and check.
            owner.dotmac_sub_account_id = account_id
            first.flush()
        future = pool.submit(contender)
        try:
            _wait_until_blocked(mapping_engine, pids.get(timeout=10))
            assert (
                not client.role_calls
                and not client.department_calls
                and not client.active_calls
            )
        finally:
            # Always release locks, even if an assertion fails.
            first.commit()
        future.result(timeout=10)
    assert (
        not client.role_calls
        and not client.department_calls
        and not client.active_calls
    )
    with mapping_engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM hr.employee WHERE organization_id = :org "
                    "AND dotmac_sub_account_id = :account"
                ),
                {"org": org, "account": account_id},
            )
            == 1
        )


def test_failed_remote_sync_rolls_back_mapping_and_timestamp(
    mapping_engine, monkeypatch
):
    org, account_id = uuid4(), str(uuid4())
    with Session(mapping_engine) as db:
        record = employee(db, org)
        client = FakeClient(existing={"id": account_id, "is_active": True})

        def fail_projection(*args):
            raise RuntimeError("synthetic projection failure")

        monkeypatch.setattr(
            staff_sync, "_refresh_staff_access_projection", fail_projection
        )
        with pytest.raises(RuntimeError, match="projection failure"):
            staff_sync.sync_employee(db, record, client=client)
        assert record.dotmac_sub_account_id is None
        assert record.dotmac_sub_staff_synced_at is None
        db.commit()


def test_migration_refuses_legacy_duplicates_and_preserves_rows(mapping_engine):
    migration = load_migration()
    with mapping_engine.begin() as connection:
        migration.op = Operations(MigrationContext.configure(connection))
        migration.downgrade()
    org, account_id = uuid4(), str(uuid4())
    with Session(mapping_engine) as db:
        first_id = employee(db, org, account_id=account_id).employee_id
        second_id = employee(
            db, org, account_id=account_id, status=EmployeeStatus.TERMINATED
        ).employee_id
        db.commit()
    with (
        pytest.raises(RuntimeError, match="No data was changed"),
        mapping_engine.begin() as connection,
    ):
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
    with mapping_engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT employee_id, dotmac_sub_account_id FROM hr.employee "
                "WHERE organization_id = :org"
            ),
            {"org": org},
        ).all()
        assert set(rows) == {(first_id, account_id), (second_id, account_id)}
    # A simulated, explicitly reviewed correction of synthetic test data.
    with mapping_engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE hr.employee SET dotmac_sub_account_id = NULL "
                "WHERE organization_id = :org AND employee_id = :employee"
            ),
            {"org": org, "employee": second_id},
        )
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
    with pytest.raises(IntegrityError), mapping_engine.begin() as connection:
        connection.execute(
            sa.text(
                "UPDATE hr.employee SET dotmac_sub_account_id = :account "
                "WHERE organization_id = :org AND employee_id = :employee"
            ),
            {"org": org, "employee": second_id, "account": account_id},
        )
