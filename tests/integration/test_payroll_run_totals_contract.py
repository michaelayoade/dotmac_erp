"""Hosted PostgreSQL proofs for the payroll contract consumer.

Selected by the integration lane, with no fallback to DATABASE_URL or SQLite.
Each test owns a disposable schema; concurrency requires committed rows visible
across two independent sessions. No ERP/module payroll storage is touched.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import Numeric, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.schema import CreateSchema, DropSchema

from app.services.people.payroll import run_totals


class Base(DeclarativeBase):
    pass


class Entry(Base):
    __tablename__ = "entry"
    __table_args__ = {"schema": "payroll_totals_canary"}
    entry_id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(String)
    currency_code: Mapped[str] = mapped_column(String, default="NGN")
    employee_count: Mapped[int] = mapped_column(default=0)
    total_gross_pay: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    total_deductions: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    total_net_pay: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)


class Slip(Base):
    __tablename__ = "slip"
    __table_args__ = {"schema": "payroll_totals_canary"}
    slip_id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(String)
    payroll_entry_id: Mapped[str | None] = mapped_column(String)
    currency_code: Mapped[str] = mapped_column(String, default="NGN")
    gross_pay: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    total_deduction: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    net_pay: Mapped[Decimal] = mapped_column(Numeric(18, 2))


@pytest.fixture
def payroll_engine(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgresql+")):
        pytest.fail("payroll integration proof requires hosted TEST_DATABASE_URL")
    schema = f"payroll_totals_{uuid4().hex}"
    engine = create_engine(url).execution_options(
        schema_translate_map={"payroll_totals_canary": schema}
    )
    monkeypatch.setattr(run_totals, "PayrollEntry", Entry)
    monkeypatch.setattr(run_totals, "SalarySlip", Slip)
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
            Base.metadata.create_all(connection)
        yield engine
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
        engine.dispose()


def new_entry(db):
    run = Entry(entry_id=str(uuid4()), organization_id=str(uuid4()))
    db.add(run)
    db.flush()
    return run


def new_slip(db, run, net="75.00", org=None):
    record = Slip(
        slip_id=str(uuid4()),
        organization_id=org or run.organization_id,
        payroll_entry_id=run.entry_id,
        gross_pay=Decimal("100.00"),
        total_deduction=Decimal("25.00"),
        net_pay=Decimal(net),
    )
    db.add(record)
    return record


def test_postgresql_stored_precision_last_link_and_scoped_mutations(payroll_engine):
    with Session(payroll_engine, autoflush=False, expire_on_commit=False) as db:
        run = new_entry(db)
        record = new_slip(db, run, net="75.005")
        record.payroll_entry_id = None
        db.flush()
        record.payroll_entry_id = run.entry_id
        new_slip(db, run, net="999.00", org=str(uuid4()))
        run_totals.refresh_run_totals(db, run)
        assert record.net_pay == Decimal("75.005")
        assert db.scalar(
            select(Slip.net_pay).where(Slip.slip_id == record.slip_id)
        ) == Decimal("75.01")
        assert run.employee_count == 1
        assert run.total_net_pay == Decimal("75.01")
        record.net_pay = Decimal("80.005")
        run_totals.refresh_linked_run_totals(db, run.organization_id, run.entry_id)
        assert run.total_net_pay == Decimal("80.01")
        db.delete(record)
        run_totals.refresh_linked_run_totals(db, run.organization_id, run.entry_id)
        assert run.employee_count == 0
        assert run.total_net_pay == Decimal("0.00")


@pytest.mark.parametrize(
    ("second_net", "expected_net"), [("90.00", "170.00"), ("70.00", "150.00")]
)
def test_concurrent_distinct_slip_edits_serialize_before_totals_read(
    payroll_engine, second_net: str, expected_net: str
):
    with Session(payroll_engine, expire_on_commit=False) as seed:
        run = new_entry(seed)
        first, second = new_slip(seed, run), new_slip(seed, run)
        run_totals.refresh_run_totals(seed, run)
        seed.commit()
        run_id, first_id, second_id = run.entry_id, first.slip_id, second.slip_id

    started = Event()
    worker_pid = []

    def edit_second():
        with Session(payroll_engine, autoflush=False) as db:
            worker_pid.append(db.scalar(select(func.pg_backend_pid())))
            other_run = db.get(Entry, run_id)
            db.get(Slip, second_id).net_pay = Decimal(second_net)
            started.set()
            run_totals.refresh_run_totals(db, other_run)
            db.commit()

    with Session(payroll_engine, autoflush=False) as first_db:
        first_pid = first_db.scalar(select(func.pg_backend_pid()))
        first_run = first_db.get(Entry, run_id)
        first_db.get(Slip, first_id).net_pay = Decimal("80.00")
        run_totals.refresh_run_totals(first_db, first_run)
        assert first_run.total_net_pay == Decimal("155.00")
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(edit_second)
            try:
                assert started.wait(5), "second session did not start"
                deadline = time.monotonic() + 5
                with payroll_engine.connect() as observer:
                    while time.monotonic() < deadline:
                        blockers = observer.scalar(
                            select(func.pg_blocking_pids(worker_pid[0]))
                        )
                        if first_pid in blockers:
                            break
                        if future.done():
                            future.result()  # surface errors instead of masking them
                            pytest.fail(
                                "second edit completed without waiting for the run lock"
                            )
                        time.sleep(0.01)
                    else:
                        pytest.fail("second session never waited for the run lock")
                assert not future.done()
            finally:
                # Release the holder even when the blocking proof fails.
                first_db.commit()
            future.result(timeout=10)
    with Session(payroll_engine) as verify:
        stored = verify.get(Entry, run_id)
        assert stored.employee_count == 2
        assert stored.total_net_pay == Decimal(expected_net)
        assert stored.total_net_pay == verify.scalar(select(func.sum(Slip.net_pay)))
