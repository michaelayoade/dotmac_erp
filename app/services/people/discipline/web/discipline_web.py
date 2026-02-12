"""
Discipline Web Service - HR admin view service for discipline management.

Provides view-focused data and operations for discipline web routes including:
case management, workflow operations, and reporting.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.models.people.discipline import (
    ActionType,
    CaseStatus,
    DocumentType,
    SeverityLevel,
    ViolationType,
)
from app.models.people.hr import Employee, EmployeeStatus
from app.models.person import Person
from app.schemas.people.discipline import (
    CaseActionCreate,
    CaseListFilter,
    CaseWitnessCreate,
    DisciplinaryCaseCreate,
    IssueQueryRequest,
    RecordDecisionRequest,
    ScheduleHearingRequest,
)
from app.services.common import PaginationParams, ValidationError, coerce_uuid
from app.services.formatters import parse_date
from app.services.people.discipline import DisciplineService
from app.services.people.hr import EmployeeService
from app.services.people.hr.employee_types import EmployeeFilters
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def parse_uuid(value: str | None) -> UUID | None:
    """Parse string to UUID, returning None if invalid."""
    if not value or value.strip() == "":
        return None
    try:
        return UUID(value.strip())
    except (ValueError, TypeError):
        return None


class DisciplineWebService:
    """HR Admin Discipline Web Service."""

    # ─────────────────────────────────────────────────────────────────────────
    # Case List and Detail
    # ─────────────────────────────────────────────────────────────────────────

    def list_cases_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None = None,
        violation_type: str | None = None,
        severity: str | None = None,
        employee_id: str | None = None,
        include_closed: bool = False,
        page: int = 1,
    ) -> HTMLResponse:
        """Render cases list page for HR admin."""
        org_id = coerce_uuid(auth.organization_id)
        offset = (page - 1) * 20
        limit = 20

        service = DisciplineService(db)

        # Parse filters
        filters = CaseListFilter(
            status=CaseStatus(status) if status else None,
            violation_type=ViolationType(violation_type) if violation_type else None,
            severity=SeverityLevel(severity) if severity else None,
            employee_id=parse_uuid(employee_id),
            include_closed=include_closed,
        )

        cases, total = service.list_cases(
            org_id, filters=filters, offset=offset, limit=limit
        )

        total_pages = (total + limit - 1) // limit if total > 0 else 1

        context = base_context(
            request, auth, "Disciplinary Cases", "hr-discipline", db=db
        )
        context.update(
            {
                "cases": cases,
                "status": status,
                "violation_type": violation_type,
                "severity": severity,
                "employee_id": employee_id,
                "include_closed": include_closed,
                "statuses": [s.value for s in CaseStatus],
                "violation_types": [v.value for v in ViolationType],
                "severities": [s.value for s in SeverityLevel],
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            }
        )
        return templates.TemplateResponse(
            request, "people/hr/discipline/cases.html", context
        )

    def case_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
    ) -> HTMLResponse:
        """Render case detail page for HR admin."""
        org_id = coerce_uuid(auth.organization_id)
        service = DisciplineService(db)

        try:
            case = service.get_case_detail(case_id)
        except Exception:
            raise HTTPException(status_code=404, detail="Case not found")

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

        # Get employees for dropdowns
        employee_service = EmployeeService(db, org_id)
        employees = employee_service.list_employees(
            filters=EmployeeFilters(status=EmployeeStatus.ACTIVE),
            pagination=PaginationParams(limit=500),
        ).items

        context = base_context(
            request, auth, f"Case {case.case_number}", "hr-discipline", db=db
        )
        context.update(
            {
                "case": case,
                "employees": employees,
                "statuses": [s.value for s in CaseStatus],
                "action_types": [a.value for a in ActionType],
                "document_types": [d.value for d in DocumentType],
            }
        )
        response = templates.TemplateResponse(
            request, "people/hr/discipline/case_detail.html", context
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    # ─────────────────────────────────────────────────────────────────────────
    # Case Creation
    # ─────────────────────────────────────────────────────────────────────────

    def case_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error: str | None = None,
        form_data: dict | None = None,
    ) -> HTMLResponse:
        """Render new case form."""
        org_id = coerce_uuid(auth.organization_id)
        employee_service = EmployeeService(db, org_id)
        working_form_data = dict(form_data or {})
        employee_name = working_form_data.get("employee_name", "")
        reported_by_name = working_form_data.get("reported_by_name", "")

        def _employee_label(emp: Employee) -> str:
            name = emp.person.name if emp.person else ""
            if emp.employee_code:
                return f"{name} ({emp.employee_code})" if name else emp.employee_code
            return name

        if not employee_name and working_form_data.get("employee_id"):
            try:
                employee = employee_service.get_employee(
                    UUID(working_form_data["employee_id"])
                )
                employee_name = _employee_label(employee)
            except Exception:
                employee_name = ""

        if not reported_by_name and working_form_data.get("reported_by_id"):
            try:
                reporter = employee_service.get_employee(
                    UUID(working_form_data["reported_by_id"])
                )
                reported_by_name = _employee_label(reporter)
            except Exception:
                reported_by_name = ""

        working_form_data["employee_name"] = employee_name
        working_form_data["reported_by_name"] = reported_by_name

        context = base_context(
            request, auth, "New Disciplinary Case", "hr-discipline", db=db
        )
        context.update(
            {
                "violation_types": [v.value for v in ViolationType],
                "severities": [s.value for s in SeverityLevel],
                "error": error,
                "form_data": working_form_data,
            }
        )
        response = templates.TemplateResponse(
            request, "people/hr/discipline/case_form.html", context
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @staticmethod
    def employee_typeahead(
        db: Session,
        organization_id: str,
        query: str,
        limit: int = 8,
    ) -> dict:
        """Search active employees for discipline typeahead fields."""
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import joinedload as jl

        org_id = coerce_uuid(organization_id)
        search_term = f"%{query.strip()}%"
        stmt = (
            sa_select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .options(jl(Employee.person))
            .where(
                Employee.organization_id == org_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .where(
                (Person.first_name.ilike(search_term))
                | (Person.last_name.ilike(search_term))
                | (Person.email.ilike(search_term))
                | (Employee.employee_code.ilike(search_term))
            )
            .order_by(Person.first_name.asc(), Person.last_name.asc())
            .limit(limit)
        )
        employees = list(db.scalars(stmt).unique().all())
        items = []
        for employee in employees:
            name = employee.person.name if employee.person else ""
            label = name
            if employee.employee_code:
                label = (
                    f"{name} ({employee.employee_code})"
                    if name
                    else employee.employee_code
                )
            items.append(
                {
                    "ref": str(employee.employee_id),
                    "label": label,
                    "name": name,
                    "employee_code": employee.employee_code or "",
                }
            )
        return {"items": items}

    def case_create_response(
        self,
        auth: WebAuthContext,
        db: Session,
        employee_id: str,
        violation_type: str,
        severity: str,
        subject: str,
        description: str | None = None,
        incident_date: str | None = None,
        reported_by_id: str | None = None,
    ) -> RedirectResponse:
        """Create a new disciplinary case."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)

        data = DisciplinaryCaseCreate(
            employee_id=UUID(employee_id),
            violation_type=ViolationType(violation_type),
            severity=SeverityLevel(severity),
            subject=subject,
            description=description,
            incident_date=parse_date(incident_date),
            reported_date=date.today(),
            reported_by_id=parse_uuid(reported_by_id),
        )

        case = service.create_case(org_id, data, created_by_id=person_id)
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/discipline/{case.case_id}?success=created",
            status_code=303,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Workflow Operations
    # ─────────────────────────────────────────────────────────────────────────

    def issue_query_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
        query_text: str,
        response_due_date: str,
    ) -> RedirectResponse:
        """Issue a query to the employee."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

        try:
            due_date = parse_date(response_due_date)
            if not due_date:
                raise ValueError("invalid response due date")
            try:
                data = IssueQueryRequest(
                    query_text=query_text,
                    response_due_date=due_date,
                )
            except PydanticValidationError as exc:
                db.rollback()
                message = "Please check the form and try again."
                for err in exc.errors():
                    if err.get("loc", [None])[-1] == "query_text":
                        message = "Query text must be at least 10 characters."
                        break
                    if err.get("loc", [None])[-1] == "response_due_date":
                        message = "Response due date is invalid."
                        break
                return RedirectResponse(
                    url=f"/people/hr/discipline/{case_id}?error={quote(message)}",
                    status_code=303,
                )
            service.issue_query(case_id, data, issued_by_id=person_id)
            db.commit()
            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?success=query_issued",
                status_code=303,
            )
        except ValueError:
            db.rollback()
            message = quote("Response due date is invalid.")
            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?error={message}",
                status_code=303,
            )
        except ValidationError as exc:
            db.rollback()
            message = quote(exc.message or "Unable to issue query.")
            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?error={message}",
                status_code=303,
            )
        except Exception:
            db.rollback()
            error_id = uuid4()
            logger.exception(
                "Issue query failed. error_id=%s case_id=%s org_id=%s person_id=%s",
                error_id,
                case_id,
                org_id,
                person_id,
            )
            message = quote(
                f"Unable to issue query. Please try again. Error ID: {error_id}"
            )
            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?error={message}",
                status_code=303,
            )

    def schedule_hearing_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
        hearing_date: str,
        hearing_location: str | None = None,
        panel_chair_id: str | None = None,
    ) -> RedirectResponse:
        """Schedule a hearing."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

        try:
            data = ScheduleHearingRequest(
                hearing_date=datetime.fromisoformat(hearing_date),
                hearing_location=hearing_location,
                panel_chair_id=parse_uuid(panel_chair_id),
            )
            service.schedule_hearing(case_id, data, scheduled_by_id=person_id)
            db.commit()
            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?success=hearing_scheduled",
                status_code=303,
            )
        except ValueError:
            db.rollback()
            message = quote("Hearing date is invalid.")
            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?error={message}",
                status_code=303,
            )
        except ValidationError as exc:
            db.rollback()
            message = quote(exc.message or "Unable to schedule hearing.")
            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?error={message}",
                status_code=303,
            )

    def record_hearing_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
        hearing_notes: str,
    ) -> RedirectResponse:
        """Record hearing notes."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

        service.record_hearing_notes(case_id, hearing_notes, recorded_by_id=person_id)
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?success=hearing_recorded",
            status_code=303,
        )

    def record_decision_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
        decision_summary: str,
        action_type: str | None = None,
        action_description: str | None = None,
        effective_date: str | None = None,
        end_date: str | None = None,
    ) -> RedirectResponse:
        """Record decision and actions."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

        try:
            actions = []
            if action_type:
                actions.append(
                    CaseActionCreate(
                        action_type=ActionType(action_type),
                        description=action_description,
                        effective_date=parse_date(effective_date) or date.today(),
                        end_date=parse_date(end_date),
                    )
                )

            data = RecordDecisionRequest(
                decision_summary=decision_summary,
                actions=actions,
            )

            service.record_decision(case_id, data, decided_by_id=person_id)
            db.commit()

            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?success=decision_recorded",
                status_code=303,
            )
        except ValueError:
            db.rollback()
            message = quote("Invalid data provided for decision.")
            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?error={message}",
                status_code=303,
            )
        except ValidationError as exc:
            db.rollback()
            message = quote(exc.message or "Unable to record decision.")
            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?error={message}",
                status_code=303,
            )

    def close_case_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
    ) -> RedirectResponse:
        """Close a case."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

        service.close_case(case_id, closed_by_id=person_id)
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?success=closed",
            status_code=303,
        )

    def withdraw_case_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
    ) -> RedirectResponse:
        """Withdraw a case."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

        service.withdraw_case(case_id, withdrawn_by_id=person_id)
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?success=withdrawn",
            status_code=303,
        )

    def delete_case_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
    ) -> RedirectResponse:
        """Delete a draft case."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

        try:
            service.delete_case(case_id, deleted_by_id=person_id)
            db.commit()
        except ValidationError as exc:
            db.rollback()
            message = quote(exc.message or "Unable to delete case.")
            return RedirectResponse(
                url=f"/people/hr/discipline/{case_id}?error={message}",
                status_code=303,
            )

        return RedirectResponse(
            url="/people/hr/discipline?success=deleted",
            status_code=303,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Witness Management
    # ─────────────────────────────────────────────────────────────────────────

    def add_witness_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
        employee_id: str | None = None,
        external_name: str | None = None,
        external_contact: str | None = None,
    ) -> RedirectResponse:
        """Add a witness to a case."""
        org_id = coerce_uuid(auth.organization_id)

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

        data = CaseWitnessCreate(
            employee_id=parse_uuid(employee_id),
            external_name=external_name,
            external_contact=external_contact,
        )

        service.add_witness(case_id, data)
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?success=witness_added",
            status_code=303,
        )

    def acknowledge_response_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
        response_id: UUID,
    ) -> RedirectResponse:
        """Acknowledge an employee response."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

        service.acknowledge_response(response_id, acknowledged_by_id=person_id)
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?success=response_acknowledged",
            status_code=303,
        )
