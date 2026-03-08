"""
Self-service web view service for employees and managers.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote, urlencode
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload
from starlette.datastructures import UploadFile

from app.models.finance.core_org.pfa_directory import PFADirectory
from app.models.people.exp import (
    ExpenseClaim,
    ExpenseClaimAction,
    ExpenseClaimActionStatus,
    ExpenseClaimActionType,
    ExpenseClaimStatus,
)
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.leave import LeaveApplication, LeaveApplicationStatus
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus
from app.models.people.scheduling import ScheduleStatus, SwapRequestStatus
from app.models.people.scheduling.shift_schedule import ShiftSchedule
from app.models.person import Gender as PersonGender
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.services.common import PaginationParams, ValidationError, coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.expense.limit_service import ExpenseLimitService
from app.services.finance.banking.bank_directory import BankDirectoryService
from app.services.people.attendance import AttendanceService
from app.services.people.attendance.attendance_service import AttendanceServiceError
from app.services.people.expense import ExpenseService
from app.services.people.hr import EmployeeService
from app.services.people.hr.employee_types import EmployeeFilters
from app.services.people.hr.info_change_service import InfoChangeService
from app.services.people.leave import LeaveService
from app.services.people.payroll.paye_calculator import PAYECalculator
from app.services.people.scheduling import SchedulingService, SwapService
from app.services.settings.bank_directory import OrgBankDirectoryService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class SelfServiceWebService:
    """View service for employee self-service pages."""

    @staticmethod
    def _has_named_role(db: Session, person_id: UUID, role_names: set[str]) -> bool:
        normalized_names = {name.strip().lower() for name in role_names if name}
        if not normalized_names:
            return False
        rows = db.execute(
            select(Role.name)
            .join(PersonRole, PersonRole.role_id == Role.id)
            .where(
                PersonRole.person_id == person_id,
                Role.is_active == True,  # noqa: E712
            )
        ).all()
        for (raw_name,) in rows:
            if not raw_name:
                continue
            n = raw_name.strip().lower()
            if n in normalized_names or n.replace(" ", "_") in normalized_names:
                return True
        return False

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
        db: Session, org_id: UUID | None, person_id: UUID | None
    ) -> UUID:
        if org_id is None or person_id is None:
            raise HTTPException(
                status_code=403, detail="Missing organization or person context"
            )
        employee = db.scalar(
            select(Employee).where(
                Employee.organization_id == org_id,
                Employee.person_id == person_id,
            )
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
        detail: str | None = None,
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
    def _get_expense_approver_options(db: Session, org_id: UUID) -> list[dict]:
        """Return active employee options with admin or expense_approver roles."""
        roles = db.scalars(
            select(Role).where(
                Role.is_active == True, Role.name.in_(["admin", "expense_approver"])
            )
        ).all()
        role_ids = [role.id for role in roles]
        if not role_ids:
            return []

        rows = db.execute(
            select(Employee, PersonRole, Person)
            .join(PersonRole, PersonRole.person_id == Employee.person_id)
            .join(Person, Person.id == Employee.person_id)
            .where(
                Employee.organization_id == org_id,
                Employee.status == EmployeeStatus.ACTIVE,
                PersonRole.role_id.in_(role_ids),
            )
            .order_by(Person.first_name, Person.last_name)
        ).all()

        options = {}
        for employee, _, person in rows:
            label = ""
            if person:
                if person.name:
                    label = person.name
                else:
                    label = (
                        f"{person.first_name or ''} {person.last_name or ''}".strip()
                    )
            if employee.employee_code:
                label = (
                    f"{label} ({employee.employee_code})"
                    if label
                    else employee.employee_code
                )
            options[str(employee.employee_id)] = {
                "id": str(employee.employee_id),
                "label": label or "Unnamed",
            }

        return list(options.values())

    @staticmethod
    def _has_team_approvals(
        db: Session,
        org_id: UUID | None,
        person_id: UUID | None,
        *,
        employee_id: UUID | None = None,
    ) -> bool:
        if org_id is None or person_id is None:
            return False
        has_leave_role = SelfServiceWebService._has_named_role(
            db,
            person_id,
            {"admin", "leave_approver", "Leave approver"},
        )
        if has_leave_role:
            return True
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
    def _has_team_expense_approvals(
        db: Session,
        org_id: UUID | None,
        person_id: UUID | None,
        *,
        employee_id: UUID | None = None,
    ) -> bool:
        if org_id is None or person_id is None:
            return False
        has_expense_role = SelfServiceWebService._has_named_role(
            db,
            person_id,
            {"admin", "expense_approver", "Expense approver"},
        )
        if has_expense_role:
            return True
        try:
            approver_employee_id = (
                employee_id
                or SelfServiceWebService._get_employee_id(db, org_id, person_id)
            )
        except HTTPException:
            return False

        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(expense_approver_id=approver_employee_id),
            pagination=PaginationParams(offset=0, limit=1),
        )
        return bool(reports.items)

    @staticmethod
    def _save_receipt_file(org_id: UUID, receipt_file: UploadFile) -> str:
        from app.services.file_upload import FileUploadError, get_expense_receipt_upload

        svc = get_expense_receipt_upload()
        file_data = receipt_file.file.read()
        try:
            result = svc.save(
                file_data=file_data,
                content_type=receipt_file.content_type,
                subdirs=(str(org_id),),
                original_filename=receipt_file.filename,
            )
        except FileUploadError:
            raise
        return str(result.file_path)

    @staticmethod
    def _get_tickets_for_dropdown(db: Session, org_id: UUID) -> list[dict]:
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
    def _get_projects_for_dropdown(db: Session, org_id: UUID) -> list[dict]:
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
        success: str | None = None,
        error: str | None = None,
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

        employee = db.scalar(
            select(Employee)
            .options(joinedload(Employee.person))
            .where(
                Employee.organization_id == org_id,
                Employee.employee_id == employee_id,
            )
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
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        return templates.TemplateResponse(request, "people/self/tax_info.html", context)

    def tax_info_submit_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        payload: dict[str, object | None],
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

        employee = db.scalar(
            select(Employee)
            .options(joinedload(Employee.person))
            .where(
                Employee.organization_id == org_id,
                Employee.employee_id == employee_id,
            )
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

        def _normalize(value: object | None) -> str | None:
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
        db: Session, org_id: UUID, project_id: str | None = None
    ) -> list[dict]:
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
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
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
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        return templates.TemplateResponse(request, "people/self/tasks.html", context)

    def attendance_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        month: str | None = None,
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
        org_tzinfo = svc.get_org_tzinfo(org_id)

        def _format_time(value: datetime | None) -> str:
            if not value:
                return "-"
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(org_tzinfo).strftime("%H:%M")

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
                "today_check_in_display": _format_time(
                    today_record.check_in if today_record else None
                ),
                "today_check_out_display": _format_time(
                    today_record.check_out if today_record else None
                ),
                "summary": summary,
                "recent_records": [
                    {
                        "attendance_date": rec.attendance_date,
                        "status": rec.status,
                        "check_in_display": _format_time(rec.check_in),
                        "check_out_display": _format_time(rec.check_out),
                        "working_hours": rec.working_hours,
                    }
                    for rec in recent.items
                ],
                "month": month,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        return templates.TemplateResponse(
            request, "people/self/attendance.html", context
        )

    def check_in_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        notes: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
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
        except AttendanceServiceError as exc:
            return RedirectResponse(
                url=f"/people/self/attendance?{urlencode({'error': str(exc)})}",
                status_code=303,
            )
        db.commit()
        return RedirectResponse(url="/people/self/attendance", status_code=302)

    def check_out_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        notes: str | None = None,
        latitude: float | None = None,
        longitude: float | None = None,
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
        except AttendanceServiceError as exc:
            return RedirectResponse(
                url=f"/people/self/attendance?{urlencode({'error': str(exc)})}",
                status_code=303,
            )
        db.commit()
        return RedirectResponse(url="/people/self/attendance", status_code=302)

    def scheduling_schedules_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        year_month: str | None = None,
    ) -> HTMLResponse:
        """Self-service monthly schedule view."""
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
                    "My Schedule",
                    "self-attendance",
                    detail=exc.detail,
                )
            raise

        resolved_month = year_month or date.today().strftime("%Y-%m")
        svc = SchedulingService(db)
        schedules = svc.list_schedules(
            org_id=org_id,
            employee_id=employee_id,
            schedule_month=resolved_month,
            pagination=PaginationParams(offset=0, limit=200),
        )

        context = base_context(request, auth, "My Schedule", "self-attendance", db=db)
        context.update(
            {
                "year_month": resolved_month,
                "schedules": schedules.items,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        return templates.TemplateResponse(
            request, "people/self/scheduling_schedules.html", context
        )

    def scheduling_swaps_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        year_month: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Self-service swap requests page."""
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
                    "My Shift Swaps",
                    "self-attendance",
                    detail=exc.detail,
                )
            raise

        resolved_month = year_month or date.today().strftime("%Y-%m")
        pager = PaginationParams.from_page(page, per_page=20)
        swap_svc = SwapService(db)
        my_requests = swap_svc.get_my_requests(
            org_id=org_id,
            employee_id=employee_id,
            pagination=pager,
        )
        pending_acceptance = swap_svc.get_pending_acceptance(
            org_id=org_id,
            employee_id=employee_id,
            pagination=PaginationParams(offset=0, limit=50),
        )

        my_schedules = list(
            db.scalars(
                select(ShiftSchedule).where(
                    ShiftSchedule.organization_id == org_id,
                    ShiftSchedule.employee_id == employee_id,
                    ShiftSchedule.schedule_month == resolved_month,
                    ShiftSchedule.status == ScheduleStatus.PUBLISHED,
                )
            ).all()
        )
        my_schedule_ids = {s.shift_schedule_id for s in my_schedules}

        coworker_schedules = list(
            db.scalars(
                select(ShiftSchedule)
                .where(
                    ShiftSchedule.organization_id == org_id,
                    ShiftSchedule.schedule_month == resolved_month,
                    ShiftSchedule.status == ScheduleStatus.PUBLISHED,
                    ShiftSchedule.employee_id != employee_id,
                )
                .order_by(ShiftSchedule.shift_date, ShiftSchedule.employee_id)
                .limit(400)
            ).all()
        )
        employee_ids = {s.employee_id for s in coworker_schedules}
        employees = list(
            db.scalars(
                select(Employee).where(Employee.employee_id.in_(employee_ids))
            ).all()
        )
        employee_map = {
            emp.employee_id: (
                emp.full_name or emp.employee_code or str(emp.employee_id)
            )
            for emp in employees
        }

        target_options = [
            {
                "id": str(s.shift_schedule_id),
                "label": f"{employee_map.get(s.employee_id, str(s.employee_id))} - {s.shift_date.isoformat()}",
            }
            for s in coworker_schedules
        ]

        context = base_context(
            request, auth, "My Shift Swaps", "self-attendance", db=db
        )
        context.update(
            {
                "year_month": resolved_month,
                "my_requests": my_requests.items,
                "pending_acceptance": pending_acceptance.items,
                "my_schedule_options": my_schedules,
                "target_schedule_options": target_options,
                "my_schedule_ids": {str(sid) for sid in my_schedule_ids},
                "swap_statuses": [s.value for s in SwapRequestStatus],
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        return templates.TemplateResponse(
            request, "people/self/scheduling_swaps.html", context
        )

    def scheduling_create_swap_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        form: dict,
    ) -> RedirectResponse:
        """Create swap request from self-service page."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)
        year_month = str(form.get("year_month") or date.today().strftime("%Y-%m"))
        try:
            requester_schedule_id = coerce_uuid(
                str(form.get("requester_schedule_id", ""))
            )
            target_schedule_id = coerce_uuid(str(form.get("target_schedule_id", "")))
        except Exception:
            return RedirectResponse(
                f"/people/self/scheduling/swaps?year_month={year_month}&error={quote('Invalid schedule selection')}",
                status_code=303,
            )
        reason_raw = form.get("reason")
        reason = str(reason_raw).strip() if isinstance(reason_raw, str) else None
        if not requester_schedule_id or not target_schedule_id:
            return RedirectResponse(
                f"/people/self/scheduling/swaps?year_month={year_month}&error={quote('Both schedules are required')}",
                status_code=303,
            )
        try:
            SwapService(db).create_swap_request(
                org_id=org_id,
                requester_id=employee_id,
                requester_schedule_id=requester_schedule_id,
                target_schedule_id=target_schedule_id,
                reason=reason,
            )
            db.commit()
            return RedirectResponse(
                f"/people/self/scheduling/swaps?year_month={year_month}&success={quote('Swap request submitted')}",
                status_code=303,
            )
        except Exception as exc:
            db.rollback()
            return RedirectResponse(
                f"/people/self/scheduling/swaps?year_month={year_month}&error={quote(str(exc))}",
                status_code=303,
            )

    def scheduling_accept_swap_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        request_id: UUID,
    ) -> RedirectResponse:
        """Accept a pending swap request as target employee."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)
        try:
            SwapService(db).accept_swap_request(
                org_id=org_id,
                request_id=request_id,
                accepting_employee_id=employee_id,
            )
            db.commit()
            return RedirectResponse(
                "/people/self/scheduling/swaps?success=accepted",
                status_code=303,
            )
        except Exception as exc:
            db.rollback()
            return RedirectResponse(
                f"/people/self/scheduling/swaps?error={quote(str(exc))}",
                status_code=303,
            )

    def scheduling_decline_swap_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        request_id: UUID,
        form: dict,
    ) -> RedirectResponse:
        """Decline a pending swap request as target employee."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)
        reason_raw = form.get("reason")
        reason = str(reason_raw).strip() if isinstance(reason_raw, str) else None
        try:
            SwapService(db).decline_swap_request(
                org_id=org_id,
                request_id=request_id,
                declining_employee_id=employee_id,
                reason=reason,
            )
            db.commit()
            return RedirectResponse(
                "/people/self/scheduling/swaps?success=declined",
                status_code=303,
            )
        except Exception as exc:
            db.rollback()
            return RedirectResponse(
                f"/people/self/scheduling/swaps?error={quote(str(exc))}",
                status_code=303,
            )

    def scheduling_cancel_swap_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        request_id: UUID,
    ) -> RedirectResponse:
        """Cancel own swap request."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        employee_id = self._get_employee_id(db, org_id, person_id)
        try:
            SwapService(db).cancel_swap_request(
                org_id=org_id,
                request_id=request_id,
                requester_id=employee_id,
            )
            db.commit()
            return RedirectResponse(
                "/people/self/scheduling/swaps?success=cancelled",
                status_code=303,
            )
        except Exception as exc:
            db.rollback()
            return RedirectResponse(
                f"/people/self/scheduling/swaps?error={quote(str(exc))}",
                status_code=303,
            )

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
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
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
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
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
        half_day: str | None = None,
        reason: str | None = None,
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

        # Get cost centers for dropdown
        from app.models.finance.core_org.cost_center import CostCenter

        cost_centers_stmt = select(CostCenter).where(
            CostCenter.organization_id == org_id,
            CostCenter.is_active.is_(True),
        ).order_by(CostCenter.cost_center_code)
        cost_centers = list(db.scalars(cost_centers_stmt).all())

        allowed_banks = OrgBankDirectoryService(db).list_active_banks(org_id)

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
                "cost_centers": cost_centers,
                "tasks": tasks,
                "selected_ticket_id": selected_ticket_id,
                "selected_project_id": selected_project_id,
                "selected_task_id": selected_task_id,
                "employee_bank_code": (employee.bank_branch_code if employee else "")
                or "",
                "employee_bank_name": (employee.bank_name if employee else "") or "",
                "employee_bank_account_number": (
                    employee.bank_account_number if employee else ""
                )
                or "",
                "employee_recipient_name": (employee.full_name if employee else "")
                or "",
                "allowed_banks": allowed_banks,
                "expense_approver_options": self._get_expense_approver_options(
                    db, org_id
                ),
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
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
        recipient_bank_code: str | None = None,
        recipient_bank_name: str | None = None,
        recipient_account_number: str | None = None,
        recipient_name: str | None = None,
        requested_approver_id: str | None = None,
        receipt_url: str | None = None,
        receipt_number: str | None = None,
        receipt_files: list[UploadFile] | None = None,
        receipt_file: UploadFile | None = None,
        submit_now: str | None = None,
        project_id: str | None = None,
        ticket_id: str | None = None,
        task_id: str | None = None,
        cost_center_id: str | None = None,
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

        resolved_receipt_urls: list[str] = []
        if receipt_url and receipt_url.strip():
            resolved_receipt_urls.append(receipt_url.strip())

        upload_files: list[UploadFile] = []
        if receipt_files:
            upload_files.extend(
                f
                for f in receipt_files
                if isinstance(f, UploadFile) and getattr(f, "filename", None)
            )
        if (
            receipt_file
            and isinstance(receipt_file, UploadFile)
            and getattr(receipt_file, "filename", None)
            and receipt_file not in upload_files
        ):
            upload_files.append(receipt_file)

        if upload_files:
            # File uploads can fail validation (e.g. unsupported MIME type).
            # For self-service web flows, redirect back with a user-visible error
            # toast instead of raising an unhandled exception (500).
            from app.services.file_upload import (
                FileUploadError,
                get_expense_receipt_upload,
            )

            upload_svc = get_expense_receipt_upload()
            uploaded_paths: list[str] = []
            try:
                for upload in upload_files:
                    # Note: UploadFile.file is a SpooledTemporaryFile; reading consumes it.
                    file_data = upload.file.read()
                    result = upload_svc.save(
                        file_data=file_data,
                        content_type=upload.content_type,
                        subdirs=(str(org_id),),
                        original_filename=upload.filename,
                    )
                    uploaded_paths.append(str(result.file_path))
                    resolved_receipt_urls.append(str(result.file_path))
            except FileUploadError as exc:
                # Best-effort cleanup of any earlier uploads in this request.
                for path in uploaded_paths:
                    try:
                        upload_svc.delete(path)
                    except Exception:
                        logger.exception(
                            "Failed to cleanup orphaned receipt upload",
                            extra={
                                "organization_id": str(org_id),
                                "path": path,
                            },
                        )
                return RedirectResponse(
                    url=f"/people/self/expenses?error={quote(str(exc))}",
                    status_code=303,
                )

        resolved_receipt_url: str | None
        if not resolved_receipt_urls:
            resolved_receipt_url = None
        elif len(resolved_receipt_urls) == 1:
            resolved_receipt_url = resolved_receipt_urls[0]
        else:
            resolved_receipt_url = json.dumps(resolved_receipt_urls)

        svc = ExpenseService(db, auth)
        claim = svc.create_claim(
            org_id,
            employee_id=employee_id,
            claim_date=claim_date,
            purpose=purpose.strip(),
            project_id=coerce_uuid(project_id) if project_id else None,
            ticket_id=coerce_uuid(ticket_id) if ticket_id else None,
            task_id=coerce_uuid(task_id) if task_id else None,
            cost_center_id=coerce_uuid(cost_center_id) if cost_center_id else None,
            recipient_bank_code=recipient_bank_code,
            recipient_bank_name=recipient_bank_name,
            recipient_account_number=recipient_account_number,
            recipient_name=recipient_name,
            requested_approver_id=coerce_uuid(requested_approver_id)
            if requested_approver_id
            else None,
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
            svc.submit_claim(org_id, claim.claim_id, skip_receipt_validation=True)
        db.commit()
        return RedirectResponse(url="/people/self/expenses", status_code=303)

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
        can_submit = claim.status == ExpenseClaimStatus.DRAFT
        categories = svc.list_categories(org_id, is_active=True, pagination=None).items

        # Get projects, tickets, tasks for dropdowns (same as create form)
        projects = self._get_projects_for_dropdown(db, org_id)
        tickets = self._get_tickets_for_dropdown(db, org_id)
        tasks = self._get_tasks_for_dropdown(
            db, org_id, str(claim.project_id) if claim.project_id else None
        )

        # Get cost centers for dropdown
        from app.models.finance.core_org.cost_center import CostCenter

        cost_centers_stmt = select(CostCenter).where(
            CostCenter.organization_id == org_id,
            CostCenter.is_active.is_(True),
        ).order_by(CostCenter.cost_center_code)
        cost_centers = list(db.scalars(cost_centers_stmt).all())

        allowed_banks = OrgBankDirectoryService(db).list_active_banks(org_id)
        allowed_bank_names = {bank.bank_name for bank in allowed_banks}

        context = base_context(
            request, auth, "Edit Expense Claim", "self-expenses", db=db
        )
        context.update(
            {
                "claim": claim,
                "categories": categories,
                "can_edit": can_edit,
                "can_submit": can_submit,
                "projects": projects,
                "tickets": tickets,
                "tasks": tasks,
                "cost_centers": cost_centers,
                "allowed_banks": allowed_banks,
                "allowed_bank_names": allowed_bank_names,
                "expense_approver_options": self._get_expense_approver_options(
                    db, org_id
                ),
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        return templates.TemplateResponse(
            request, "people/self/expense_claim_edit.html", context
        )

    def expense_claim_submit_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        claim_id: UUID,
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
                status_code=400, detail="Only draft claims can be submitted"
            )

        svc.submit_claim(org_id, claim_id, skip_receipt_validation=True)
        db.commit()
        return RedirectResponse(url="/people/self/expenses", status_code=302)

    def expense_claim_delete_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        claim_id: UUID,
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
                status_code=400, detail="Only draft claims can be deleted"
            )

        svc.delete_claim(org_id, claim_id)
        db.commit()
        return RedirectResponse(url="/people/self/expenses", status_code=302)

    def expense_claim_update_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        claim_id: UUID,
        items: list[dict],
        recipient_bank_code: str | None = None,
        recipient_bank_name: str | None = None,
        recipient_account_number: str | None = None,
        recipient_name: str | None = None,
        requested_approver_id: UUID | None = None,
        project_id: UUID | None = None,
        ticket_id: UUID | None = None,
        task_id: UUID | None = None,
        cost_center_id: UUID | None = None,
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
            recipient_bank_name=recipient_bank_name,
            recipient_account_number=recipient_account_number,
            recipient_name=recipient_name,
            requested_approver_id=requested_approver_id,
            project_id=project_id,
            ticket_id=ticket_id,
            task_id=task_id,
            cost_center_id=cost_center_id,
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
        status: str | None = None,
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

        scope_filters = [LeaveApplication.leave_approver_id == manager_employee_id]
        if report_ids:
            scope_filters.append(LeaveApplication.employee_id.in_(report_ids))

        query = (
            select(LeaveApplication)
            .options(
                joinedload(LeaveApplication.employee).joinedload(Employee.person),
                joinedload(LeaveApplication.leave_type),
            )
            .where(
                LeaveApplication.organization_id == org_id,
                or_(*scope_filters),
            )
        )
        if status:
            try:
                status_value = LeaveApplicationStatus(status)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid status") from exc
            query = query.where(LeaveApplication.status == status_value)
        query = query.order_by(LeaveApplication.from_date.desc())
        count_query = select(func.count()).select_from(query.subquery())
        total = db.scalar(count_query) or 0
        items = list(
            db.scalars(query.offset(pagination.offset).limit(pagination.limit)).all()
        )

        total_pages = (total + pagination.limit - 1) // pagination.limit if total else 1
        active_filters = build_active_filters(
            params={"status": status},
            labels={"status": "Status"},
        )
        context = base_context(request, auth, "Team Leave", "self-team-leave", db=db)
        context.update(
            {
                "applications": items,
                "status": status,
                "statuses": [s.value for s in LeaveApplicationStatus],
                "active_filters": active_filters,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "total_count": total,
                "limit": pagination.limit,
                "has_prev": page > 1,
                "has_next": pagination.offset + pagination.limit < total,
            }
        )
        context["has_team_approvals"] = True
        context["can_team_leave"] = True
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=manager_employee_id
        )
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
        if (
            application.employee_id not in report_ids
            and application.leave_approver_id != manager_employee_id
        ):
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
        reason: str | None = None,
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
        if (
            application.employee_id not in report_ids
            and application.leave_approver_id != manager_employee_id
        ):
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
        status: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        try:
            approver_employee_id = self._get_employee_id(db, org_id, person_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._employee_required_response(
                    request,
                    auth,
                    db,
                    "My Approvals",
                    "self-my-approvals",
                    detail=exc.detail,
                )
            raise

        report_data = ExpenseService(db, auth).get_my_approvals_report(
            org_id,
            approver_id=approver_employee_id,
        )
        decisions = report_data["decisions"]
        if status:
            status = status.upper()
            if status not in {"APPROVED", "REJECTED"}:
                raise HTTPException(status_code=400, detail="Invalid status")
            action_filter = "APPROVE" if status == "APPROVED" else "REJECT"
            decisions = [
                d
                for d in decisions
                if d.get("action_type", "").upper() == action_filter
            ]

        pagination = PaginationParams.from_page(page, per_page=20)
        total = len(decisions)
        items = decisions[pagination.offset : pagination.offset + pagination.limit]
        total_pages = (total + pagination.limit - 1) // pagination.limit if total else 1

        weekly_balance = None
        approver = db.get(Employee, approver_employee_id)
        if approver is not None:
            limit_svc = ExpenseLimitService(db)
            budget_info = limit_svc._get_approver_weekly_budget(org_id, approver)
            if budget_info is not None:
                budget_amount, limit_id = budget_info
                now = datetime.now(UTC)
                week_start = limit_svc._start_of_week_utc(now)
                week_end = week_start + timedelta(days=6)
                latest_reset = limit_svc.get_latest_weekly_reset(
                    org_id,
                    approver_id=approver_employee_id,
                    approver_limit_id=limit_id,
                    from_datetime=week_start,
                )
                usage_start = latest_reset.reset_at if latest_reset else week_start
                used_amount = db.scalar(
                    select(
                        func.coalesce(
                            func.sum(ExpenseClaim.total_approved_amount), Decimal("0")
                        )
                    )
                    .select_from(ExpenseClaim)
                    .join(
                        ExpenseClaimAction,
                        and_(
                            ExpenseClaimAction.claim_id == ExpenseClaim.claim_id,
                            ExpenseClaimAction.action_type
                            == ExpenseClaimActionType.APPROVE,
                            ExpenseClaimAction.status
                            == ExpenseClaimActionStatus.COMPLETED,
                        ),
                    )
                    .where(
                        ExpenseClaim.organization_id == org_id,
                        ExpenseClaim.status.in_(
                            [ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PAID]
                        ),
                        ExpenseClaimAction.created_at >= usage_start,
                        ExpenseClaimAction.created_at <= now,
                        ExpenseClaim.approver_id == approver_employee_id,
                    )
                ) or Decimal("0")
                weekly_balance = {
                    "week_label": (
                        f"{week_start.date().isoformat()} - "
                        f"{week_end.date().isoformat()}"
                    ),
                    "budget": budget_amount,
                    "used": used_amount,
                    "remaining": budget_amount - used_amount,
                    "last_reset_at": latest_reset.reset_at if latest_reset else None,
                }

        context = base_context(
            request, auth, "My Approvals", "self-my-approvals", db=db
        )
        active_filters = build_active_filters(params={"status": status})
        context.update(
            {
                "approvals": items,
                "status": status,
                "statuses": ["APPROVED", "REJECTED"],
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "has_prev": page > 1,
                "has_next": pagination.offset + pagination.limit < total,
                "weekly_balance": weekly_balance,
                "summary": {
                    "approved_count": report_data["approved_count"],
                    "rejected_count": report_data["rejected_count"],
                    "approved_total": report_data["approved_total"],
                    "rejected_total": report_data["rejected_total"],
                },
                "active_filters": active_filters,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=approver_employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=approver_employee_id
        )
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
            filters=EmployeeFilters(expense_approver_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = {emp.employee_id for emp in reports}
        if claim.employee_id not in report_ids:
            raise HTTPException(status_code=403, detail="Forbidden")

        ExpenseService(db, auth).approve_claim(
            org_id=org_id,
            claim_id=claim_id,
            approver_id=manager_employee_id,
        )
        db.commit()
        return RedirectResponse(url="/people/self/my-approvals", status_code=302)

    def team_expense_reject_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        claim_id: UUID,
        reason: str | None = None,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        manager_employee_id = self._get_employee_id(db, org_id, person_id)

        claim = ExpenseService(db, auth).get_claim(org_id, claim_id)
        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(expense_approver_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = {emp.employee_id for emp in reports}
        if claim.employee_id not in report_ids:
            raise HTTPException(status_code=403, detail="Forbidden")

        ExpenseService(db, auth).reject_claim(
            org_id=org_id,
            claim_id=claim_id,
            approver_id=manager_employee_id,
            reason=reason or "Rejected",
        )
        db.commit()
        return RedirectResponse(url="/people/self/my-approvals", status_code=302)

    # ============ Payslips Self-Service ============

    def payslips_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        year: int | None = None,
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
        query = select(SalarySlip).where(
            SalarySlip.organization_id == org_id,
            SalarySlip.employee_id == employee_id,
            SalarySlip.status.in_(SalarySlipStatus.gl_impacting()),
        )

        if year:
            query = query.where(func.extract("year", SalarySlip.start_date) == year)

        query = query.order_by(SalarySlip.start_date.desc())

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total = db.scalar(count_query) or 0

        # Get paginated items
        slips = db.scalars(
            query.offset(pagination.offset).limit(pagination.limit)
        ).all()

        # Get available years for filtering
        years_query = (
            select(func.distinct(func.extract("year", SalarySlip.start_date)))
            .where(
                SalarySlip.organization_id == org_id,
                SalarySlip.employee_id == employee_id,
                SalarySlip.status.in_(SalarySlipStatus.gl_impacting()),
            )
            .order_by(func.extract("year", SalarySlip.start_date).desc())
        )
        available_years = [int(y[0]) for y in db.execute(years_query).all() if y[0]]

        total_pages = (total + pagination.limit - 1) // pagination.limit if total else 1

        context = base_context(request, auth, "My Payslips", "self-payslips", db=db)
        active_filters = build_active_filters(
            params={"year": str(year) if year else None},
            labels={"year": "Year"},
        )
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
                "active_filters": active_filters,
            }
        )
        context["has_team_approvals"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
        context["can_team_leave"] = context["has_team_approvals"]
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
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
        if slip.status not in SalarySlipStatus.gl_impacting():
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
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
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
            case.has_pending_response = (  # type: ignore[attr-defined]
                case.status.value == "QUERY_ISSUED"
                and case.response_due_date is not None
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
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
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

        from app.models.people.discipline import CaseStatus
        from app.services.people.discipline import DisciplineService

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
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=employee_id
        )
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

        from app.schemas.people.discipline import CaseResponseCreate
        from app.services.people.discipline import DisciplineService

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

        from app.schemas.people.discipline import FileAppealRequest
        from app.services.people.discipline import DisciplineService

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

    def team_discipline_cases_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        include_closed: bool = False,
        page: int = 1,
    ) -> HTMLResponse:
        """List discipline cases for direct reports."""
        from app.models.people.discipline import CaseStatus, DisciplinaryCase

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
                    "Team Discipline",
                    "self-team-discipline",
                    detail=exc.detail,
                )
            raise

        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = [emp.employee_id for emp in reports]
        has_direct_reports = bool(report_ids)

        pagination = PaginationParams.from_page(page, per_page=20)
        total = 0
        cases = []
        if report_ids:
            query = select(DisciplinaryCase).where(
                DisciplinaryCase.organization_id == org_id,
                DisciplinaryCase.employee_id.in_(report_ids),
                DisciplinaryCase.is_deleted == False,  # noqa: E712
            )
            if not include_closed:
                query = query.where(
                    DisciplinaryCase.status.notin_(
                        [CaseStatus.CLOSED, CaseStatus.WITHDRAWN]
                    )
                )
            total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
            cases = list(
                db.scalars(
                    query.order_by(DisciplinaryCase.created_at.desc())
                    .offset(pagination.offset)
                    .limit(pagination.limit)
                ).all()
            )

        total_pages = (total + pagination.limit - 1) // pagination.limit if total else 1
        context = base_context(
            request, auth, "Team Discipline", "self-team-discipline", db=db
        )
        context.update(
            {
                "cases": cases,
                "include_closed": include_closed,
                "has_direct_reports": has_direct_reports,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "has_prev": page > 1,
                "has_next": pagination.offset + pagination.limit < total,
            }
        )
        context["has_team_approvals"] = (
            self._has_team_approvals(
                db, org_id, person_id, employee_id=manager_employee_id
            )
            or self._has_team_expense_approvals(
                db, org_id, person_id, employee_id=manager_employee_id
            )
            or has_direct_reports
        )
        context["can_team_leave"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=manager_employee_id
        )
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=manager_employee_id
        )
        context["can_team_discipline"] = True
        return templates.TemplateResponse(
            request, "people/self/team_discipline.html", context
        )

    def team_discipline_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        error: str | None = None,
        form_data: dict[str, str] | None = None,
    ) -> HTMLResponse:
        """Render form for creating team discipline case."""
        from app.models.people.discipline import SeverityLevel, ViolationType

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
                    "Team Discipline",
                    "self-team-discipline",
                    detail=exc.detail,
                )
            raise

        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items

        context = base_context(
            request, auth, "New Team Discipline Case", "self-team-discipline", db=db
        )
        context.update(
            {
                "error": error,
                "form_data": form_data or {},
                "reports": reports,
                "has_direct_reports": bool(reports),
                "violation_types": [v.value for v in ViolationType],
                "severities": [s.value for s in SeverityLevel],
            }
        )
        context["has_team_approvals"] = (
            self._has_team_approvals(
                db, org_id, person_id, employee_id=manager_employee_id
            )
            or self._has_team_expense_approvals(
                db, org_id, person_id, employee_id=manager_employee_id
            )
            or bool(reports)
        )
        context["can_team_leave"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=manager_employee_id
        )
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=manager_employee_id
        )
        context["can_team_discipline"] = True
        return templates.TemplateResponse(
            request, "people/self/team_discipline_new.html", context
        )

    def team_discipline_create_case_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        employee_id: str,
        violation_type: str,
        severity: str,
        subject: str,
        description: str | None = None,
        incident_date: str | None = None,
        query_text: str,
        response_due_date: str,
    ) -> RedirectResponse | HTMLResponse:
        """Create a team discipline case and immediately issue a query."""
        from app.models.people.discipline import SeverityLevel, ViolationType
        from app.schemas.people.discipline import (
            DisciplinaryCaseCreate,
            IssueQueryRequest,
        )
        from app.services.people.discipline import DisciplineService

        required = [
            employee_id,
            violation_type,
            severity,
            subject,
            query_text,
            response_due_date,
        ]
        form_data = {
            "employee_id": employee_id,
            "violation_type": violation_type,
            "severity": severity,
            "subject": subject,
            "description": description or "",
            "incident_date": incident_date or "",
            "query_text": query_text,
            "response_due_date": response_due_date,
        }
        if any(not str(value or "").strip() for value in required):
            return self.team_discipline_new_form_response(
                request,
                auth,
                db,
                error="Employee, violation type, severity, subject, query text, and response due date are required.",
                form_data=form_data,
            )

        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        manager_employee_id = self._get_employee_id(db, org_id, person_id)

        employee_uuid = coerce_uuid(employee_id)
        if employee_uuid is None:
            return self.team_discipline_new_form_response(
                request,
                auth,
                db,
                error="Invalid employee selected.",
                form_data=form_data,
            )

        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = {emp.employee_id for emp in reports}
        if employee_uuid not in report_ids:
            return self.team_discipline_new_form_response(
                request,
                auth,
                db,
                error="You can only create cases for your direct reports.",
                form_data=form_data,
            )

        try:
            violation = ViolationType(violation_type)
            severity_level = SeverityLevel(severity)
        except ValueError:
            return self.team_discipline_new_form_response(
                request,
                auth,
                db,
                error="Invalid violation type or severity.",
                form_data=form_data,
            )

        try:
            due_date = date.fromisoformat(response_due_date)
            incident = date.fromisoformat(incident_date) if incident_date else None
        except ValueError:
            return self.team_discipline_new_form_response(
                request,
                auth,
                db,
                error="Dates must be in YYYY-MM-DD format.",
                form_data=form_data,
            )

        service = DisciplineService(db)
        try:
            case = service.create_case(
                org_id,
                DisciplinaryCaseCreate(
                    employee_id=employee_uuid,
                    violation_type=violation,
                    severity=severity_level,
                    subject=subject,
                    description=description,
                    incident_date=incident,
                    reported_date=date.today(),
                    reported_by_id=manager_employee_id,
                ),
                created_by_id=person_id,
            )
            service.issue_query(
                case.case_id,
                IssueQueryRequest(query_text=query_text, response_due_date=due_date),
                issued_by_id=person_id,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/self/team/discipline/{case.case_id}?success=case_created",
                status_code=303,
            )
        except (ValidationError, HTTPException) as exc:
            db.rollback()
            message = getattr(exc, "detail", None) or str(exc)
            return self.team_discipline_new_form_response(
                request,
                auth,
                db,
                error=message,
                form_data=form_data,
            )

    def team_discipline_case_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        *,
        case_id: UUID,
    ) -> HTMLResponse:
        """View team discipline case detail."""
        from app.models.people.discipline import CaseStatus
        from app.services.people.discipline import DisciplineService

        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        manager_employee_id = self._get_employee_id(db, org_id, person_id)

        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = {emp.employee_id for emp in reports}

        try:
            case = DisciplineService(db).get_case_detail(case_id)
        except Exception:
            raise HTTPException(status_code=404, detail="Case not found")

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")
        if case.employee_id not in report_ids:
            raise HTTPException(status_code=403, detail="Forbidden")

        context = base_context(
            request,
            auth,
            f"Team Case {case.case_number}",
            "self-team-discipline",
            db=db,
        )
        context.update(
            {
                "case": case,
                "can_issue_query": case.status == CaseStatus.DRAFT,
            }
        )
        context["has_team_approvals"] = (
            self._has_team_approvals(
                db, org_id, person_id, employee_id=manager_employee_id
            )
            or self._has_team_expense_approvals(
                db, org_id, person_id, employee_id=manager_employee_id
            )
            or bool(report_ids)
        )
        context["can_team_leave"] = self._has_team_approvals(
            db, org_id, person_id, employee_id=manager_employee_id
        )
        context["can_team_expenses"] = self._has_team_expense_approvals(
            db, org_id, person_id, employee_id=manager_employee_id
        )
        context["can_team_discipline"] = True
        return templates.TemplateResponse(
            request, "people/self/team_discipline_detail.html", context
        )

    def team_discipline_issue_query_response(
        self,
        auth: WebAuthContext,
        db: Session,
        *,
        case_id: UUID,
        query_text: str,
        response_due_date: str,
    ) -> RedirectResponse:
        """Issue query to employee for team discipline case."""
        from app.schemas.people.discipline import IssueQueryRequest
        from app.services.people.discipline import DisciplineService

        if not query_text or not response_due_date:
            return RedirectResponse(
                url=f"/people/self/team/discipline/{case_id}?error={quote('Query text and response due date are required.')}",
                status_code=303,
            )

        org_id = coerce_uuid(auth.organization_id)
        person_id = coerce_uuid(auth.person_id)
        manager_employee_id = self._get_employee_id(db, org_id, person_id)

        employee_svc = EmployeeService(db, org_id)
        reports = employee_svc.list_employees(
            filters=EmployeeFilters(reports_to_id=manager_employee_id),
            pagination=PaginationParams(offset=0, limit=1000),
        ).items
        report_ids = {emp.employee_id for emp in reports}

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)
        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")
        if case.employee_id not in report_ids:
            raise HTTPException(status_code=403, detail="Forbidden")

        try:
            due_date = date.fromisoformat(response_due_date)
        except ValueError:
            return RedirectResponse(
                url=f"/people/self/team/discipline/{case_id}?error={quote('Response due date must be in YYYY-MM-DD format.')}",
                status_code=303,
            )

        try:
            service.issue_query(
                case_id=case_id,
                data=IssueQueryRequest(
                    query_text=query_text, response_due_date=due_date
                ),
                issued_by_id=person_id,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/self/team/discipline/{case_id}?success=query_issued",
                status_code=303,
            )
        except (ValidationError, HTTPException) as exc:
            db.rollback()
            message = quote(getattr(exc, "detail", None) or str(exc))
            return RedirectResponse(
                url=f"/people/self/team/discipline/{case_id}?error={message}",
                status_code=303,
            )


self_service_web_service = SelfServiceWebService()
