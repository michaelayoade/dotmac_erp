"""Causal canaries for ERP's persisted-fact payroll adapter.

These use a real autoflush=False SQLAlchemy session with isolated projections
of the ERP columns. The contract is not mocked. Execute in hosted CI after the
released payroll a2 pin is installed. PostgreSQL precision still needs the
hosted PostgreSQL lane; SQLite does not reproduce PostgreSQL write rounding.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_payroll import PayrollTotalsViolation
from sqlalchemy import Numeric, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.services.finance.banking.bank_upload import PaymentItem
from app.services.people.payroll import run_totals


class Base(DeclarativeBase):
    pass


class Entry(Base):
    __tablename__ = "entry"
    __table_args__ = {"prefixes": ["TEMPORARY"]}
    entry_id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(String)
    currency_code: Mapped[str] = mapped_column(String, default="NGN")
    employee_count: Mapped[int] = mapped_column(default=0)
    total_gross_pay: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    total_deductions: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    total_net_pay: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)


class Slip(Base):
    __tablename__ = "slip"
    __table_args__ = {"prefixes": ["TEMPORARY"]}
    slip_id: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[str] = mapped_column(String)
    payroll_entry_id: Mapped[str | None] = mapped_column(String)
    currency_code: Mapped[str] = mapped_column(String, default="NGN")
    gross_pay: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    total_deduction: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    net_pay: Mapped[Decimal] = mapped_column(Numeric(18, 2))


@pytest.fixture
def session(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(run_totals, "PayrollEntry", Entry)
    monkeypatch.setattr(run_totals, "SalarySlip", Slip)
    with Session(engine, autoflush=False, expire_on_commit=False) as db:
        yield db
    engine.dispose()


def entry(db):
    record = Entry(entry_id=str(uuid4()), organization_id=str(uuid4()))
    db.add(record)
    db.flush()
    return record


def slip(db, run, *, net="75.00", org=None):
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


def test_last_pending_link_is_included_with_autoflush_disabled(session):
    run = entry(session)
    first = slip(session, run)
    last = slip(session, run)
    last.payroll_entry_id = None
    session.flush()
    # Reproduce generation: create_salary_slip flushes before caller links it.
    last.payroll_entry_id = run.entry_id
    run_totals.refresh_run_totals(session, run)
    assert session.autoflush is False
    assert run.employee_count == 2
    assert run.total_net_pay == Decimal("150.00")
    assert first in session


def test_pending_draft_edit_and_delete_refresh_linked_header(session):
    run = entry(session)
    first = slip(session, run)
    last = slip(session, run)
    run_totals.refresh_run_totals(session, run)
    first.net_pay = Decimal("80.00")
    session.delete(last)
    run_totals.refresh_linked_run_totals(session, run.organization_id, run.entry_id)
    assert run.employee_count == 1
    assert run.total_net_pay == Decimal("80.00")
    session.delete(first)
    run_totals.refresh_linked_run_totals(session, run.organization_id, run.entry_id)
    assert run.employee_count == 0
    assert run.total_gross_pay == run.total_deductions == run.total_net_pay == 0


def test_scoped_read_excludes_another_tenant_even_if_link_matches(session):
    run = entry(session)
    slip(session, run)
    slip(session, run, net="999.00", org=str(uuid4()))
    run_totals.refresh_run_totals(session, run)
    assert run.employee_count == 1
    assert run.total_net_pay == Decimal("75.00")


def test_scalar_projection_bypasses_live_unrounded_identity_map(session):
    run = entry(session)
    record = slip(session, run, net="75.004")
    run_totals.refresh_run_totals(session, run)
    # ORM state keeps the Python input even after flush. Scalar Numeric output
    # represents the persisted scale; the adapter must not aggregate record.
    assert record.net_pay == Decimal("75.004")
    assert session.scalar(select(Slip.net_pay)) == Decimal("75.00")
    assert run.total_net_pay == Decimal("75.00")


def payment(amount):
    return PaymentItem(
        reference="SAL-test",
        beneficiary_name="Private Employee",
        amount=Decimal(amount),
        account_number="0123456789",
        bank_name="Private Bank",
    )


def test_export_mismatch_refuses_without_amount_or_identity_disclosure(session, caplog):
    run = entry(session)
    slip(session, run)
    run_totals.refresh_run_totals(session, run)
    with pytest.raises(PayrollTotalsViolation) as raised:
        run_totals.validate_run_payment_items(session, run, [payment("74.00")])
    evidence = str(raised.value) + caplog.text
    for private in ("74.00", "75.00", "Private Employee", "0123456789"):
        assert private not in evidence
    assert run.total_net_pay == Decimal("75.00")
    assert set(Base.metadata.tables) == {"entry", "slip"}


def test_payment_parity_is_net_only_and_does_not_recalculate_salary(session):
    run = entry(session)
    slip(session, run, net="70.00")  # deliberately independent of 100 - 25
    run_totals.refresh_run_totals(session, run)
    run_totals.validate_run_payment_items(session, run, [payment("70.00")])
    assert run.total_gross_pay == Decimal("100.00")
    assert run.total_deductions == Decimal("25.00")
    assert run.total_net_pay == Decimal("70.00")
