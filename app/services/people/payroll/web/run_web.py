"""
Payroll Web Service - Payroll Run/Entry operations.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus
from app.models.finance.core_org import Organization
from app.models.finance.gl.account import Account
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.department import Department
from app.models.people.hr.designation import Designation
from app.models.people.payroll.salary_assignment import SalaryStructureAssignment
from app.models.people.payroll.salary_structure import PayrollFrequency, SalaryStructure
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
    parse_payroll_frequency,
    parse_slip_status,
    ENTRY_STATUSES,
    PAYROLL_FREQUENCIES,
    SLIP_STATUSES,
)


class RunWebService:
    """Service for payroll run web views."""

    @staticmethod
    def _form_text(value: object | None, default: str = "") -> str:
        if isinstance(value, str):
            return value.strip()
        return default

    def _get_default_frequency(self, db: Session, org_id) -> PayrollFrequency:
        org = db.get(Organization, org_id) if org_id else None
        if org and org.hr_payroll_frequency:
            parsed = parse_payroll_frequency(org.hr_payroll_frequency)
            if parsed:
                return parsed
        return PayrollFrequency.MONTHLY

    @staticmethod
    def _get_default_period(
        frequency: PayrollFrequency,
        today: date,
    ) -> tuple[date, date]:
        if frequency == PayrollFrequency.WEEKLY:
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
        elif frequency == PayrollFrequency.BIWEEKLY:
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=13)
        elif frequency == PayrollFrequency.SEMIMONTHLY:
            if today.day <= 15:
                start = today.replace(day=1)
                end = today.replace(day=15)
            else:
                start = today.replace(day=16)
                last_day = monthrange(today.year, today.month)[1]
                end = today.replace(day=last_day)
        else:
            start = today.replace(day=1)
            last_day = monthrange(today.year, today.month)[1]
            end = today.replace(day=last_day)
        return start, end

    def _count_active_assignments(
        self,
        db: Session,
        org_id,
        effective_date: date,
    ) -> int:
        return (
            db.query(SalaryStructureAssignment)
            .join(Employee, SalaryStructureAssignment.employee_id == Employee.employee_id)
            .filter(SalaryStructureAssignment.organization_id == org_id)
            .filter(SalaryStructureAssignment.from_date <= effective_date)
            .filter(
                or_(
                    SalaryStructureAssignment.to_date.is_(None),
                    SalaryStructureAssignment.to_date >= effective_date,
                )
            )
            .filter(Employee.status.in_([EmployeeStatus.ACTIVE, EmployeeStatus.ON_LEAVE]))
            .count()
        )

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
        status_counts = {}
        for entry_status in ENTRY_STATUSES:
            try:
                status_enum = parse_entry_status(entry_status)
                if status_enum:
                    status_counts[entry_status] = (
                        db.query(PayrollEntry)
                        .filter(
                            PayrollEntry.organization_id == org_id,
                            PayrollEntry.status == status_enum,
                        )
                        .count()
                    )
            except Exception:
                status_counts[entry_status] = 0

        context = base_context(request, auth, "Payroll Runs", "payroll", db=db)
        context["request"] = request
        context.update({
            "runs": entries,
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
            "status_counts": status_counts,
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

        designations = (
            db.query(Designation)
            .filter(
                Designation.organization_id == org_id,
                Designation.is_active == True,
                Designation.is_deleted == False,
            )
            .order_by(Designation.designation_name)
            .all()
        )

        structures = (
            db.query(SalaryStructure)
            .filter(SalaryStructure.organization_id == org_id, SalaryStructure.is_active == True)
            .order_by(SalaryStructure.structure_name)
            .all()
        )
        bank_accounts = (
            db.query(BankAccount)
            .filter(BankAccount.organization_id == org_id)
            .order_by(BankAccount.bank_name, BankAccount.account_name)
            .all()
        )

        # Expense accounts for GL posting (account codes starting with 6)
        expense_accounts = (
            db.query(Account)
            .filter(
                Account.organization_id == org_id,
                Account.is_active == True,
                Account.account_code.like("6%"),
            )
            .order_by(Account.account_code)
            .all()
        )

        today = date.today()
        frequency = self._get_default_frequency(db, org_id)
        default_start, default_end = self._get_default_period(frequency, today)
        assigned_count = self._count_active_assignments(db, org_id, default_start)

        context = base_context(request, auth, "New Payroll Run", "payroll", db=db)
        context["request"] = request
        context.update({
            "entry": None,
            "run": None,
            "departments": departments,
            "designations": designations,
            "structures": structures,
            "bank_accounts": bank_accounts,
            "expense_accounts": expense_accounts,
            "current_year": today.year,
            "current_month": today.month,
            "frequencies": PAYROLL_FREQUENCIES,
            "assigned_count": assigned_count,
            "default_start": default_start.isoformat(),
            "default_end": default_end.isoformat(),
            "default_posting": today.isoformat(),
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
    ) -> HTMLResponse | RedirectResponse:
        """Create new payroll run."""
        org_id = coerce_uuid(auth.organization_id)
        user_id = coerce_uuid(auth.user_id)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        entry_name = self._form_text(form.get("entry_name"))
        payroll_year = parse_int(self._form_text(form.get("payroll_year")))
        payroll_month = parse_int(self._form_text(form.get("payroll_month")))
        department_id = self._form_text(form.get("department_id"))
        designation_id = self._form_text(form.get("designation_id"))
        structure_id = self._form_text(form.get("structure_id"))
        bank_account_id = self._form_text(form.get("bank_account_id"))
        expense_account_id = self._form_text(form.get("expense_account_id"))
        payroll_frequency = self._form_text(form.get("payroll_frequency"))
        currency_code = self._form_text(form.get("currency_code"))
        start_date_str = self._form_text(form.get("start_date"))
        end_date_str = self._form_text(form.get("end_date"))
        posting_date_str = self._form_text(form.get("posting_date"))
        notes = self._form_text(form.get("notes"))

        try:
            svc = PayrollService(db)
            start_date = parse_date(start_date_str)
            end_date = parse_date(end_date_str)
            posting_date = parse_date(posting_date_str) or date.today()
            if not start_date or not end_date:
                raise ValueError("Start date and end date are required")

            parsed_frequency = parse_payroll_frequency(payroll_frequency)
            entry = svc.create_payroll_entry(
                org_id,
                posting_date=posting_date,
                start_date=start_date,
                end_date=end_date,
                source_bank_account_id=parse_uuid(bank_account_id) if bank_account_id else None,
                expense_account_id=parse_uuid(expense_account_id) if expense_account_id else None,
                department_id=parse_uuid(department_id) if department_id else None,
                designation_id=parse_uuid(designation_id) if designation_id else None,
                payroll_frequency=parsed_frequency or PayrollFrequency.MONTHLY,
                currency_code=currency_code or "NGN",
                notes=notes or entry_name or None,
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
                    "designation_id": designation_id,
                    "structure_id": structure_id,
                    "bank_account_id": bank_account_id,
                    "expense_account_id": expense_account_id,
                    "payroll_frequency": payroll_frequency,
                    "currency_code": currency_code,
                    "start_date": start_date_str,
                    "end_date": end_date_str,
                    "posting_date": posting_date_str,
                    "notes": notes or entry_name,
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
    ) -> HTMLResponse | RedirectResponse:
        """Render payroll run detail page."""
        org_id = coerce_uuid(auth.organization_id)
        e_id = parse_uuid(entry_id)

        if not e_id:
            return RedirectResponse(url="/people/payroll/runs", status_code=303)

        entry = db.get(PayrollEntry, e_id)
        if not entry or entry.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/runs", status_code=303)

        # Get associated slips
        search = self._form_text(request.query_params.get("search"))
        status_value = self._form_text(request.query_params.get("status"))
        status_filter = parse_slip_status(status_value)

        slips_query = (
            db.query(SalarySlip)
            .filter(SalarySlip.payroll_entry_id == e_id)
        )
        if search:
            like = f"%{search}%"
            slips_query = slips_query.filter(
                or_(
                    SalarySlip.employee_name.ilike(like),
                    SalarySlip.slip_number.ilike(like),
                )
            )
        if status_filter:
            slips_query = slips_query.filter(SalarySlip.status == status_filter)

        slips = slips_query.order_by(SalarySlip.employee_name).all()

        slip_status_counts = {}
        for status, count in (
            db.query(SalarySlip.status, func.count())
            .filter(SalarySlip.payroll_entry_id == e_id)
            .group_by(SalarySlip.status)
            .all()
        ):
            slip_status_counts[status.value] = count

        total_slips = sum(slip_status_counts.values())

        # Get active bank accounts for bank upload dropdown
        bank_accounts = (
            db.query(BankAccount)
            .filter(BankAccount.organization_id == org_id)
            .order_by(BankAccount.bank_name, BankAccount.account_name)
            .all()
        )

        context = base_context(request, auth, entry.entry_name or "Payroll Run", "payroll", db=db)
        context["request"] = request
        context.update({
            "entry": entry,
            "slips": slips,
            "bank_accounts": bank_accounts,
            "success": success,
            "error": error,
            "slip_search": search,
            "slip_status": status_value if status_filter else "",
            "slip_statuses": SLIP_STATUSES,
            "slip_status_counts": slip_status_counts,
            "filtered_slip_count": len(slips),
            "total_slip_count": total_slips,
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
                svc = PayrollService(db)
                svc.generate_salary_slips(
                    org_id,
                    e_id,
                    created_by_id=user_id,
                )
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
                svc = PayrollService(db)
                svc.regenerate_salary_slips(
                    org_id,
                    e_id,
                    created_by_id=user_id,
                )
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
                svc = PayrollService(db)
                svc.submit_payroll_entry(
                    org_id,
                    e_id,
                    submitted_by=user_id,
                )
                db.commit()
            except Exception as e:
                db.rollback()
                return RedirectResponse(
                    url=f"/people/payroll/runs/{entry_id}?error={quote(str(e))}",
                    status_code=303,
                )

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
                svc = PayrollService(db)
                svc.approve_payroll_entry(
                    org_id,
                    e_id,
                    approved_by=user_id,
                )
                db.commit()
            except Exception as e:
                db.rollback()
                return RedirectResponse(
                    url=f"/people/payroll/runs/{entry_id}?error={quote(str(e))}",
                    status_code=303,
                )

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
                svc = PayrollService(db)
                svc.handoff_payroll_to_books(
                    org_id,
                    e_id,
                    posting_date=parse_date(posting_date) or date.today(),
                    user_id=user_id,
                )
                db.commit()
            except Exception as e:
                db.rollback()
                return RedirectResponse(
                    url=f"/people/payroll/runs/{entry_id}?error={quote(str(e))}",
                    status_code=303,
                )

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

        designations = (
            db.query(Designation)
            .filter(
                Designation.organization_id == org_id,
                Designation.is_active == True,
                Designation.is_deleted == False,
            )
            .order_by(Designation.designation_name)
            .all()
        )

        structures = (
            db.query(SalaryStructure)
            .filter(SalaryStructure.organization_id == org_id, SalaryStructure.is_active == True)
            .order_by(SalaryStructure.structure_name)
            .all()
        )

        bank_accounts = (
            db.query(BankAccount)
            .filter(BankAccount.organization_id == org_id)
            .order_by(BankAccount.bank_name, BankAccount.account_name)
            .all()
        )

        expense_accounts = (
            db.query(Account)
            .filter(
                Account.organization_id == org_id,
                Account.is_active == True,
                Account.account_code.like("6%"),
            )
            .order_by(Account.account_code)
            .all()
        )

        today = date.today()
        frequency = self._get_default_frequency(db, org_id)
        default_start, default_end = self._get_default_period(frequency, today)
        start_date = parse_date(form_data.get("start_date")) if form_data else None
        end_date = parse_date(form_data.get("end_date")) if form_data else None
        posting_date = parse_date(form_data.get("posting_date")) if form_data else None
        effective_start = start_date or default_start
        assigned_count = self._count_active_assignments(db, org_id, effective_start)

        context = base_context(request, auth, "New Payroll Run", "payroll", db=db)
        context["request"] = request
        context.update({
            "entry": None,
            "run": None,
            "departments": departments,
            "designations": designations,
            "structures": structures,
            "bank_accounts": bank_accounts,
            "expense_accounts": expense_accounts,
            "current_year": today.year,
            "current_month": today.month,
            "frequencies": PAYROLL_FREQUENCIES,
            "assigned_count": assigned_count,
            "default_start": (start_date or default_start).isoformat(),
            "default_end": (end_date or default_end).isoformat(),
            "default_posting": (posting_date or today).isoformat(),
            "form_data": form_data,
            "error": error,
            "errors": {},
        })
        return templates.TemplateResponse(request, "people/payroll/run_form.html", context)

    def bank_upload_response(
        self,
        auth: WebAuthContext,
        db: Session,
        entry_id: str,
        source_account_id: Optional[str] = None,
    ):
        """
        Generate bank upload file for payroll run (Zenith Bank format).

        Args:
            auth: Web auth context
            db: Database session
            entry_id: Payroll entry ID
            source_account_id: Bank account ID for the debit account

        Returns:
            StreamingResponse with CSV file
        """
        from fastapi.responses import StreamingResponse
        import io

        from app.services.finance.banking.bank_upload import (
            BankUploadService,
            PaymentItem,
        )

        org_id = coerce_uuid(auth.organization_id)
        e_id = parse_uuid(entry_id)

        if not e_id:
            return RedirectResponse(url="/people/payroll/runs", status_code=303)

        entry = db.get(PayrollEntry, e_id)
        if not entry or entry.organization_id != org_id:
            return RedirectResponse(url="/people/payroll/runs", status_code=303)

        # Resolve source bank account (prefer run selection, fallback to query param)
        resolved_source_id = source_account_id or (str(entry.source_bank_account_id) if entry.source_bank_account_id else None)
        source_account_number = ""
        if resolved_source_id:
            sa_id = parse_uuid(resolved_source_id)
            if sa_id:
                source_bank = db.get(BankAccount, sa_id)
                if source_bank and source_bank.organization_id == org_id:
                    source_account_number = source_bank.account_number or ""
        if not source_account_number:
            return RedirectResponse(
                url=f"/people/payroll/runs/{entry_id}?error=Select a payment bank account to download the file",
                status_code=303,
            )

        # Get all salary slips for this entry
        slips = (
            db.query(SalarySlip)
            .filter(SalarySlip.payroll_entry_id == e_id)
            .filter(SalarySlip.net_pay > 0)
            .order_by(SalarySlip.employee_name)
            .all()
        )

        if not slips:
            return RedirectResponse(
                url=f"/people/payroll/runs/{entry_id}?error=No salary slips found",
                status_code=303,
            )

        # Convert slips to payment items
        payment_items: list[PaymentItem] = []
        for slip in slips:
            # Skip slips without bank details
            if not slip.bank_account_number:
                continue

            base_ref = slip.slip_number or f"SAL-{slip.slip_id.hex[:8].upper()}"
            suffix = slip.employee.employee_code if slip.employee and slip.employee.employee_code else slip.slip_id.hex[:6].upper()

            payment_items.append(
                PaymentItem(
                    reference=f"{base_ref}-{suffix}",
                    beneficiary_name=slip.bank_account_name or slip.employee_name or "Unknown",
                    amount=slip.net_pay,
                    account_number=slip.bank_account_number,
                    bank_name=slip.bank_name or "",
                    bank_code=slip.bank_branch_code,
                    beneficiary_code=slip.employee.employee_code if slip.employee else None,
                    narration=f"Salary {entry.payroll_month}/{entry.payroll_year}" if entry.payroll_month else "Salary Payment",
                )
            )

        if not payment_items:
            return RedirectResponse(
                url=f"/people/payroll/runs/{entry_id}?error=No slips with bank details found",
                status_code=303,
            )

        # Generate bank upload (Zenith format only)
        bank_service = BankUploadService(db)
        payment_date = entry.posting_date or date.today()

        result = bank_service.generate_upload(
            items=payment_items,
            source_account_number=source_account_number,
            payment_date=payment_date,
            bank_format="zenith",
            batch_reference=entry.entry_number,
        )

        # Generate filename with entry info
        entry_suffix = entry.entry_name.lower().replace(" ", "_") if entry.entry_name else entry.entry_number
        filename = f"bank_upload_zenith_{entry_suffix}_{payment_date.strftime('%Y%m%d')}.csv"

        # Return as downloadable file
        return StreamingResponse(
            io.BytesIO(result.content),
            media_type=result.content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Row-Count": str(result.row_count),
                "X-Total-Amount": str(result.total_amount),
            },
        )
