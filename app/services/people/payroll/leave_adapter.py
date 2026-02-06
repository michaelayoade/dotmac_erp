"""
Leave Payroll Adapter.

Bridges the Leave module with Payroll for:
- Calculating Leave Without Pay (LWP) days for salary deductions
- Getting leave summary for payroll display
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.leave import (
    LeaveApplication,
    LeaveApplicationStatus,
    LeaveType,
)

logger = logging.getLogger(__name__)


@dataclass
class LeaveSummary:
    """Leave summary for an employee in a payroll period."""

    employee_id: UUID
    period_start: date
    period_end: date
    total_leave_days: Decimal
    lwp_days: Decimal
    paid_leave_days: Decimal
    by_type: list[dict]  # [{"type": "Annual Leave", "days": 5, "is_lwp": False}, ...]


class LeavePayrollAdapter:
    """
    Adapter for integrating Leave data into Payroll calculations.

    Retrieves approved leave applications and calculates LWP deductions
    for salary slip generation.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_lwp_days(
        self,
        employee_id: UUID,
        period_start: date,
        period_end: date,
    ) -> Decimal:
        """
        Get total Leave Without Pay days for employee in period.

        Only counts:
        - LeaveType.is_lwp = True
        - LeaveApplication.status = APPROVED
        - Leave dates overlapping with pay period

        Handles partial overlaps (leave spanning multiple pay periods).

        Args:
            employee_id: Employee UUID
            period_start: Payroll period start date
            period_end: Payroll period end date

        Returns:
            Total LWP days as Decimal
        """
        # Get approved LWP applications overlapping with period
        stmt = (
            select(LeaveApplication)
            .join(LeaveType, LeaveApplication.leave_type_id == LeaveType.leave_type_id)
            .where(
                LeaveApplication.employee_id == employee_id,
                LeaveApplication.status == LeaveApplicationStatus.APPROVED,
                LeaveType.is_lwp == True,
                # Overlaps with period
                LeaveApplication.from_date <= period_end,
                LeaveApplication.to_date >= period_start,
            )
        )

        applications = self.db.scalars(stmt).all()

        total_lwp_days = Decimal("0")

        for app in applications:
            # Calculate overlap with pay period
            overlap_start = max(app.from_date, period_start)
            overlap_end = min(app.to_date, period_end)

            # Count days in overlap (inclusive)
            overlap_days = (overlap_end - overlap_start).days + 1

            # Adjust for half-day leaves if applicable
            if hasattr(app, "half_day") and app.half_day and overlap_days == 1:
                total_lwp_days += Decimal("0.5")
            else:
                total_lwp_days += Decimal(str(overlap_days))

        return total_lwp_days

    def get_leave_summary(
        self,
        employee_id: UUID,
        period_start: date,
        period_end: date,
    ) -> LeaveSummary:
        """
        Get detailed leave summary for payroll display.

        Returns breakdown by leave type including both paid and unpaid leave.

        Args:
            employee_id: Employee UUID
            period_start: Payroll period start date
            period_end: Payroll period end date

        Returns:
            LeaveSummary with breakdown by type
        """
        # Get all approved leaves overlapping with period
        stmt = (
            select(
                LeaveType.leave_type_name.label("type_name"),
                LeaveType.is_lwp,
                LeaveApplication.from_date,
                LeaveApplication.to_date,
                LeaveApplication.total_leave_days,
            )
            .join(LeaveType, LeaveApplication.leave_type_id == LeaveType.leave_type_id)
            .where(
                LeaveApplication.employee_id == employee_id,
                LeaveApplication.status == LeaveApplicationStatus.APPROVED,
                LeaveApplication.from_date <= period_end,
                LeaveApplication.to_date >= period_start,
            )
        )

        results = self.db.execute(stmt).all()

        by_type: dict[str, dict] = {}
        total_days = Decimal("0")
        lwp_days = Decimal("0")
        paid_days = Decimal("0")

        for row in results:
            # Calculate overlap days
            overlap_start = max(row.from_date, period_start)
            overlap_end = min(row.to_date, period_end)
            overlap_days = Decimal(str((overlap_end - overlap_start).days + 1))

            # Aggregate by type
            type_name = row.type_name
            if type_name not in by_type:
                by_type[type_name] = {
                    "type": type_name,
                    "days": Decimal("0"),
                    "is_lwp": row.is_lwp,
                }
            by_type[type_name]["days"] += overlap_days

            total_days += overlap_days
            if row.is_lwp:
                lwp_days += overlap_days
            else:
                paid_days += overlap_days

        return LeaveSummary(
            employee_id=employee_id,
            period_start=period_start,
            period_end=period_end,
            total_leave_days=total_days,
            lwp_days=lwp_days,
            paid_leave_days=paid_days,
            by_type=list(by_type.values()),
        )

    def get_bulk_lwp_days(
        self,
        employee_ids: list[UUID],
        period_start: date,
        period_end: date,
    ) -> dict[UUID, Decimal]:
        """
        Get LWP days for multiple employees at once.

        More efficient than calling get_lwp_days() for each employee.

        Args:
            employee_ids: List of employee UUIDs
            period_start: Payroll period start date
            period_end: Payroll period end date

        Returns:
            Dict mapping employee_id to LWP days
        """
        if not employee_ids:
            return {}

        # Get all LWP applications for these employees
        stmt = (
            select(
                LeaveApplication.employee_id,
                LeaveApplication.from_date,
                LeaveApplication.to_date,
            )
            .join(LeaveType, LeaveApplication.leave_type_id == LeaveType.leave_type_id)
            .where(
                LeaveApplication.employee_id.in_(employee_ids),
                LeaveApplication.status == LeaveApplicationStatus.APPROVED,
                LeaveType.is_lwp == True,
                LeaveApplication.from_date <= period_end,
                LeaveApplication.to_date >= period_start,
            )
        )

        results = self.db.execute(stmt).all()

        # Aggregate by employee
        lwp_by_emp: dict[UUID, Decimal] = {
            emp_id: Decimal("0") for emp_id in employee_ids
        }

        for row in results:
            overlap_start = max(row.from_date, period_start)
            overlap_end = min(row.to_date, period_end)
            overlap_days = Decimal(str((overlap_end - overlap_start).days + 1))
            lwp_by_emp[row.employee_id] += overlap_days

        return lwp_by_emp


def leave_payroll_adapter(db: Session) -> LeavePayrollAdapter:
    """Create a LeavePayrollAdapter instance."""
    return LeavePayrollAdapter(db)
