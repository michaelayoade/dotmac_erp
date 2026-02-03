"""
Self-service web view service for employees and managers.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import shutil
import uuid
from typing import Optional
from uuid import UUID
from urllib.parse import urlencode

from fastapi import HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.exp import ExpenseClaim, ExpenseClaimStatus, ExpenseClaimItem
from app.models.people.hr.employee import Employee
from app.models.people.leave import LeaveApplication, LeaveApplicationStatus
from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.finance.core_org.pfa_directory import PFADirectory
from app.models.person import Gender as PersonGender
from app.services.finance.banking.bank_directory import BankDirectoryService
from app.services.people.hr.info_change_service import InfoChangeService
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.attendance import AttendanceService
from app.services.people.expense import ExpenseService
from app.services.people.hr import EmployeeService
from app.services.people.hr.employee_types import EmployeeFilters
from app.services.people.leave import LeaveService
from app.services.people.payroll.paye_calculator import PAYECalculator
from app.services.common import ValidationError
from app.templates import templates
from app.web.deps import base_context, WebAuthContext


class SelfServiceWebService:
    """View service for employee self-service pages."""

    @staticmethod
    def _nigeria_states() -> list[str]:
        return [
            "Abia",
            "Adamawa",
            "Akwa Ibom",
            "Anambra",
            "Bauchi",
            "Bayelsa",
            "Benue",
            "Borno",
            "Cross River",
            "Delta",
            "Ebonyi",
            "Edo",
            "Ekiti",
            "Enugu",
            "Gombe",
            "Imo",
            "Jigawa",
            "Kaduna",
            "Kano",
            "Katsina",
            "Kebbi",
            "Kogi",
            "Kwara",
            "Lagos",
            "Nasarawa",
            "Niger",
            "Ogun",
            "Ondo",
            "Osun",
            "Oyo",
            "Plateau",
            "Rivers",
            "Sokoto",
            "Taraba",
            "Yobe",
            "Zamfara",
            "FCT",
        ]

    @staticmethod
    def _get_employee_id(
        db: Session, org_id: Optional[UUID], person_id: Optional[UUID]
    ) -> UUID:
        if org_id is None or person_id is None:
            raise HTTPException(
                status_code=403, detail="Missing organization or person context"
            )
        employee = (
            db.query(Employee)
            .filter(Employee.organization_id == org_id)
            .filter(Employee.person_id == person_id)
            .first()
        )
        if not employee:
            raise HTTPException(status_code=404, detail="Employee profile not found")
        return employee.employee_id

    @staticmethod
    def _employee_required_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        page_title: str,
        active_module: str,
        *,
        detail: Optional[str] = None,
    ) -> HTMLResponse:
        context = base_context(request, auth, page_title, active_module, db=db)
        context["has_team_approvals"] = False
        context["can_team_leave"] = False
        context["can_team_expenses"] = False
        context["error"] = detail or "Employee profile not found."
        return templates.TemplateResponse(
            request, "people/self/employee_required.html", context
        )

    @staticmethod
    def _has_team_approvals(
        db: Session,
        org_id: Optional[UUID],
        person_id: Optional[UUID],
        *,
        employee_id: Optional[UUID] = None,
    ) -> bool:
        if org_id is None or person_id is None:
            return False
        try:
            manager_employee_id = employee_id or SelfServiceWebService._get_employee_id(
                db, org_id, person_id
            )
        except HTTPException:
            return False

        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1),
        )
        return bool(reports.items)

    @staticmethod
    def _save_receipt_file(org_id: UUID, receipt_file: UploadFile) -> str:
        upload_dir = Path("uploads/people_expense_receipts") / str(org_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(receipt_file.filename or "").suffix
        file_name = f"{uuid.uuid4().hex}{suffix}"
        file_path = upload_dir / file_name
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(receipt_file.file, buffer)
        return str(file_path)

    @staticmethod
    def _get_tickets_for_dropdown(db: Session, org_id: UUID) -> list:
        """Get open/active support tickets for expense linking."""
        from app.models.support.ticket import Ticket, TicketStatus

        tickets = (
            db.execute(
                select(Ticket)
                .where(
                    Ticket.organization_id == org_id,
                    Ticket.status.in_(
                        [TicketStatus.OPEN, TicketStatus.REPLIED, TicketStatus.ON_HOLD]
                    ),
                )
                .order_by(Ticket.opening_date.desc())
                .limit(100)
            )
            .scalars()
            .all()
        )

        return [
            {
                "ticket_id": str(t.ticket_id),
                "ticket_number": t.ticket_number,
                "subject": t.subject,
            }
            for t in tickets
        ]

    @staticmethod
    def _get_projects_for_dropdown(db: Session, org_id: UUID) -> list:
        """Get active projects for expense linking."""
        try:
            from app.models.finance.core_org.project import Project, ProjectStatus

            projects = (
                db.execute(
                    select(Project)
                    .where(
                        Project.organization_id == org_id,
                        Project.status == ProjectStatus.ACTIVE,
                    )
                    .order_by(Project.project_code)
                )
                .scalars()
                .all()
            )

            return [
                {
                    "project_id": str(p.project_id),
                    "project_code": p.project_code,
                    "project_name": p.project_name,
                }
                for p in projects
            ]
        except Exception:
            # Project model may not exist
            return []

    def tax_info_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        success: Optional[str] = None,
        error: Optional[str] = None,
    ) -> HTMLResponse:
        """Self-service tax, bank, and personal info page."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "Tax & Bank Info",
                    "self-tax-info",
                    detail=exc.detail,
                )
            raise

        employee = (
            db.query(Employee)
            .options(joinedload(Employee.person))
            .filter(Employee.employee_id == employee_id)
            .first()
        )

        tax_profile = db.scalar(
            select(EmployeeTaxProfile)
            .where(
                EmployeeTaxProfile.employee_id == employee_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
            .order_by(EmployeeTaxProfile.effective_from.desc())
            .limit(1)
        )

        banks = BankDirectoryService(db).list_active_banks()
        pfas = list(
            db.scalars(
                select(PFADirectory)
                .where(PFADirectory.is_active.is_(True))
                .order_by(PFADirectory.pfa_name)
            ).all()
        )

        info_change_service = InfoChangeService(db)
        has_pending = info_change_service.has_pending_request(org_id, employee_id)
        recent_requests = info_change_service.get_employee_requests(
            org_id,
            employee_id,
            include_resolved=True,
            limit=10,
        )

        context = base_context(request, auth, "Tax & Bank Info", "self-tax-info", db=db)
        context.update(
            {
                "employee": employee,
                "person": employee.person if employee else None,
                "tax_profile": tax_profile,
                "banks": banks,
                "pfas": pfas,
                "nigeria_states": self._nigeria_states(),
                "has_pending": has_pending,
                "recent_requests": recent_requests,
                "success": success,
                "error": error,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(request, "people/self/tax_info.html", context)

    def tax_info_submit_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        payload: dict[str, Optional[object]],
    ) -> RedirectResponse:
        """Submit a change request for tax, bank, and personal info."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)

        info_change_service = InfoChangeService(db)
        if info_change_service.has_pending_request(org_id, employee_id):
            return RedirectResponse(
                url="/people/self/tax-info?error=You+already+have+a+pending+request",
                status_code=303,
            )

        employee = (
            db.query(Employee)
            .options(joinedload(Employee.person))
            .filter(Employee.employee_id == employee_id)
            .first()
        )
        if not employee:
            return RedirectResponse(
                url="/people/self/tax-info?error=Employee+profile+not+found",
                status_code=303,
            )

        person = employee.person
        tax_profile = db.scalar(
            select(EmployeeTaxProfile)
            .where(
                EmployeeTaxProfile.employee_id == employee_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
            .order_by(EmployeeTaxProfile.effective_from.desc())
            .limit(1)
        )

        def _normalize(value: Optional[object]) -> Optional[str]:
            if value is None:
                return None
            value = str(value).strip()
            if not value:
                return None
            if value.lower() in {"none", "null"}:
                return None
            return value

        proposed_changes: dict[str, object] = {}

        # Person fields
        if person:
            phone = _normalize(payload.get("phone"))
            if phone != (person.phone or None):
                proposed_changes["phone"] = phone

            dob = payload.get("date_of_birth")
            current_dob = person.date_of_birth
            if dob != current_dob:
                proposed_changes["date_of_birth"] = (
                    dob.isoformat() if isinstance(dob, date) else None
                )

            gender_value = _normalize(payload.get("gender"))
            if gender_value:
                try:
                    gender = PersonGender(gender_value)
                except Exception:
                    return RedirectResponse(
                        url="/people/self/tax-info?error=Invalid+gender+value",
                        status_code=303,
                    )
            else:
                gender = None
            if (person.gender or None) != gender:
                proposed_changes["gender"] = gender.value if gender else None

            for field in [
                "address_line1",
                "address_line2",
                "city",
                "region",
                "postal_code",
                "country_code",
            ]:
                new_val = _normalize(payload.get(field))
                if field == "country_code" and new_val:
                    new_val = new_val.upper()
                    if len(new_val) != 2:
                        return RedirectResponse(
                            url="/people/self/tax-info?error=Country+code+must+be+2+letters",
                            status_code=303,
                        )
                current_val = getattr(person, field)
                if new_val != (current_val or None):
                    proposed_changes[field] = new_val

        # Employee contact fields
        for field in [
            "personal_email",
            "personal_phone",
            "emergency_contact_name",
            "emergency_contact_phone",
        ]:
            new_val = _normalize(payload.get(field))
            current_val = getattr(employee, field)
            if new_val != (current_val or None):
                proposed_changes[field] = new_val

        # Bank fields
        for field in [
            "bank_name",
            "bank_account_number",
            "bank_account_name",
            "bank_branch_code",
        ]:
            new_val = _normalize(payload.get(field))
            current_val = getattr(employee, field)
            if new_val != (current_val or None):
                proposed_changes[field] = new_val

        # Tax/pension fields
        for field in ["tin", "tax_state", "rsa_pin", "pfa_code", "nhf_number"]:
            new_val = _normalize(payload.get(field))
            current_val = getattr(tax_profile, field) if tax_profile else None
            if new_val != (current_val or None):
                proposed_changes[field] = new_val

        if not proposed_changes:
            return RedirectResponse(
                url="/people/self/tax-info?error=No+changes+detected",
                status_code=303,
            )

        info_change_service.submit_change_request(
            organization_id=org_id,
            employee_id=employee_id,
            proposed_changes=proposed_changes,
        )
        db.commit()
        return RedirectResponse(
            url="/people/self/tax-info?success=Change+request+submitted",
            status_code=303,
        )

    @staticmethod
    def _get_tasks_for_dropdown(
        db: Session, org_id: UUID, project_id: Optional[str] = None
    ) -> list:
        """Get tasks for expense linking."""
        try:
            from app.models.pm.task import Task

            stmt = select(Task).where(
                Task.organization_id == org_id,
                Task.is_deleted == False,  # noqa: E712
            )
            if project_id:
                stmt = stmt.where(Task.project_id == coerce_uuid(project_id))
            tasks = db.execute(stmt.order_by(Task.task_code)).scalars().all()

            return [
                {
                    "task_id": str(t.task_id),
                    "task_code": t.task_code,
                    "task_name": t.task_name,
                    "project_id": str(t.project_id),
                }
                for t in tasks
            ]
        except Exception:
            return []

    def tickets_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        page: int = 1,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "My Tickets",
                    "self-tickets",
                    detail=exc.detail,
                )
            raise

        from app.services.support.ticket import ticket_service

        per_page = 20
        tickets, total = ticket_service.list_tickets(
            db,
            org_id,
            assigned_to_id=employee_id,
            page=page,
            per_page=per_page,
        )
        total_pages = (total + per_page - 1) // per_page

        context = base_context(request, auth, "My Tickets", "self-tickets", db=db)
        context.update(
            {
                "tickets": tickets,
                "page": page,
                "total": total,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(request, "people/self/tickets.html", context)

    def tasks_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        page: int = 1,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "My Tasks",
                    "self-tasks",
                    detail=exc.detail,
                )
            raise

        from app.services.pm.task_service import TaskService

        per_page = 20
        svc = TaskService(db, org_id)
        result = svc.list_tasks(
            assigned_to_id=employee_id,
            params=PaginationParams(offset=(page - 1) * per_page, limit=per_page),
        )
        total = result.total
        total_pages = (total + per_page - 1) // per_page

        context = base_context(request, auth, "My Tasks", "self-tasks", db=db)
        context.update(
            {
                "tasks": result.items,
                "page": page,
                "total": total,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(request, "people/self/tasks.html", context)

    def attendance_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        month: Optional[str] = None,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "My Attendance",
                    "self-attendance",
                    detail=exc.detail,
                )
            raise

        svc = AttendanceService(db)
        today = svc.get_org_today(org_id)
        today_record = svc.get_attendance_by_date(org_id, employee_id, today)

        if month:
            try:
                year, month_num = [int(part) for part in month.split("-", 1)]
            except ValueError as exc:
                raise HTTPException(
                    status_code=400, detail="Invalid month format"
                ) from exc
            summary = svc.get_employee_monthly_summary(
                org_id, employee_id, year, month_num
            )
        else:
            summary = svc.get_employee_monthly_summary(
                org_id, employee_id, today.year, today.month
            )

        recent = svc.list_attendance(
            org_id,
            employee_id=employee_id,
            from_date=today.replace(day=1),
            to_date=today,
            pagination=PaginationParams(offset=0, limit=10),
        )

        context = base_context(request, auth, "My Attendance", "self-attendance", db=db)
        context.update(
            {
                "today_record": today_record,
                "summary": summary,
                "recent_records": recent.items,
                "month": month,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(
            request, "people/self/attendance.html", context
        )

    def check_in_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        notes: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)
        try:
            AttendanceService(db).check_in(
                org_id,
                employee_id,
                check_in_time=None,
                notes=notes,
                latitude=latitude,
                longitude=longitude,
            )
        except ValidationError as exc:
            return RedirectResponse(
                url=f"/people/self/attendance?{urlencode({'error': exc.message})}",
                status_code=303,
            )
        db.commit()
        return RedirectResponse(url="/people/self/attendance", status_code=302)

    def check_out_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        notes: Optional[str] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)
        try:
            AttendanceService(db).check_out(
                org_id,
                employee_id,
                check_out_time=None,
                notes=notes,
                latitude=latitude,
                longitude=longitude,
            )
        except ValidationError as exc:
            return RedirectResponse(
                url=f"/people/self/attendance?{urlencode({'error': exc.message})}",
                status_code=303,
            )
        db.commit()
        return RedirectResponse(url="/people/self/attendance", status_code=302)

    def leave_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "My Leave",
                    "self-leave",
                    detail=exc.detail,
                )
            raise

        svc = LeaveService(db, auth)
        balances = svc.get_employee_balances(org_id, employee_id)
        applications = svc.list_applications(
            org_id,
            employee_id=employee_id,
            pagination=PaginationParams(offset=0, limit=15),
        )
        leave_types = svc.list_leave_types(
            org_id, is_active=True, pagination=None
        ).items

        context = base_context(request, auth, "My Leave", "self-leave", db=db)
        context.update(
            {
                "balances": balances,
                "applications": applications.items,
                "leave_types": leave_types,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(request, "people/self/leave.html", context)

    def leave_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        application_id: UUID,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "Leave Application",
                    "self-leave",
                    detail=exc.detail,
                )
            raise

        svc = LeaveService(db, auth)
        application = svc.get_application(org_id, application_id)
        if application.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        context = base_context(request, auth, "Leave Application", "self-leave", db=db)
        context["application"] = application
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(
            request, "people/self/leave_detail.html", context
        )

    def leave_cancel_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        application_id: UUID,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)

        svc = LeaveService(db, auth)
        application = svc.get_application(org_id, application_id)
        if application.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        svc.cancel_application(org_id, application_id)
        db.commit()
        return RedirectResponse(url="/people/self/leave", status_code=302)

    def leave_apply_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        leave_type_id: str,
        from_date: date,
        to_date: date,
        half_day: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)

        LeaveService(db, auth).create_application(
            org_id,
            employee_id=employee_id,
            leave_type_id=coerce_uuid(leave_type_id),
            from_date=from_date,
            to_date=to_date,
            half_day=half_day is not None,
            half_day_date=from_date if half_day else None,
            reason=reason,
        )
        db.commit()
        return RedirectResponse(url="/people/self/leave", status_code=302)

    def expenses_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "My Expenses",
                    "self-expenses",
                    detail=exc.detail,
                )
            raise

        employee = db.get(Employee, employee_id)
        svc = ExpenseService(db, auth)
        claims = svc.list_claims(
            org_id,
            employee_id=employee_id,
            pagination=PaginationParams(offset=0, limit=20),
        )
        categories = svc.list_categories(org_id, is_active=True, pagination=None).items

        # Get open/active tickets for dropdown
        tickets = self._get_tickets_for_dropdown(db, org_id)

        # Get projects for dropdown
        projects = self._get_projects_for_dropdown(db, org_id)

        selected_ticket_id = request.query_params.get("ticket_id")
        selected_project_id = request.query_params.get("project_id")
        selected_task_id = request.query_params.get("task_id")
        tasks = self._get_tasks_for_dropdown(db, org_id, selected_project_id)
        context = base_context(request, auth, "My Expenses", "self-expenses", db=db)
        context.update(
            {
                "claims": claims.items,
                "categories": categories,
                "tickets": tickets,
                "projects": projects,
                "tasks": tasks,
                "selected_ticket_id": selected_ticket_id,
                "selected_project_id": selected_project_id,
                "selected_task_id": selected_task_id,
                "employee_bank_code": (employee.bank_branch_code if employee else "")
                or "",
                "employee_bank_account_number": (
                    employee.bank_account_number if employee else ""
                )
                or "",
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(request, "people/self/expenses.html", context)

    def expense_claim_create_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        claim_date: date,
        purpose: str,
        expense_date: date,
        category_id: str,
        description: str,
        claimed_amount: str,
        recipient_bank_code: Optional[str] = None,
        recipient_account_number: Optional[str] = None,
        receipt_url: Optional[str] = None,
        receipt_number: Optional[str] = None,
        receipt_file: Optional[UploadFile] = None,
        submit_now: Optional[str] = None,
        project_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)

        try:
            amount = Decimal(claimed_amount)
        except (InvalidOperation, TypeError) as exc:
            raise HTTPException(
                status_code=400, detail="Invalid claimed amount"
            ) from exc

        resolved_receipt_url = receipt_url.strip() if receipt_url else None
        if receipt_file:
            resolved_receipt_url = self._save_receipt_file(org_id, receipt_file)

        svc = ExpenseService(db, auth)
        claim = svc.create_claim(
            org_id,
            employee_id=employee_id,
            claim_date=claim_date,
            purpose=purpose.strip(),
            project_id=coerce_uuid(project_id) if project_id else None,
            ticket_id=coerce_uuid(ticket_id) if ticket_id else None,
            task_id=coerce_uuid(task_id) if task_id else None,
            recipient_bank_code=recipient_bank_code,
            recipient_account_number=recipient_account_number,
            items=[
                {
                    "expense_date": expense_date,
                    "category_id": coerce_uuid(category_id),
                    "description": description.strip(),
                    "claimed_amount": amount,
                    "receipt_url": resolved_receipt_url,
                    "receipt_number": receipt_number.strip()
                    if receipt_number
                    else None,
                }
            ],
        )
        if submit_now:
            svc.submit_claim(org_id, claim.claim_id)
        db.commit()
        return RedirectResponse(url="/people/self/expenses", status_code=302)

    def expense_claim_edit_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        claim_id: UUID,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)

        svc = ExpenseService(db, auth)
        claim = svc.get_claim(org_id, claim_id)
        if claim.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        can_edit = claim.status == ExpenseClaimStatus.DRAFT
        categories = svc.list_categories(org_id, is_active=True, pagination=None).items

        # Get projects, tickets, tasks for dropdowns (same as create form)
        projects = self._get_projects_for_dropdown(db, org_id)
        tickets = self._get_tickets_for_dropdown(db, org_id)
        tasks = self._get_tasks_for_dropdown(
            db, org_id, str(claim.project_id) if claim.project_id else None
        )

        context = base_context(
            request, auth, "Edit Expense Claim", "self-expenses", db=db
        )
        context.update(
            {
                "claim": claim,
                "categories": categories,
                "can_edit": can_edit,
                "projects": projects,
                "tickets": tickets,
                "tasks": tasks,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(
            request, "people/self/expense_claim_edit.html", context
        )

    def expense_claim_update_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        claim_id: UUID,
        items: list[dict],
        recipient_bank_code: Optional[str] = None,
        recipient_account_number: Optional[str] = None,
        project_id: Optional[UUID] = None,
        ticket_id: Optional[UUID] = None,
        task_id: Optional[UUID] = None,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)

        svc = ExpenseService(db, auth)
        claim = svc.get_claim(org_id, claim_id)
        if claim.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        if claim.status != ExpenseClaimStatus.DRAFT:
            raise HTTPException(
                status_code=400, detail="Only draft claims can be edited"
            )

        svc.update_claim(
            org_id,
            claim_id,
            recipient_bank_code=recipient_bank_code,
            recipient_account_number=recipient_account_number,
            project_id=project_id,
            ticket_id=ticket_id,
            task_id=task_id,
        )

        for item in items:
            if item.get("remove"):
                svc.remove_claim_item(
                    org_id,
                    claim_id=claim_id,
                    item_id=coerce_uuid(item["item_id"]),
                )
                continue

            svc.update_claim_item(
                org_id,
                claim_id=claim_id,
                item_id=coerce_uuid(item["item_id"]),
                expense_date=item["expense_date"],
                category_id=coerce_uuid(item["category_id"]),
                description=item["description"],
                claimed_amount=item["claimed_amount"],
                receipt_number=item.get("receipt_number"),
                receipt_url=item.get("receipt_url"),
            )

        db.commit()
        return RedirectResponse(url="/people/self/expenses", status_code=302)

    def team_leave_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        status: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            manager_employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "Team Leave",
                    "self-team-leave",
                    detail=exc.detail,
                )
            raise

        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = [emp.employee_id for emp in reports]
        items = []
        total = 0
        pagination = PaginationParams.from_page(page, per_page=20)

        if report_ids:
            query = select(LeaveApplication).where(
                LeaveApplication.organization_id == org_id,
                LeaveApplication.employee_id.in_(report_ids),
            )
            if status:
                try:
                    status_value = LeaveApplicationStatus(status)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400, detail="Invalid status"
                    ) from exc
                query = query.where(LeaveApplication.status == status_value)
            query = query.order_by(LeaveApplication.from_date.desc())
            count_query = select(func.count()).select_from(query.subquery())
            total = db.scalar(count_query) or 0
            items = list(
                db.scalars(
                    query.offset(pagination.offset).limit(pagination.limit)
                ).all()
            )

        total_pages = (total + pagination.limit - 1) // pagination.limit if total else 1
        context = base_context(request, auth, "Team Leave", "self-team-leave", db=db)
        context.update(
            {
                "applications": items,
                "status": status,
                "statuses": [s.value for s in LeaveApplicationStatus],
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "has_prev": page > 1,
                "has_next": pagination.offset + pagination.limit < total,
            }
        )
        context["has_team_approvals"] = True
        context["can_team_leave"] = True
        context["can_team_expenses"] = True
        return templates.TemplateResponse(
            request, "people/self/team_leave.html", context
        )

    def team_leave_approve_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        application_id: UUID,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        manager_employee_id = self._get_employee_id(db, org_id, person_id)

        application = LeaveService(db, auth).get_application(org_id, application_id)
        if application.employee_id == manager_employee_id:
            raise HTTPException(status_code=400, detail="Cannot approve own leave")

        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = {emp.employee_id for emp in reports}
        if application.employee_id not in report_ids:
            raise HTTPException(status_code=403, detail="Forbidden")

        LeaveService(db, auth).approve_application(
            org_id=org_id,
            application_id=application_id,
            approver_id=person_id,
        )
        db.commit()
        return RedirectResponse(url="/people/self/team/leave", status_code=302)

    def team_leave_reject_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        application_id: UUID,
        reason: Optional[str] = None,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        manager_employee_id = self._get_employee_id(db, org_id, person_id)

        application = LeaveService(db, auth).get_application(org_id, application_id)
        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = {emp.employee_id for emp in reports}
        if application.employee_id not in report_ids:
            raise HTTPException(status_code=403, detail="Forbidden")

        LeaveService(db, auth).reject_application(
            org_id=org_id,
            application_id=application_id,
            approver_id=person_id,
            reason=reason or "Rejected",
        )
        db.commit()
        return RedirectResponse(url="/people/self/team/leave", status_code=302)

    def team_expenses_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        status: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            manager_employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "Team Expenses",
                    "self-team-expenses",
                    detail=exc.detail,
                )
            raise

        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = [emp.employee_id for emp in reports]
        items = []
        total = 0
        pagination = PaginationParams.from_page(page, per_page=20)

        if report_ids:
            query = (
                select(ExpenseClaim)
                .options(
                    joinedload(ExpenseClaim.items).joinedload(
                        ExpenseClaimItem.category
                    ),
                    joinedload(ExpenseClaim.employee),
                )
                .where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.employee_id.in_(report_ids),
                )
            )
            if status:
                try:
                    status_value = ExpenseClaimStatus(status)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400, detail="Invalid status"
                    ) from exc
                query = query.where(ExpenseClaim.status == status_value)
            query = query.order_by(ExpenseClaim.claim_date.desc())
            count_query = select(func.count()).select_from(
                select(ExpenseClaim)
                .where(
                    ExpenseClaim.organization_id == org_id,
                    ExpenseClaim.employee_id.in_(report_ids),
                )
                .subquery()
            )
            if status:
                count_query = select(func.count()).select_from(
                    select(ExpenseClaim)
                    .where(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.employee_id.in_(report_ids),
                        ExpenseClaim.status == status_value,
                    )
                    .subquery()
                )
            total = db.scalar(count_query) or 0
            items = list(
                db.scalars(query.offset(pagination.offset).limit(pagination.limit))
                .unique()
                .all()
            )

        total_pages = (total + pagination.limit - 1) // pagination.limit if total else 1
        context = base_context(
            request, auth, "Team Expenses", "self-team-expenses", db=db
        )
        context.update(
            {
                "claims": items,
                "status": status,
                "statuses": [s.value for s in ExpenseClaimStatus],
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "has_prev": page > 1,
                "has_next": pagination.offset + pagination.limit < total,
            }
        )
        context["has_team_approvals"] = True
        context["can_team_leave"] = True
        context["can_team_expenses"] = True
        return templates.TemplateResponse(
            request, "people/self/team_expenses.html", context
        )

    def team_expense_approve_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        claim_id: UUID,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        manager_employee_id = self._get_employee_id(db, org_id, person_id)

        claim = ExpenseService(db, auth).get_claim(org_id, claim_id)
        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = {emp.employee_id for emp in reports}
        if claim.employee_id not in report_ids:
            raise HTTPException(status_code=403, detail="Forbidden")

        ExpenseService(db, auth).approve_claim(
            org_id=org_id,
            claim_id=claim_id,
            approver_id=person_id,
        )
        db.commit()
        return RedirectResponse(url="/people/self/team/expenses", status_code=302)

    def team_expense_reject_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        claim_id: UUID,
        reason: Optional[str] = None,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        manager_employee_id = self._get_employee_id(db, org_id, person_id)

        claim = ExpenseService(db, auth).get_claim(org_id, claim_id)
        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = {emp.employee_id for emp in reports}
        if claim.employee_id not in report_ids:
            raise HTTPException(status_code=403, detail="Forbidden")

        ExpenseService(db, auth).reject_claim(
            org_id=org_id,
            claim_id=claim_id,
            approver_id=person_id,
            reason=reason or "Rejected",
        )
        db.commit()
        return RedirectResponse(url="/people/self/team/expenses", status_code=302)

    # ============ Payslips Self-Service ============

    def payslips_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        year: Optional[int] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Self-service payslips list page."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "My Payslips",
                    "self-payslips",
                    detail=exc.detail,
                )
            raise

        pagination = PaginationParams.from_page(page, per_page=12)

        # Query salary slips for this employee
        query = db.query(SalarySlip).filter(
            SalarySlip.organization_id == org_id,
            SalarySlip.employee_id == employee_id,
            SalarySlip.status.in_(
                [
                    SalarySlipStatus.POSTED,
                    SalarySlipStatus.PAID,
                ]
            ),
        )

        if year:
            query = query.filter(func.extract("year", SalarySlip.start_date) == year)

        query = query.order_by(SalarySlip.start_date.desc())

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = db.scalar(count_query) or 0

        # Get paginated items
        slips = query.offset(pagination.offset).limit(pagination.limit).all()

        # Get available years for filtering
        years_query = (
            db.query(func.distinct(func.extract("year", SalarySlip.start_date)))
            .filter(
                SalarySlip.organization_id == org_id,
                SalarySlip.employee_id == employee_id,
                SalarySlip.status.in_([SalarySlipStatus.POSTED, SalarySlipStatus.PAID]),
            )
            .order_by(func.extract("year", SalarySlip.start_date).desc())
        )
        available_years = [int(y[0]) for y in years_query.all() if y[0]]

        total_pages = (total + pagination.limit - 1) // pagination.limit if total else 1

        context = base_context(request, auth, "My Payslips", "self-payslips", db=db)
        context.update(
            {
                "slips": slips,
                "year": year,
                "available_years": available_years,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "has_prev": page > 1,
                "has_next": pagination.offset + pagination.limit < total,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(request, "people/self/payslips.html", context)

    def payslip_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        slip_id: UUID,
    ) -> HTMLResponse:
        """Self-service payslip detail page with PAYE breakdown."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "Payslip Detail",
                    "self-payslips",
                    detail=exc.detail,
                )
            raise

        # Get the salary slip
        slip = db.get(SalarySlip, slip_id)
        if not slip or slip.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payslip not found")

        # Ensure employee can only view their own slips
        if slip.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Only show posted/paid slips in self-service
        if slip.status not in [SalarySlipStatus.POSTED, SalarySlipStatus.PAID]:
            raise HTTPException(status_code=403, detail="Payslip not yet available")

        # Calculate PAYE breakdown for display
        paye_breakdown = None
        if slip.gross_pay and slip.gross_pay > 0:
            # Find basic pay from earnings
            basic_pay = Decimal("0")
            for earning in slip.earnings:
                if earning.abbr and earning.abbr.upper() in ("BAS", "BASIC"):
                    basic_pay = earning.amount
                    break

            # If no BASIC found, estimate from gross (60% assumption)
            if basic_pay == 0:
                basic_pay = slip.gross_pay * Decimal("0.6")

            calculator = PAYECalculator(db)
            paye_breakdown = calculator.calculate(
                organization_id=org_id,
                gross_monthly=slip.gross_pay,
                basic_monthly=basic_pay,
                employee_id=employee_id,
                as_of_date=slip.start_date,
            )

        context = base_context(
            request, auth, f"Payslip {slip.slip_number}", "self-payslips", db=db
        )
        context.update(
            {
                "slip": slip,
                "paye_breakdown": paye_breakdown,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(
            request, "people/self/payslip_detail.html", context
        )

    # =========================================================================
    # Discipline Self-Service
    # =========================================================================

    def discipline_cases_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        include_closed: bool = False,
    ) -> HTMLResponse:
        """Self-service disciplinary cases list."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException:
            return self._employee_required_response(
                request, auth, db, "Discipline", "self-discipline"
            )

        from app.services.people.discipline import DisciplineService

        discipline_service = DisciplineService(db)
        cases, _total = discipline_service.list_employee_cases(
            org_id, employee_id, include_closed=include_closed
        )

        # Mark cases that need response
        for case in cases:
            setattr(
                case,
                "has_pending_response",
                case.status.value == "QUERY_ISSUED"
                and case.response_due_date is not None,
            )

        context = base_context(request, auth, "Discipline", "self-discipline", db=db)
        context.update(
            {
                "cases": cases,
                "include_closed": include_closed,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(
            request, "people/self/discipline.html", context
        )

    def discipline_case_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        case_id: UUID,
    ) -> HTMLResponse:
        """Self-service disciplinary case detail view."""
        org_id = auth.organization_id
        person_id = auth.person_id

        try:
            employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException:
            return self._employee_required_response(
                request, auth, db, "Discipline", "self-discipline"
            )

        from app.services.people.discipline import DisciplineService
        from app.models.people.discipline import CaseStatus

        discipline_service = DisciplineService(db)
        try:
            case = discipline_service.get_case_detail(case_id)
        except Exception:
            raise HTTPException(status_code=404, detail="Case not found")

        # Verify this is the employee's own case
        if case.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Determine what actions are available
        can_respond = case.status == CaseStatus.QUERY_ISSUED
        can_appeal = (
            case.status == CaseStatus.DECISION_MADE
            and case.appeal_deadline is not None
            and date.today() <= case.appeal_deadline
        )

        context = base_context(
            request, auth, f"Case {case.case_number}", "self-discipline", db=db
        )
        context.update(
            {
                "case": case,
                "can_respond": can_respond,
                "can_appeal": can_appeal,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = context["has_team_approvals"]
        return templates.TemplateResponse(
            request, "people/self/discipline_detail.html", context
        )

    def discipline_submit_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        case_id: UUID,
        response_text: str,
    ) -> RedirectResponse:
        """Submit employee response to disciplinary query."""
        org_id = auth.organization_id
        person_id = auth.person_id

        employee_id = self._get_employee_id(db, org_id, person_id)

        from app.services.people.discipline import DisciplineService
        from app.schemas.people.discipline import CaseResponseCreate

        discipline_service = DisciplineService(db)
        case = discipline_service.get_case_or_404(case_id)

        # Verify this is the employee's own case
        if case.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        response_data = CaseResponseCreate(response_text=response_text)
        discipline_service.record_response(case_id, response_data)
        db.commit()

        return RedirectResponse(
            url=f"/people/self/discipline/{case_id}?success=response_submitted",
            status_code=303,
        )

    def discipline_file_appeal_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        case_id: UUID,
        appeal_reason: str,
    ) -> RedirectResponse:
        """File an appeal against disciplinary decision."""
        org_id = auth.organization_id
        person_id = auth.person_id

        employee_id = self._get_employee_id(db, org_id, person_id)

        from app.services.people.discipline import DisciplineService
        from app.schemas.people.discipline import FileAppealRequest

        discipline_service = DisciplineService(db)
        case = discipline_service.get_case_or_404(case_id)

        # Verify this is the employee's own case
        if case.employee_id != employee_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        appeal_data = FileAppealRequest(appeal_reason=appeal_reason)
        discipline_service.file_appeal(case_id, appeal_data)
        db.commit()

        return RedirectResponse(
            url=f"/people/self/discipline/{case_id}?success=appeal_filed",
            status_code=303,
        )


self_service_web_service = SelfServiceWebService()
