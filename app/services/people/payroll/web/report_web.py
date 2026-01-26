"""
Payroll Web Service - Report operations.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.people.hr.department import Department
from app.models.people.hr.employee import Employee
from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus
from app.models.people.payroll.payroll_entry import PayrollEntry
from app.services.common import coerce_uuid
from app.templates import templates
from app.web.deps import base_context, WebAuthContext

from .base import parse_int, parse_uuid


class ReportWebService:
    """Service for payroll report web views."""

    def summary_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> HTMLResponse:
        """Render payroll summary report."""
        org_id = coerce_uuid(auth.organization_id)

        today = date.today()
        year = year or today.year
        month = month or today.month

        # Get aggregated data
        query = (
            db.query(
                func.count(SalarySlip.slip_id).label("slip_count"),
                func.sum(SalarySlip.gross_pay).label("total_gross"),
                func.sum(SalarySlip.net_pay).label("total_net"),
                func.sum(SalarySlip.total_deduction).label("total_deductions"),
            )
            .filter(
                SalarySlip.organization_id == org_id,
                SalarySlip.status.in_([SalarySlipStatus.APPROVED, SalarySlipStatus.POSTED]),
            )
        )

        # Filter by period
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1)
        else:
            end_date = date(year, month + 1, 1)

        query = query.filter(
            SalarySlip.start_date >= start_date,
            SalarySlip.start_date < end_date,
        )

        result = query.first()

        if not result:
            summary = {
                "slip_count": 0,
                "total_gross": Decimal("0"),
                "total_net": Decimal("0"),
                "total_deductions": Decimal("0"),
            }
        else:
            summary = {
                "slip_count": result.slip_count or 0,
                "total_gross": result.total_gross or Decimal("0"),
                "total_net": result.total_net or Decimal("0"),
                "total_deductions": result.total_deductions or Decimal("0"),
            }

        context = base_context(request, auth, "Payroll Summary", "payroll", db=db)
        context["request"] = request
        context.update({
            "year": year,
            "month": month,
            "summary": summary,
        })
        return templates.TemplateResponse(request, "people/payroll/reports/summary.html", context)

    def by_department_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> HTMLResponse:
        """Render payroll by department report."""
        org_id = coerce_uuid(auth.organization_id)

        today = date.today()
        year = year or today.year
        month = month or today.month

        # Get department breakdown
        departments = (
            db.query(Department)
            .filter(Department.organization_id == org_id)
            .order_by(Department.department_name)
            .all()
        )

        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1)
        else:
            end_date = date(year, month + 1, 1)

        dept_data = []
        for dept in departments:
            result = (
                db.query(
                    func.count(SalarySlip.slip_id).label("slip_count"),
                    func.sum(SalarySlip.gross_pay).label("total_gross"),
                    func.sum(SalarySlip.net_pay).label("total_net"),
                )
                .join(Employee, SalarySlip.employee_id == Employee.employee_id)
                .filter(
                    SalarySlip.organization_id == org_id,
                    Employee.department_id == dept.department_id,
                    SalarySlip.status.in_([SalarySlipStatus.APPROVED, SalarySlipStatus.POSTED]),
                    SalarySlip.start_date >= start_date,
                    SalarySlip.start_date < end_date,
                )
                .first()
            )
            if result and result.slip_count and result.slip_count > 0:
                dept_data.append({
                    "department": dept,
                    "slip_count": result.slip_count or 0,
                    "total_gross": result.total_gross or Decimal("0"),
                    "total_net": result.total_net or Decimal("0"),
                })

        context = base_context(request, auth, "Payroll by Department", "payroll", db=db)
        context["request"] = request
        context.update({
            "year": year,
            "month": month,
            "dept_data": dept_data,
        })
        return templates.TemplateResponse(request, "people/payroll/reports/by_department.html", context)

    def tax_summary_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        year: Optional[int] = None,
        month: Optional[int] = None,
    ) -> HTMLResponse:
        """Render tax summary report."""
        org_id = coerce_uuid(auth.organization_id)

        today = date.today()
        year = year or today.year
        month = month or today.month

        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1)
        else:
            end_date = date(year, month + 1, 1)

        # Get all approved/posted slips for the period
        slips = (
            db.query(SalarySlip)
            .filter(
                SalarySlip.organization_id == org_id,
                SalarySlip.status.in_([SalarySlipStatus.APPROVED, SalarySlipStatus.POSTED]),
                SalarySlip.start_date >= start_date,
                SalarySlip.start_date < end_date,
            )
            .all()
        )

        # Calculate totals
        total_paye = sum((getattr(s, "paye", None) or Decimal("0") for s in slips), Decimal("0"))
        total_pension = sum((getattr(s, "pension_employee", None) or Decimal("0") for s in slips), Decimal("0"))
        total_nhf = sum((getattr(s, "nhf", None) or Decimal("0") for s in slips), Decimal("0"))
        total_nhis = sum((getattr(s, "nhis_employee", None) or Decimal("0") for s in slips), Decimal("0"))

        tax_summary = {
            "total_paye": total_paye,
            "total_pension": total_pension,
            "total_nhf": total_nhf,
            "total_nhis": total_nhis,
            "total_statutory": total_paye + total_pension + total_nhf + total_nhis,
            "employee_count": len(slips),
        }

        context = base_context(request, auth, "Tax Summary Report", "payroll", db=db)
        context["request"] = request
        context.update({
            "year": year,
            "month": month,
            "tax_summary": tax_summary,
        })
        return templates.TemplateResponse(request, "people/payroll/reports/tax_summary.html", context)

    def trends_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        year: Optional[int] = None,
    ) -> HTMLResponse:
        """Render payroll trends report."""
        org_id = coerce_uuid(auth.organization_id)

        today = date.today()
        year = year or today.year

        # Get monthly data for the year
        monthly_data = []
        for month in range(1, 13):
            start_date = date(year, month, 1)
            if month == 12:
                end_date = date(year + 1, 1, 1)
            else:
                end_date = date(year, month + 1, 1)

            result = (
                db.query(
                    func.count(SalarySlip.slip_id).label("slip_count"),
                    func.sum(SalarySlip.gross_pay).label("total_gross"),
                    func.sum(SalarySlip.net_pay).label("total_net"),
                )
                .filter(
                    SalarySlip.organization_id == org_id,
                    SalarySlip.status.in_([SalarySlipStatus.APPROVED, SalarySlipStatus.POSTED]),
                    SalarySlip.start_date >= start_date,
                    SalarySlip.start_date < end_date,
                )
                .first()
            )

            monthly_data.append({
                "month": month,
                "month_name": start_date.strftime("%B"),
                "slip_count": result.slip_count or 0,
                "total_gross": result.total_gross or Decimal("0"),
                "total_net": result.total_net or Decimal("0"),
            })

        context = base_context(request, auth, "Payroll Trends", "payroll", db=db)
        context["request"] = request
        context.update({
            "year": year,
            "monthly_data": monthly_data,
        })
        return templates.TemplateResponse(request, "people/payroll/reports/trends.html", context)
