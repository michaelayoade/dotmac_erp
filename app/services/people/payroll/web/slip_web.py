"""
Payroll Web Service - Salary Slip operations.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.employment_type import EmploymentType
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus
from app.models.people.payroll.salary_structure import SalaryStructure
from app.services.common import coerce_uuid
from app.services.people.payroll import (
    SalarySlipInput,
    payroll_gl_adapter,
    salary_slip_service,
)
from app.services.people.payroll.paye_calculator import PAYECalculator
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import (
    DEFAULT_PAGE_SIZE,
    SLIP_STATUSES,
    parse_date,
    parse_decimal,
    parse_slip_status,
    parse_uuid,
)

logger = logging.getLogger(__name__)


class SlipWebService:
    """Service for salary slip web views."""

    @staticmethod
    def _form_str(form: Any, key: str) -> str:
        """Normalize form value to a trimmed string."""
        value = form.get(key)
        if value is None:
            return ""
        return str(value).strip()

    def list_slips_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        status: str | None = None,
        page: int = 1,
    ) -> HTMLResponse | RedirectResponse:
        """Render salary slips list page."""
        org_id = coerce_uuid(auth.organization_id)
        per_page = DEFAULT_PAGE_SIZE
        offset = (page - 1) * per_page

        query = db.query(SalarySlip).filter(SalarySlip.organization_id == org_id)

        if search:
            query = query.filter(
                SalarySlip.slip_number.ilike(f"%{search}%")
                | SalarySlip.employee_name.ilike(f"%{search}%")
            )

        status_enum = parse_slip_status(status)
        if status_enum:
            query = query.filter(SalarySlip.status == status_enum)

        total = query.count()
        slips = (
            query.order_by(SalarySlip.created_at.desc())
            .offset(offset)
            .limit(per_page)
            .all()
        )
        total_pages = (total + per_page - 1) // per_page

        # Get counts by status
        status_counts = {}
        for s in SalarySlipStatus:
            count = (
                db.query(SalarySlip)
                .filter(SalarySlip.organization_id == org_id, SalarySlip.status == s)
                .count()
            )
            status_counts[s.value] = count

        context = base_context(request, auth, "Salary Slips", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "slips": slips,
                "search": search or "",
                "status": status or "",
                "page": page,
                "total_pages": total_pages,
                "total_count": total,
                "total": total,
                "limit": per_page,
                "has_prev": page > 1,
                "has_next": page < total_pages,
                "status_counts": status_counts,
                "statuses": SLIP_STATUSES,
            }
        )
        return templates.TemplateResponse(request, "people/payroll/slips.html", context)

    def export_slips_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        status: str | None = None,
    ) -> Response:
        """Export salary slips to CSV."""
        org_id = coerce_uuid(auth.organization_id)

        query = db.query(SalarySlip).filter(SalarySlip.organization_id == org_id)

        if search:
            query = query.filter(
                SalarySlip.slip_number.ilike(f"%{search}%")
                | SalarySlip.employee_name.ilike(f"%{search}%")
            )

        status_enum = parse_slip_status(status)
        if status_enum:
            query = query.filter(SalarySlip.status == status_enum)

        slips = query.order_by(SalarySlip.created_at.desc()).all()

        headers = [
            "Slip #",
            "Employee",
            "Period",
            "Gross",
            "Deductions",
            "Net Pay",
            "Status",
            "Bank Name",
            "Bank Account Number",
            "Bank Branch Code",
        ]

        rows: list[list[str]] = [headers]
        for slip in slips:
            period = f"{slip.start_date.strftime('%b %d')} - {slip.end_date.strftime('%b %d, %Y')}"
            rows.append(
                [
                    slip.slip_number,
                    slip.employee_name or "",
                    period,
                    f"{slip.gross_pay:,.2f}",
                    f"({slip.total_deduction:,.2f})",
                    f"{slip.net_pay:,.2f}",
                    slip.status.value.title(),
                    slip.bank_name or "",
                    slip.bank_account_number or "",
                    slip.bank_branch_code or "",
                ]
            )

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerows(rows)
        content = buffer.getvalue()
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="salary_slips.csv"'},
        )

    def slip_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Render new salary slip form."""
        org_id = coerce_uuid(auth.organization_id)

        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            )
            .order_by(Employee.employee_code)
            .all()
        )

        context = base_context(request, auth, "New Salary Slip", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "slip": None,
                "employees": employees,
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/slip_form.html", context
        )

    def slip_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        slip_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render edit salary slip form."""
        org_id = coerce_uuid(auth.organization_id)
        s_id = parse_uuid(slip_id)

        if not s_id:
            return RedirectResponse(
                url="/people/payroll/slips?success=Record+updated+successfully",
                status_code=303,
            )

        slip = db.get(SalarySlip, s_id)
        if not slip or slip.organization_id != org_id:
            return RedirectResponse(
                url="/people/payroll/slips?success=Record+updated+successfully",
                status_code=303,
            )

        if slip.status != SalarySlipStatus.DRAFT:
            return RedirectResponse(
                url=f"/people/payroll/slips/{slip_id}?saved=1", status_code=303
            )

        employees = (
            db.query(Employee)
            .filter(
                Employee.organization_id == org_id,
                Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]),
            )
            .order_by(Employee.employee_code)
            .all()
        )

        context = base_context(request, auth, "Edit Salary Slip", "payroll", db=db)
        context["request"] = request
        context.update(
            {
                "slip": slip,
                "employees": employees,
                "form_data": {
                    "employee_id": str(slip.employee_id),
                    "start_date": slip.start_date.isoformat(),
                    "end_date": slip.end_date.isoformat(),
                    "posting_date": slip.posting_date.isoformat()
                    if slip.posting_date
                    else "",
                    "total_working_days": str(slip.total_working_days or ""),
                    "absent_days": str(slip.absent_days or "0"),
                    "leave_without_pay": str(slip.leave_without_pay or "0"),
                },
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/slip_form.html", context
        )

    async def create_slip_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> Response:
        """Create new salary slip."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        employee_id = self._form_str(form, "employee_id")
        start_date = self._form_str(form, "start_date")
        end_date = self._form_str(form, "end_date")
        posting_date = self._form_str(form, "posting_date")
        total_working_days = self._form_str(form, "total_working_days")
        absent_days = self._form_str(form, "absent_days") or "0"
        leave_without_pay = self._form_str(form, "leave_without_pay") or "0"

        if not employee_id or not start_date or not end_date:
            employees = (
                db.query(Employee)
                .filter(
                    Employee.organization_id == org_id,
                    Employee.status.in_(
                        [EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]
                    ),
                )
                .order_by(Employee.employee_code)
                .all()
            )
            context = base_context(request, auth, "New Salary Slip", "payroll", db=db)
            context["request"] = request
            context.update(
                {
                    "slip": None,
                    "employees": employees,
                    "error": "Employee, period start, and period end are required.",
                    "form_data": {
                        "employee_id": employee_id,
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                    "errors": {},
                }
            )
            return templates.TemplateResponse(
                request, "people/payroll/slip_form.html", context
            )

        try:
            start = parse_date(start_date)
            end = parse_date(end_date)
            posting = parse_date(posting_date)
            if start is None or end is None:
                raise ValueError("Invalid start or end date")

            slip_input = SalarySlipInput(
                employee_id=coerce_uuid(employee_id),
                start_date=start,
                end_date=end,
                posting_date=posting,
                total_working_days=parse_decimal(total_working_days),
                absent_days=parse_decimal(absent_days) or Decimal("0"),
                leave_without_pay=parse_decimal(leave_without_pay) or Decimal("0"),
            )

            slip = salary_slip_service.create_salary_slip(
                db=db,
                organization_id=org_id,
                input=slip_input,
                created_by_user_id=user_id,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/payroll/slips/{slip.slip_id}?saved=1", status_code=303
            )

        except Exception as e:
            db.rollback()
            employees = (
                db.query(Employee)
                .filter(
                    Employee.organization_id == org_id,
                    Employee.status.in_(
                        [EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]
                    ),
                )
                .order_by(Employee.employee_code)
                .all()
            )

            context = base_context(request, auth, "New Salary Slip", "payroll", db=db)
            context["request"] = request
            context.update(
                {
                    "slip": None,
                    "employees": employees,
                    "error": str(e),
                    "form_data": {
                        "employee_id": employee_id,
                        "start_date": start_date,
                        "end_date": end_date,
                        "posting_date": posting_date,
                        "total_working_days": total_working_days,
                        "absent_days": absent_days,
                        "leave_without_pay": leave_without_pay,
                    },
                    "errors": {},
                }
            )
            return templates.TemplateResponse(
                request, "people/payroll/slip_form.html", context
            )

    async def update_slip_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        slip_id: str,
    ) -> Response:
        """Update salary slip."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        employee_id = self._form_str(form, "employee_id")
        start_date = self._form_str(form, "start_date")
        end_date = self._form_str(form, "end_date")
        posting_date = self._form_str(form, "posting_date")
        total_working_days = self._form_str(form, "total_working_days")
        absent_days = self._form_str(form, "absent_days") or "0"
        leave_without_pay = self._form_str(form, "leave_without_pay") or "0"

        if not employee_id or not start_date or not end_date:
            employees = (
                db.query(Employee)
                .filter(
                    Employee.organization_id == org_id,
                    Employee.status.in_(
                        [EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]
                    ),
                )
                .order_by(Employee.employee_code)
                .all()
            )
            context = base_context(request, auth, "Edit Salary Slip", "payroll", db=db)
            context["request"] = request
            context.update(
                {
                    "slip": db.get(SalarySlip, parse_uuid(slip_id))
                    if parse_uuid(slip_id)
                    else None,
                    "employees": employees,
                    "error": "Employee, period start, and period end are required.",
                    "form_data": {
                        "employee_id": employee_id,
                        "start_date": start_date,
                        "end_date": end_date,
                        "posting_date": posting_date,
                        "total_working_days": total_working_days,
                        "absent_days": absent_days,
                        "leave_without_pay": leave_without_pay,
                    },
                    "errors": {},
                }
            )
            return templates.TemplateResponse(
                request, "people/payroll/slip_form.html", context
            )

        try:
            start = parse_date(start_date)
            end = parse_date(end_date)
            posting = parse_date(posting_date)
            if start is None or end is None:
                raise ValueError("Invalid start or end date")

            slip_input = SalarySlipInput(
                employee_id=coerce_uuid(employee_id),
                start_date=start,
                end_date=end,
                posting_date=posting,
                total_working_days=parse_decimal(total_working_days),
                absent_days=parse_decimal(absent_days) or Decimal("0"),
                leave_without_pay=parse_decimal(leave_without_pay) or Decimal("0"),
            )

            slip = salary_slip_service.update_salary_slip(
                db=db,
                organization_id=org_id,
                slip_id=coerce_uuid(slip_id),
                input=slip_input,
                updated_by_user_id=user_id,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/payroll/slips/{slip.slip_id}?saved=1", status_code=303
            )

        except Exception as e:
            db.rollback()
            employees = (
                db.query(Employee)
                .filter(
                    Employee.organization_id == org_id,
                    Employee.status.in_(
                        [EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]
                    ),
                )
                .order_by(Employee.employee_code)
                .all()
            )

            context = base_context(request, auth, "Edit Salary Slip", "payroll", db=db)
            context["request"] = request
            context.update(
                {
                    "slip": db.get(SalarySlip, parse_uuid(slip_id))
                    if parse_uuid(slip_id)
                    else None,
                    "employees": employees,
                    "error": str(e),
                    "form_data": {
                        "employee_id": employee_id,
                        "start_date": start_date,
                        "end_date": end_date,
                        "posting_date": posting_date,
                        "total_working_days": total_working_days,
                        "absent_days": absent_days,
                        "leave_without_pay": leave_without_pay,
                    },
                    "errors": {},
                }
            )
            return templates.TemplateResponse(
                request, "people/payroll/slip_form.html", context
            )

    def slip_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        slip_id: str,
    ) -> Response:
        """Render salary slip detail page with PAYE breakdown."""
        org_id = coerce_uuid(auth.organization_id)
        s_id = parse_uuid(slip_id)

        if not s_id:
            return RedirectResponse(
                url="/people/payroll/slips?success=Record+saved+successfully",
                status_code=303,
            )

        slip = db.get(SalarySlip, s_id)
        if not slip or slip.organization_id != org_id:
            return RedirectResponse(
                url="/people/payroll/slips?success=Record+saved+successfully",
                status_code=303,
            )

        # Calculate PAYE breakdown for display
        paye_breakdown = None
        tax_profile = None
        skip_deductions = False

        employee = db.get(Employee, slip.employee_id) if slip.employee_id else None
        structure = (
            db.get(SalaryStructure, slip.structure_id) if slip.structure_id else None
        )
        if employee and structure:
            employment_type = employee.employment_type
            if employment_type is None and employee.employment_type_id:
                employment_type = db.get(EmploymentType, employee.employment_type_id)

            type_code = (
                (employment_type.type_code or "").strip().lower()
                if employment_type
                else ""
            )
            type_name = (
                (employment_type.type_name or "").strip().lower()
                if employment_type
                else ""
            )
            is_contract = type_code == "contract" or type_name == "contract"
            is_contract_structure = (
                structure.structure_name or ""
            ).strip().lower() == "contract staff"
            skip_deductions = is_contract or is_contract_structure

        if slip.employee_id:
            tax_profile = (
                db.query(EmployeeTaxProfile)
                .filter(
                    EmployeeTaxProfile.organization_id == org_id,
                    EmployeeTaxProfile.employee_id == slip.employee_id,
                    EmployeeTaxProfile.effective_to.is_(None),
                )
                .first()
            )

            # Calculate PAYE breakdown if we have gross pay
            if slip.gross_pay > 0 and not skip_deductions:
                calculator = PAYECalculator(db)
                basic_estimate = slip.gross_pay * Decimal("0.6")

                paye_breakdown = calculator.calculate(
                    organization_id=org_id,
                    gross_monthly=slip.gross_pay,
                    basic_monthly=basic_estimate,
                    annual_rent=tax_profile.annual_rent
                    if tax_profile
                    else Decimal("0"),
                    rent_verified=tax_profile.rent_receipt_verified
                    if tax_profile
                    else False,
                    pension_rate=tax_profile.pension_rate
                    if tax_profile
                    else Decimal("0.08"),
                    nhf_rate=tax_profile.nhf_rate if tax_profile else Decimal("0.025"),
                    nhis_rate=tax_profile.nhis_rate if tax_profile else Decimal("0"),
                )

        context = base_context(request, auth, "Salary Slip", "payroll", db=db)
        context["request"] = request
        error = request.query_params.get("error")
        success = request.query_params.get("success")
        context.update(
            {
                "slip": slip,
                "paye_breakdown": paye_breakdown,
                "tax_profile": tax_profile,
                "error": error,
                "success": success,
            }
        )
        return templates.TemplateResponse(
            request, "people/payroll/slip_detail.html", context
        )

    def submit_slip_response(
        self,
        auth: WebAuthContext,
        db: Session,
        slip_id: str,
    ) -> RedirectResponse:
        """Submit salary slip for approval."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        try:
            salary_slip_service.submit_salary_slip(
                db=db,
                organization_id=org_id,
                slip_id=coerce_uuid(slip_id),
                submitted_by_user_id=user_id,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            message = getattr(e, "detail", None) or str(e)
            return RedirectResponse(
                url=f"/people/payroll/slips/{slip_id}?error={quote(message)}",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/people/payroll/slips/{slip_id}?saved=1", status_code=303
        )

    def approve_slip_response(
        self,
        auth: WebAuthContext,
        db: Session,
        slip_id: str,
    ) -> RedirectResponse:
        """Approve salary slip."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        try:
            salary_slip_service.approve_salary_slip(
                db=db,
                organization_id=org_id,
                slip_id=coerce_uuid(slip_id),
                approved_by_user_id=user_id,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            message = getattr(e, "detail", None) or str(e)
            return RedirectResponse(
                url=f"/people/payroll/slips/{slip_id}?error={quote(message)}",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/people/payroll/slips/{slip_id}?saved=1", status_code=303
        )

    def post_slip_response(
        self,
        auth: WebAuthContext,
        db: Session,
        slip_id: str,
        posting_date: str | None = None,
    ) -> RedirectResponse:
        """Post salary slip to GL."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        post_date = parse_date(posting_date) or date.today()

        try:
            payroll_gl_adapter.post_salary_slip(
                db=db,
                organization_id=org_id,
                slip_id=coerce_uuid(slip_id),
                posting_date=post_date,
                posted_by_user_id=user_id,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            message = getattr(e, "detail", None) or str(e)
            return RedirectResponse(
                url=f"/people/payroll/slips/{slip_id}?error={quote(message)}",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/people/payroll/slips/{slip_id}?saved=1", status_code=303
        )

    def delete_slip_response(
        self,
        auth: WebAuthContext,
        db: Session,
        slip_id: str,
    ) -> RedirectResponse:
        """Delete a draft salary slip."""
        org_id = coerce_uuid(auth.organization_id)
        s_id = parse_uuid(slip_id)

        if not s_id:
            return RedirectResponse(
                url="/people/payroll/slips?success=Record+deleted+successfully",
                status_code=303,
            )

        slip = db.get(SalarySlip, s_id)
        if not slip or slip.organization_id != org_id:
            return RedirectResponse(
                url="/people/payroll/slips?success=Record+deleted+successfully",
                status_code=303,
            )

        if slip.status != SalarySlipStatus.DRAFT:
            return RedirectResponse(
                url=f"/people/payroll/slips/{slip_id}?saved=1", status_code=303
            )

        try:
            db.delete(slip)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url="/people/payroll/slips?success=Record+deleted+successfully",
            status_code=303,
        )
