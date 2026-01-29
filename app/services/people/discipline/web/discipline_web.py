"""
Discipline Web Service - HR admin view service for discipline management.

Provides view-focused data and operations for discipline web routes including:
case management, workflow operations, and reporting.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.discipline import (
    CaseStatus,
    ViolationType,
    SeverityLevel,
    ActionType,
    DocumentType,
)
from app.services.common import coerce_uuid
from app.services.people.discipline import DisciplineService
from app.services.common import PaginationParams
from app.services.people.hr import EmployeeService
from app.services.people.hr.employee_types import EmployeeFilters
from app.models.people.hr.employee import EmployeeStatus
from app.schemas.people.discipline import (
    DisciplinaryCaseCreate,
    DisciplinaryCaseUpdate,
    IssueQueryRequest,
    ScheduleHearingRequest,
    RecordDecisionRequest,
    CaseActionCreate,
    CaseWitnessCreate,
    CaseListFilter,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def parse_uuid(value: Optional[str]) -> Optional[UUID]:
    """Parse string to UUID, returning None if invalid."""
    if not value or value.strip() == "":
        return None
    try:
        return UUID(value.strip())
    except (ValueError, TypeError):
        return None


def parse_date(value: Optional[str]) -> Optional[date]:
    """Parse string to date, returning None if invalid."""
    if not value or value.strip() == "":
        return None
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, TypeError):
        return None


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse string to datetime, returning None if invalid."""
    if not value or value.strip() == "":
        return None
    try:
        return datetime.fromisoformat(value.strip())
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
        status: Optional[str] = None,
        violation_type: Optional[str] = None,
        severity: Optional[str] = None,
        employee_id: Optional[str] = None,
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

        cases, total = service.list_cases(org_id, filters=filters, offset=offset, limit=limit)

        total_pages = (total + limit - 1) // limit if total > 0 else 1

        context = base_context(request, auth, "Disciplinary Cases", "hr-discipline", db=db)
        context.update({
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
        })
        return templates.TemplateResponse(request, "people/hr/discipline/cases.html", context)

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
        context.update({
            "case": case,
            "employees": employees,
            "statuses": [s.value for s in CaseStatus],
            "action_types": [a.value for a in ActionType],
            "document_types": [d.value for d in DocumentType],
        })
        return templates.TemplateResponse(
            request, "people/hr/discipline/case_detail.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Case Creation
    # ─────────────────────────────────────────────────────────────────────────

    def case_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new case form."""
        org_id = coerce_uuid(auth.organization_id)
        employee_service = EmployeeService(db, org_id)
        employees = employee_service.list_employees(
            filters=EmployeeFilters(status=EmployeeStatus.ACTIVE),
            pagination=PaginationParams(limit=500),
        ).items

        context = base_context(request, auth, "New Disciplinary Case", "hr-discipline", db=db)
        context.update({
            "employees": employees,
            "violation_types": [v.value for v in ViolationType],
            "severities": [s.value for s in SeverityLevel],
        })
        return templates.TemplateResponse(
            request, "people/hr/discipline/case_form.html", context
        )

    def case_create_response(
        self,
        auth: WebAuthContext,
        db: Session,
        employee_id: str,
        violation_type: str,
        severity: str,
        subject: str,
        description: Optional[str] = None,
        incident_date: Optional[str] = None,
        reported_by_id: Optional[str] = None,
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

        data = IssueQueryRequest(
            query_text=query_text,
            response_due_date=date.fromisoformat(response_due_date),
        )

        service.issue_query(case_id, data, issued_by_id=person_id)
        db.commit()

        return RedirectResponse(
            url=f"/people/hr/discipline/{case_id}?success=query_issued",
            status_code=303,
        )

    def schedule_hearing_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
        hearing_date: str,
        hearing_location: Optional[str] = None,
        panel_chair_id: Optional[str] = None,
    ) -> RedirectResponse:
        """Schedule a hearing."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

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
        action_type: Optional[str] = None,
        action_description: Optional[str] = None,
        effective_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> RedirectResponse:
        """Record decision and actions."""
        org_id = coerce_uuid(auth.organization_id)
        person_id = auth.person_id

        service = DisciplineService(db)
        case = service.get_case_or_404(case_id)

        if case.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Case not found")

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

    # ─────────────────────────────────────────────────────────────────────────
    # Witness Management
    # ─────────────────────────────────────────────────────────────────────────

    def add_witness_response(
        self,
        auth: WebAuthContext,
        db: Session,
        case_id: UUID,
        employee_id: Optional[str] = None,
        external_name: Optional[str] = None,
        external_contact: Optional[str] = None,
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
