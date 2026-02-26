"""Payroll Dashboard Web Service.

Provides dashboard data and chart computations for the Payroll module.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import and_, extract, func, or_, select
from sqlalchemy.orm import Session

from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.payroll.payroll_entry import PayrollEntry, PayrollEntryStatus
from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus
from app.services.common import coerce_uuid
from app.templates import templates

if TYPE_CHECKING:
    from app.web.deps import WebAuthContext

logger = logging.getLogger(__name__)


class PayrollDashboardService:
    """Service for Payroll module dashboard."""

    def dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render the Payroll dashboard page."""
        from app.web.deps import base_context

        org_id = coerce_uuid(auth.organization_id)

        stats = self._get_dashboard_stats(db, org_id)
        chart_data = self._get_chart_data(db, org_id)
        recent_runs = self._get_recent_runs(db, org_id, limit=5)
        alerts = self._get_alerts(db, org_id)

        context = {
            **base_context(request, auth, "Payroll Dashboard", "payroll", db=db),
            "stats": stats,
            "chart_data": chart_data,
            "recent_runs": recent_runs,
            "alerts": alerts,
        }

        return templates.TemplateResponse(
            request, "people/payroll/dashboard.html", context
        )

    def _get_dashboard_stats(self, db: Session, org_id: UUID) -> dict[str, Any]:
        """Get aggregate payroll statistics for stat cards."""
        today = date.today()
        month_start = today.replace(day=1)

        # Latest completed run (POSTED status)
        latest_run = db.scalar(
            select(PayrollEntry)
            .where(
                PayrollEntry.organization_id == org_id,
                PayrollEntry.status == PayrollEntryStatus.POSTED,
            )
            .order_by(PayrollEntry.posting_date.desc())
            .limit(1)
        )

        total_gross: Decimal = Decimal("0")
        total_net: Decimal = Decimal("0")
        if latest_run:
            total_gross = latest_run.total_gross_pay or Decimal("0")
            total_net = latest_run.total_net_pay or Decimal("0")

        # Pending runs (DRAFT + SLIPS_CREATED)
        pending_runs = (
            db.scalar(
                select(func.count(PayrollEntry.entry_id)).where(
                    and_(
                        PayrollEntry.organization_id == org_id,
                        PayrollEntry.status.in_(
                            [
                                PayrollEntryStatus.DRAFT,
                                PayrollEntryStatus.SLIPS_CREATED,
                            ]
                        ),
                    )
                )
            )
            or 0
        )

        # Employees paid this month (POSTED slips in current month)
        next_month_start = (
            month_start.replace(month=month_start.month + 1)
            if month_start.month < 12
            else month_start.replace(year=month_start.year + 1, month=1)
        )
        employees_paid = (
            db.scalar(
                select(func.count(func.distinct(SalarySlip.employee_id))).where(
                    and_(
                        SalarySlip.organization_id == org_id,
                        SalarySlip.status == SalarySlipStatus.POSTED,
                        SalarySlip.start_date >= month_start,
                        SalarySlip.start_date < next_month_start,
                    )
                )
            )
            or 0
        )

        # Total runs this year
        year_start = today.replace(month=1, day=1)
        runs_this_year = (
            db.scalar(
                select(func.count(PayrollEntry.entry_id)).where(
                    and_(
                        PayrollEntry.organization_id == org_id,
                        PayrollEntry.status == PayrollEntryStatus.POSTED,
                        PayrollEntry.posting_date >= year_start,
                    )
                )
            )
            or 0
        )

        return {
            "total_gross": total_gross,
            "total_net": total_net,
            "pending_runs": pending_runs,
            "employees_paid": employees_paid,
            "runs_this_year": runs_this_year,
            "latest_run": latest_run,
        }

    def _get_chart_data(self, db: Session, org_id: UUID) -> dict[str, Any]:
        """Get chart data for payroll dashboard."""
        return {
            "payroll_trend": self._get_payroll_trend(db, org_id),
            "department_breakdown": self._get_department_breakdown(db, org_id),
        }

    def _get_payroll_trend(self, db: Session, org_id: UUID) -> list[dict[str, Any]]:
        """Get monthly payroll totals for the last 6 months."""
        today = date.today()
        trend: list[dict[str, Any]] = []

        for i in range(5, -1, -1):
            # Proper month arithmetic: subtract i months from current month
            y = today.year
            m = today.month - i
            while m <= 0:
                m += 12
                y -= 1
            month_name = date(y, m, 1).strftime("%b")

            result = db.execute(
                select(
                    func.coalesce(func.sum(SalarySlip.gross_pay), 0),
                    func.coalesce(func.sum(SalarySlip.net_pay), 0),
                    func.coalesce(func.sum(SalarySlip.total_deduction), 0),
                ).where(
                    and_(
                        SalarySlip.organization_id == org_id,
                        SalarySlip.status == SalarySlipStatus.POSTED,
                        extract("year", SalarySlip.start_date) == y,
                        extract("month", SalarySlip.start_date) == m,
                    )
                )
            ).one()

            trend.append(
                {
                    "month": month_name,
                    "gross": float(result[0] or 0),
                    "net": float(result[1] or 0),
                    "deductions": float(result[2] or 0),
                }
            )

        return trend

    def _get_department_breakdown(
        self, db: Session, org_id: UUID
    ) -> list[dict[str, Any]]:
        """Get current month payroll by department."""
        today = date.today()
        month_start = today.replace(day=1)

        results = db.execute(
            select(
                Department.department_name,
                func.coalesce(func.sum(SalarySlip.gross_pay), 0),
            )
            .join(Employee, Employee.employee_id == SalarySlip.employee_id)
            .outerjoin(Department, Department.department_id == Employee.department_id)
            .where(
                and_(
                    SalarySlip.organization_id == org_id,
                    SalarySlip.status == SalarySlipStatus.POSTED,
                    SalarySlip.start_date >= month_start,
                )
            )
            .group_by(Department.department_name)
            .order_by(func.sum(SalarySlip.gross_pay).desc())
            .limit(8)
        ).all()

        return [
            {"name": name or "Unassigned", "amount": float(amount)}
            for name, amount in results
        ]

    def _get_recent_runs(
        self, db: Session, org_id: UUID, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Get most recent payroll runs."""
        runs = db.scalars(
            select(PayrollEntry)
            .where(PayrollEntry.organization_id == org_id)
            .order_by(PayrollEntry.created_at.desc())
            .limit(limit)
        ).all()

        return [
            {
                "entry_id": str(run.entry_id),
                "entry_number": run.entry_number,
                "status": run.status.value,
                "start_date": run.start_date,
                "end_date": run.end_date,
                "employee_count": run.employee_count or 0,
                "total_gross": run.total_gross_pay or Decimal("0"),
                "total_net": run.total_net_pay or Decimal("0"),
                "posting_date": run.posting_date,
            }
            for run in runs
        ]

    def _get_alerts(self, db: Session, org_id: UUID) -> list[dict[str, Any]]:
        """Get payroll alerts and warnings."""
        alerts: list[dict[str, Any]] = []

        # Pending runs needing attention
        submitted_count = (
            db.scalar(
                select(func.count(PayrollEntry.entry_id)).where(
                    and_(
                        PayrollEntry.organization_id == org_id,
                        PayrollEntry.status == PayrollEntryStatus.SUBMITTED,
                    )
                )
            )
            or 0
        )
        if submitted_count > 0:
            alerts.append(
                {
                    "type": "warning",
                    "icon": "clock",
                    "title": "Runs Awaiting Approval",
                    "message": f"{submitted_count} payroll run(s) submitted and awaiting approval",
                    "url": "/people/payroll/runs?status=SUBMITTED",
                }
            )

        # Employees without salary assignments
        active_count = (
            db.scalar(
                select(func.count(Employee.employee_id)).where(
                    and_(
                        Employee.organization_id == org_id,
                        Employee.is_deleted.is_(False),
                        Employee.status == EmployeeStatus.ACTIVE,
                    )
                )
            )
            or 0
        )

        from app.models.people.payroll.salary_assignment import (
            SalaryStructureAssignment,
        )

        today = date.today()
        assigned_count = (
            db.scalar(
                select(
                    func.count(func.distinct(SalaryStructureAssignment.employee_id))
                ).where(
                    and_(
                        SalaryStructureAssignment.organization_id == org_id,
                        SalaryStructureAssignment.from_date <= today,
                        or_(
                            SalaryStructureAssignment.to_date.is_(None),
                            SalaryStructureAssignment.to_date >= today,
                        ),
                    )
                )
            )
            or 0
        )

        unassigned = active_count - assigned_count
        if unassigned > 0:
            alerts.append(
                {
                    "type": "error",
                    "icon": "warning",
                    "title": "Missing Salary Assignments",
                    "message": f"{unassigned} active employee(s) without salary structure assignments",
                    "url": "/people/payroll/assignments",
                }
            )

        return alerts
