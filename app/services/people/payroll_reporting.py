"""Shared payroll reporting calculations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal

from app.models.people.payroll.salary_slip import SalarySlipStatus

REPORTABLE_SLIP_STATUSES: tuple[SalarySlipStatus, ...] = (
    SalarySlipStatus.APPROVED,
    SalarySlipStatus.POSTED,
)


def calculate_active_monthly_net_average(
    months: Iterable[Mapping[str, object]],
) -> Decimal:
    """Average net pay across months where payroll actually ran."""
    active_months = [m for m in months if int(m["slip_count"] or 0) > 0]
    if not active_months:
        return Decimal("0")

    total_net = sum(
        (Decimal(str(m["total_net"] or 0)) for m in active_months),
        Decimal("0"),
    )
    return total_net / len(active_months)
