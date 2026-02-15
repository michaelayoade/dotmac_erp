"""
WorkforceComputer — produces HR and workforce metrics.

Metrics:
    workforce.active_headcount        Count of ACTIVE employees
    workforce.turnover_30d            Employees who left in last 30 days
    workforce.leave_utilization_30d   Leave days consumed in last 30 days
    workforce.attendance_rate_30d     Present-rate percentage (last 30 days)
    workforce.pending_leave_approvals Count of SUBMITTED leave applications
    workforce.department_distribution JSON: [{dept, count}, ...]
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select

from app.services.analytics.base_computer import BaseComputer

logger = logging.getLogger(__name__)

# Statuses that count as "left"
_LEFT_STATUSES_NAMES = ("RESIGNED", "TERMINATED", "RETIRED")


class WorkforceComputer(BaseComputer):
    """Compute HR and workforce KPIs for an organization."""

    METRIC_TYPES = [
        "workforce.active_headcount",
        "workforce.turnover_30d",
        "workforce.leave_utilization_30d",
        "workforce.attendance_rate_30d",
        "workforce.pending_leave_approvals",
        "workforce.department_distribution",
    ]
    SOURCE_LABEL = "WorkforceComputer"

    def compute_for_org(
        self,
        organization_id: UUID,
        snapshot_date: date,
    ) -> int:
        """Compute all workforce metrics for a single org. Returns count written."""
        from app.models.people.attendance.attendance import (
            Attendance,
            AttendanceStatus,
        )
        from app.models.people.hr.employee import Employee, EmployeeStatus
        from app.models.people.leave.leave_application import (
            LeaveApplication,
            LeaveApplicationStatus,
        )

        written = 0

        # ── 1. Active headcount ────────────────────────────────────
        headcount_stmt = select(func.count(Employee.employee_id)).where(
            Employee.organization_id == organization_id,
            Employee.status == EmployeeStatus.ACTIVE,
        )
        headcount = int(self.db.scalar(headcount_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="workforce.active_headcount",
            snapshot_date=snapshot_date,
            value_numeric=headcount,
        )
        written += 1

        # ── 2. Turnover (last 30 days) ─────────────────────────────
        cutoff_30d = snapshot_date - timedelta(days=30)
        left_statuses = (
            EmployeeStatus.RESIGNED,
            EmployeeStatus.TERMINATED,
            EmployeeStatus.RETIRED,
        )
        turnover_stmt = select(func.count(Employee.employee_id)).where(
            Employee.organization_id == organization_id,
            Employee.status.in_(left_statuses),
            Employee.date_of_leaving >= cutoff_30d,
            Employee.date_of_leaving <= snapshot_date,
        )
        turnover = int(self.db.scalar(turnover_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="workforce.turnover_30d",
            snapshot_date=snapshot_date,
            value_numeric=turnover,
        )
        written += 1

        # ── 3. Leave utilization (last 30 days) ────────────────────
        leave_stmt = select(
            func.coalesce(func.sum(LeaveApplication.total_leave_days), 0)
        ).where(
            LeaveApplication.organization_id == organization_id,
            LeaveApplication.status == LeaveApplicationStatus.APPROVED,
            LeaveApplication.from_date >= cutoff_30d,
            LeaveApplication.from_date <= snapshot_date,
        )
        leave_days = Decimal(str(self.db.scalar(leave_stmt) or 0))

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="workforce.leave_utilization_30d",
            snapshot_date=snapshot_date,
            value_numeric=leave_days,
        )
        written += 1

        # ── 4. Attendance rate (last 30 days) ──────────────────────
        total_records_stmt = select(func.count(Attendance.attendance_id)).where(
            Attendance.organization_id == organization_id,
            Attendance.attendance_date >= cutoff_30d,
            Attendance.attendance_date <= snapshot_date,
        )
        total_records = int(self.db.scalar(total_records_stmt) or 0)

        present_statuses = (
            AttendanceStatus.PRESENT,
            AttendanceStatus.HALF_DAY,
            AttendanceStatus.WORK_FROM_HOME,
        )
        present_stmt = select(func.count(Attendance.attendance_id)).where(
            Attendance.organization_id == organization_id,
            Attendance.attendance_date >= cutoff_30d,
            Attendance.attendance_date <= snapshot_date,
            Attendance.status.in_(present_statuses),
        )
        present_count = int(self.db.scalar(present_stmt) or 0)

        attendance_rate: Decimal | None = None
        if total_records > 0:
            attendance_rate = Decimal(
                str(round(present_count / total_records * 100, 2))
            )

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="workforce.attendance_rate_30d",
            snapshot_date=snapshot_date,
            value_numeric=attendance_rate,
        )
        written += 1

        # ── 5. Pending leave approvals ─────────────────────────────
        pending_stmt = select(func.count(LeaveApplication.application_id)).where(
            LeaveApplication.organization_id == organization_id,
            LeaveApplication.status == LeaveApplicationStatus.SUBMITTED,
        )
        pending_leaves = int(self.db.scalar(pending_stmt) or 0)

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="workforce.pending_leave_approvals",
            snapshot_date=snapshot_date,
            value_numeric=pending_leaves,
        )
        written += 1

        # ── 6. Department distribution (JSON) ──────────────────────
        from app.models.people.hr.department import Department

        dept_stmt = (
            select(
                Department.department_name,
                func.count(Employee.employee_id).label("count"),
            )
            .join(Department, Employee.department_id == Department.department_id)
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .group_by(Department.department_name)
            .order_by(func.count(Employee.employee_id).desc())
            .limit(20)
        )
        dept_rows = self.db.execute(dept_stmt).all()
        dept_data = [
            {"department": str(name), "count": int(count)} for name, count in dept_rows
        ]

        self.upsert_metric(
            organization_id=organization_id,
            metric_type="workforce.department_distribution",
            snapshot_date=snapshot_date,
            value_json={"departments": dept_data, "total": headcount},
        )
        written += 1

        logger.info(
            "WorkforceComputer wrote %d metrics for org %s on %s",
            written,
            organization_id,
            snapshot_date,
        )
        return written
