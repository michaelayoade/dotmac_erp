"""Keep stored payroll-run aggregates in parity with their salary slips."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.payroll.payroll_entry import PayrollEntry
from app.models.people.payroll.salary_slip import SalarySlip
from app.services.common import coerce_uuid


def recalculate_payroll_entry_totals(
    db: Session,
    organization_id: UUID,
    entry_id: UUID,
    *,
    entry: PayrollEntry | None = None,
) -> PayrollEntry:
    """Recalculate a payroll run from all of its persisted salary slips.

    Sessions deliberately disable autoflush. Flush before querying so a newly
    assigned final slip, or a pending edit/delete, participates in the totals.
    """
    org_id = coerce_uuid(organization_id)
    run_id = coerce_uuid(entry_id)

    if entry is None:
        entry = db.scalar(
            select(PayrollEntry).where(
                PayrollEntry.organization_id == org_id,
                PayrollEntry.entry_id == run_id,
            )
        )
    if not entry or entry.organization_id != org_id:
        raise ValueError("Payroll entry not found")

    db.flush()
    slips = list(
        db.scalars(
            select(SalarySlip).where(
                SalarySlip.organization_id == org_id,
                SalarySlip.payroll_entry_id == run_id,
            )
        ).all()
    )

    entry.employee_count = len(slips)
    entry.total_gross_pay = sum(
        ((slip.gross_pay or Decimal("0")) for slip in slips),
        Decimal("0"),
    )
    entry.total_deductions = sum(
        ((slip.total_deduction or Decimal("0")) for slip in slips),
        Decimal("0"),
    )
    entry.total_net_pay = sum(
        ((slip.net_pay or Decimal("0")) for slip in slips),
        Decimal("0"),
    )
    db.flush()
    return entry
