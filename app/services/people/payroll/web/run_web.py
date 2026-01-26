"""
Payroll Web Service - Payroll Run/Entry operations.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.department import Department
from app.models.people.payroll.salary_structure import SalaryStructure
from app.models.people.payroll.payroll_entry import PayrollEntry, PayrollEntryStatus
from app.models.people.payroll.salary_slip import SalarySlip
from app.services.common import coerce_uuid
from app.services.people.payroll.payroll_service import PayrollService, PayrollServiceError
from app.templates import templates
from app.web.deps import base_context, WebAuthContext

from .base import (
    DEFAULT_PAGE_SIZE,
    parse_uuid,
    parse_date,
    parse_int,
    parse_entry_status,
    ENTRY_STATUSES,
)


class RunWebService:
    """Service for payroll run web views."""

    def list_runs_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render payroll runs list page."""
        org_id = coerce_uuid(auth.organization_id)
        per_page = DEFAULT_PAGE_SIZE
        offset = (page - 1) * per_page

        query = db.query(PayrollEntry).filter(PayrollEntry.organization_id == org_id)

        status_enum = parse_entry_status(status)
        if status_enum:
            query = query.filter(PayrollEntry.status == status_enum)

        if year:
            query = query.filter(PayrollEntry.payroll_year == year)

        if month:
            query = query.filter(PayrollEntry.payroll_month == month)

        total = query.count()
        entries = query.order_by(PayrollEntry.created_at.desc()).offset(offset).limit(per_page).all()
        total_pages = (total + per_page - 1) // per_page

        # Get statistics
        draft_count = (
            db.query(PayrollEntry)
            .filter(
                PayrollEntry.organization_id == org_id,
                PayrollEntry.status == PayrollEntryStatus.DRAFT,
            )
            .count()
        )
        pending_count = (
            db.query(PayrollEntry)
            .filter(
                PayrollEntry.organization_id == org_id,
                PayrollEntry.status == PayrollEntryStatus.PENDING,
            )
            .count()
        )

        context = base_context(request, auth, "Payroll Runs", "payroll", db=db)
        context["request"] = request
        context.update({
            "entries": entries,
            "status": status,
            "year": year,
            "month": month,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "statuses": ENTRY_STATUSES,
            "draft_count": draft_count,
            "pending_count": pending_count,
        })
        return templates.TemplateResponse(request, "people/payroll/runs.html", context)

    def run_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new payroll run form."""
        org_id = coerce_uuid(auth.organization_id)

        departments = (
            db.query(Department)
            .filter(Department.organization_id == org_id, Department.is_active == True)
            .order_by(Department.department_name)
            .all()
        )

        structures = (
            db.query(SalaryStructure)
            .filter(SalaryStructure.organization_id == org_id, SalaryStructure.is_active == True)
            .order_by(SalaryStructure.structure_name)
            .all()
        )

        today = date.today()

        context = base_context(request, auth, "New Payroll Run", "payroll", db=db)
        context["request"] = request
        context.update({
            "entry": None,
            "departments": departments,
            "structures": structures,
            "current_year": today.year,
            "current_month": today.month,
            "form_data": {
                "payroll_year": today.year,
                "payroll_month": today.month,
            },
            "errors": {},
        })
        return templates.TemplateResponse(request, "people/payroll/run_form.html", context)

    async def create_run_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Create new payroll run."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        entry_name = (form.get("entry_name") or "").strip()
        payroll_year = parse_int(form.get("payroll_year"))
        payroll_month = parse_int(form.get("payroll_month"))
        department_id = (form.get("department_id") or "").strip()
        structure_id = (form.get("structure_id") or "").strip()
        start_date_str = (form.get("start_date") or "").strip()
        end_date_str = (form.get("end_date") or "").strip()
        posting_date_str = (form.get("posting_date") or "").strip()

        try:
            svc = PayrollService(db, org_id)

            entry = svc.create_payroll_entry(
                entry_name=entry_name,
                payroll_year=payroll_year,
                payroll_month=payroll_month,
                department_id=parse_uuid(department_id) if department_id else None,
                structure_id=parse_uuid(structure_id) if structure_id else None,
                start_date=parse_date(start_date_str),
                end_date=parse_date(end_date_str),
                posting_date=parse_date(posting_date_str),
                created_by=user_id,
            )
            db.commit()
            return RedirectResponse(url=f"/people/payroll/runs/{entry.entry_id}", status_code=303)

        except Exception as e:
            db.rollback()
            return self._render_run_form_with_error(
                request, auth, db, str(e), {
                    "entry_name": entry_name,
                    "payroll_year": payroll_year,
                    "payroll_month": payroll_month,
                    "department_id": department_id,
                    "structure_id": structure_id,
                    "start_date": start_date_str,
                    "end_date": end_date_str,
                    "posting_date": posting_date_str,
                }
            )

    def run_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
        success: Optional[str] = None,
        error: Optional[str] = None,
    ) -> HTMLResponse:
        """Render payroll run detail page."""
        org_id = coerce_uuid(auth.organization_id)
        e_id = parse_uuid(entry_id)

        if not e_id:
            return RedirectResponse(url="/people/payroll/runs", status_code=303)

        entry = db.get(PayrollEntry, e_id)
        if not entry or entry.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/runs", status_code=303)

        # Get associated slips
        slips = (
            db.query(SalarySlip)
            .filter(SalarySlip.payroll_entry_id == e_id)
            .order_by(SalarySlip.employee_name)
            .all()
        )

        context = base_context(request, auth, entry.entry_name or "Payroll Run", "payroll", db=db)
        context["request"] = request
        context.update({
            "entry": entry,
            "slips": slips,
            "success": success,
            "error": error,
        })
        return templates.TemplateResponse(request, "people/payroll/run_detail.html", context)

    def generate_run_response(
        self,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
    ) -> RedirectResponse:
        """Generate salary slips for payroll run."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        e_id = parse_uuid(entry_id)

        if e_id:
            try:
                svc = PayrollService(db, org_id)
                svc.generate_salary_slips(e_id, created_by=user_id)
                db.commit()
            except Exception:
                db.rollback()

        return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)

    def regenerate_run_response(
        self,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
    ) -> RedirectResponse:
        """Regenerate salary slips for payroll run."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        e_id = parse_uuid(entry_id)

        if e_id:
            try:
                svc = PayrollService(db, org_id)
                svc.regenerate_salary_slips(e_id, created_by=user_id)
                db.commit()
            except Exception:
                db.rollback()

        return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)

    def submit_run_response(
        self,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
    ) -> RedirectResponse:
        """Submit payroll run for approval."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        e_id = parse_uuid(entry_id)

        if e_id:
            try:
                svc = PayrollService(db, org_id)
                svc.submit_payroll_entry(e_id, submitted_by=user_id)
                db.commit()
            except Exception:
                db.rollback()

        return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)

    def approve_run_response(
        self,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
    ) -> RedirectResponse:
        """Approve payroll run."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        e_id = parse_uuid(entry_id)

        if e_id:
            try:
                svc = PayrollService(db, org_id)
                svc.approve_payroll_entry(e_id, approved_by=user_id)
                db.commit()
            except Exception:
                db.rollback()

        return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)

    def post_run_response(
        self,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
        posting_date: Optional[str] = None,
    ) -> RedirectResponse:
        """Post payroll run to GL."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)
        e_id = parse_uuid(entry_id)

        if e_id:
            try:
                svc = PayrollService(db, org_id)
                svc.post_payroll_entry(
                    e_id,
                    posting_date=parse_date(posting_date) or date.today(),
                    posted_by=user_id,
                )
                db.commit()
            except Exception:
                db.rollback()

        return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)

    def delete_run_response(
        self,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
    ) -> RedirectResponse:
        """Delete payroll run."""
        org_id = coerce_uuid(auth.organization_id)
        e_id = parse_uuid(entry_id)

        if e_id:
            entry = db.get(PayrollEntry, e_id)
            if entry and entry.organization_id == org_id and entry.status == PayrollEntryStatus.DRAFT:
                # Delete associated slips first
                db.query(SalarySlip).filter(SalarySlip.payroll_entry_id == e_id).delete()
                db.delete(entry)
                db.commit()
                return RedirectResponse(url="/people/payroll/runs", status_code=303)

        return RedirectResponse(url=f"/people/payroll/runs/{entry_id}", status_code=303)

    def _render_run_form_with_error(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: str,
        form_data: dict,
    ) -> HTMLResponse:
        """Render run form with error."""
        org_id = coerce_uuid(auth.organization_id)

        departments = (
            db.query(Department)
            .filter(Department.organization_id == org_id, Department.is_active == True)
            .order_by(Department.department_name)
            .all()
        )

        structures = (
            db.query(SalaryStructure)
            .filter(SalaryStructure.organization_id == org_id, SalaryStructure.is_active == True)
            .order_by(SalaryStructure.structure_name)
            .all()
        )

        today = date.today()

        context = base_context(request, auth, "New Payroll Run", "payroll", db=db)
        context["request"] = request
        context.update({
            "entry": None,
            "departments": departments,
            "structures": structures,
            "current_year": today.year,
            "current_month": today.month,
            "form_data": form_data,
            "error": error,
            "errors": {},
        })
        return templates.TemplateResponse(request, "people/payroll/run_form.html", context)
