"""Shared payroll reporting calculations."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from decimal import Decimal

from app.models.people.payroll.salary_slip import SalarySlipStatus

REPORTABLE_SLIP_STATUSES: tuple[SalarySlipStatus, ...] = (
    SalarySlipStatus.APPROVED,
    SalarySlipStatus.POSTED,
)


def _to_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str | bytes | bytearray):
        return int(value)
    if isinstance(value, Decimal):
        return int(value)
    return 0


def _to_decimal(value: object) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | str):
        return Decimal(value)
    return Decimal(str(value))


def calculate_active_monthly_net_average(
    months: Iterable[Mapping[str, object]],
) -> Decimal:
    """Average net pay across months where payroll actually ran."""
    active_months = [m for m in months if _to_int(m["slip_count"]) > 0]
    if not active_months:
        return Decimal("0")

    total_net = sum(
        (_to_decimal(m["total_net"]) for m in active_months),
        Decimal("0"),
    )
    return total_net / len(active_months)
