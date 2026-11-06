"""Adapt persisted ERP payroll facts to the pure payroll calculation contract.

ERP remains the writer. Never pass live ORM amount attributes to aggregation:
flush pending links/edits/deletes, then select scalar columns so PostgreSQL's
stored Numeric precision is authoritative even with autoflush=False.
"""

from uuid import UUID

from dotmac_payroll import (
    PayrollAmountSnapshot,
    PayrollRunTotals,
    aggregate_payroll_run_totals,
    validate_payment_total,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.payroll.payroll_entry import PayrollEntry
from app.models.people.payroll.salary_slip import SalarySlip
from app.services.finance.banking.bank_upload import PaymentItem
from app.services.finance.money_boundary import SUPPORTED_CURRENCIES


def lock_run(db: Session, entry: PayrollEntry) -> None:
    """Serialize one tenant/run before flushing child changes or reading totals.

    Keep the lock until the caller commits/rolls back. Read Committed's next
    scalar statement then sees the preceding holder's committed slip edits.
    Refresh the derived amounts after waiting: SQLAlchemy must compare new
    totals with the preceding holder's committed values, not a stale cached
    header. Pending lifecycle state and generation flags remain untouched.
    """
    with db.no_autoflush:
        locked_id = db.scalar(
            select(PayrollEntry.entry_id)
            .where(
                PayrollEntry.organization_id == entry.organization_id,
                PayrollEntry.entry_id == entry.entry_id,
            )
            .with_for_update()
        )
        if locked_id is None:
            raise ValueError("Payroll run not found")
        db.refresh(
            entry,
            attribute_names=[
                "currency_code",
                "employee_count",
                "total_gross_pay",
                "total_deductions",
                "total_net_pay",
            ],
        )


def refresh_run_totals(db: Session, entry: PayrollEntry) -> PayrollRunTotals:
    """Map module arithmetic back to the existing ERP header, without commit."""
    lock_run(db, entry)
    db.flush()
    currency = SUPPORTED_CURRENCIES.resolve(entry.currency_code)
    rows = db.execute(
        select(
            SalarySlip.slip_id,
            SalarySlip.currency_code,
            SalarySlip.gross_pay,
            SalarySlip.total_deduction,
            SalarySlip.net_pay,
        ).where(
            SalarySlip.organization_id == entry.organization_id,
            SalarySlip.payroll_entry_id == entry.entry_id,
        )
    ).all()
    totals = aggregate_payroll_run_totals(
        currency=currency,
        snapshots=(
            PayrollAmountSnapshot(
                calculation_ref=str(row.slip_id),
                currency=SUPPORTED_CURRENCIES.resolve(row.currency_code),
                gross_amount=row.gross_pay,
                deduction_amount=row.total_deduction,
                net_amount=row.net_pay,
            )
            for row in rows
        ),
    )
    entry.employee_count = totals.calculation_count
    entry.total_gross_pay = totals.gross_amount
    entry.total_deductions = totals.deduction_amount
    entry.total_net_pay = totals.net_amount
    return totals


def lock_linked_run(
    db: Session, organization_id: UUID, entry_id: UUID | None
) -> PayrollEntry | None:
    """Take the parent lock before changing a linked draft slip."""
    if entry_id is None:
        return None
    with db.no_autoflush:
        entry = db.scalar(
            select(PayrollEntry).where(
                PayrollEntry.organization_id == organization_id,
                PayrollEntry.entry_id == entry_id,
            )
        )
    if entry is not None:
        lock_run(db, entry)
    return entry


def refresh_linked_run_totals(
    db: Session, organization_id: UUID, entry_id: UUID | None
) -> None:
    """Refresh a linked header after a draft edit or delete."""
    entry = lock_linked_run(db, organization_id, entry_id)
    if entry is not None:
        refresh_run_totals(db, entry)


def validate_run_payment_items(
    db: Session, entry: PayrollEntry, items: list[PaymentItem]
) -> None:
    """Refuse export when net payments differ from the persisted authorized header.

    This is a read/validation boundary. It never repairs an approved header,
    records settlement, or sends components into the bank command.
    """
    db.flush()
    row = db.execute(
        select(
            PayrollEntry.currency_code,
            PayrollEntry.employee_count,
            PayrollEntry.total_gross_pay,
            PayrollEntry.total_deductions,
            PayrollEntry.total_net_pay,
        ).where(
            PayrollEntry.organization_id == entry.organization_id,
            PayrollEntry.entry_id == entry.entry_id,
        )
    ).one()
    currency = SUPPORTED_CURRENCIES.resolve(row.currency_code)
    validate_payment_total(
        run_totals=PayrollRunTotals(
            currency=currency,
            calculation_count=row.employee_count,
            gross_amount=row.total_gross_pay,
            deduction_amount=row.total_deductions,
            net_amount=row.total_net_pay,
        ),
        currency=currency,
        payment_amounts=(item.amount for item in items),
    )
