"""
PMS Governance Web Service.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import EmployeeFilters
from app.services.people.hr.employees import EmployeeService
from app.services.people.perf.governance_service import PMSGovernanceService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import parse_date, parse_uuid

logger = logging.getLogger(__name__)


class GovernanceWebService:
    """Web service for governance, grievances, and stakeholder feedback pages."""

    @staticmethod
    def _text(value: object | None) -> str:
        if isinstance(value, str):
            return value.strip()
        return ""

    def governance_dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSGovernanceService(db)
        summary = svc.governance_compliance_summary(org_id)

        context = base_context(request, auth, "PMS Governance", "pms-governance", db=db)
        context.update({"summary": summary})
        return templates.TemplateResponse(
            request,
            "people/perf/pms/governance_dashboard.html",
            context,
        )

    def grievances_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None,
        page: int,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSGovernanceService(db)
        result = svc.list_grievances(
            org_id,
            status=status,
            pagination=PaginationParams.from_page(page, per_page=20),
        )
        context = base_context(request, auth, "PMS Grievances", "pms-grievances", db=db)
        emp_svc = EmployeeService(db, org_id)
        employees = emp_svc.list_employees(
            EmployeeFilters(is_active=True), PaginationParams(limit=500)
        ).items
        context.update(
            {
                "records": result.items,
                "status": status,
                "employees": employees,
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "saved": request.query_params.get("saved"),
                "error": request.query_params.get("error"),
            }
        )
        return templates.TemplateResponse(
            request,
            "people/perf/pms/grievances.html",
            context,
        )

    def grievance_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(
            request, auth, "New PMS Grievance", "pms-grievances", db=db
        )
        context.update({"error": None, "form_data": {}})
        return templates.TemplateResponse(
            request,
            "people/perf/pms/grievance_form.html",
            context,
        )

    async def grievance_create_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSGovernanceService(db)
        try:
            title = self._text(form_data.get("title"))
            description = self._text(form_data.get("description"))
            if not title or not description:
                raise ValueError("Title and description are required")
            if not auth.employee_id:
                raise ValueError("Current user is not linked to an employee profile")

            svc.create_grievance(
                org_id,
                raised_by_employee_id=auth.employee_id,
                title=title,
                description=description,
                channel=self._text(form_data.get("channel")) or "INTERNAL",
                appraisal_id=parse_uuid(self._text(form_data.get("appraisal_id"))),
                inst_perf_id=parse_uuid(self._text(form_data.get("inst_perf_id"))),
            )
            db.commit()
            return RedirectResponse("/people/perf/pms/grievances?saved=1", status_code=303)
        except Exception as exc:
            logger.exception("Failed to create governance grievance")
            db.rollback()
            context = base_context(
                request, auth, "New PMS Grievance", "pms-grievances", db=db
            )
            context.update({"error": str(exc), "form_data": dict(form_data)})
            return templates.TemplateResponse(
                request,
                "people/perf/pms/grievance_form.html",
                context,
            )

    async def assign_grievance_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        grievance_id: str,
    ) -> RedirectResponse:
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSGovernanceService(db)
        try:
            assigned_to_id = parse_uuid(self._text(form_data.get("assigned_to_employee_id")))
            if assigned_to_id is None:
                raise ValueError("Assigned employee is required")
            svc.assign_grievance(
                org_id,
                grievance_id=coerce_uuid(grievance_id),
                assigned_to_employee_id=assigned_to_id,
                due_date=parse_date(self._text(form_data.get("due_date"))),
            )
            db.commit()
            return RedirectResponse("/people/perf/pms/grievances?saved=1", status_code=303)
        except Exception as exc:
            db.rollback()
            logger.exception("Failed to assign grievance %s", grievance_id)
            return RedirectResponse(
                f"/people/perf/pms/grievances?error={str(exc)}",
                status_code=303,
            )

    async def resolve_grievance_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        grievance_id: str,
    ) -> RedirectResponse:
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSGovernanceService(db)
        try:
            resolution_notes = self._text(form_data.get("resolution_notes"))
            svc.resolve_grievance(
                org_id,
                grievance_id=coerce_uuid(grievance_id),
                resolution_notes=resolution_notes,
            )
            db.commit()
            return RedirectResponse("/people/perf/pms/grievances?saved=1", status_code=303)
        except Exception as exc:
            db.rollback()
            logger.exception("Failed to resolve grievance %s", grievance_id)
            return RedirectResponse(
                f"/people/perf/pms/grievances?error={str(exc)}",
                status_code=303,
            )

    async def escalate_grievance_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        grievance_id: str,
    ) -> RedirectResponse:
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSGovernanceService(db)
        try:
            escalation_notes = self._text(form_data.get("escalation_notes"))
            svc.escalate_grievance_to_fcsc(
                org_id,
                grievance_id=coerce_uuid(grievance_id),
                escalation_notes=escalation_notes or None,
            )
            db.commit()
            return RedirectResponse("/people/perf/pms/grievances?saved=1", status_code=303)
        except Exception as exc:
            db.rollback()
            logger.exception("Failed to escalate grievance %s", grievance_id)
            return RedirectResponse(
                f"/people/perf/pms/grievances?error={str(exc)}",
                status_code=303,
            )

    def feedback_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None,
        page: int,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSGovernanceService(db)
        result = svc.list_stakeholder_feedback(
            org_id,
            status=status,
            pagination=PaginationParams.from_page(page, per_page=20),
        )
        context = base_context(
            request, auth, "Stakeholder Feedback", "pms-stakeholder-feedback", db=db
        )
        context.update(
            {
                "records": result.items,
                "status": status,
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
            }
        )
        return templates.TemplateResponse(
            request,
            "people/perf/pms/stakeholder_feedback.html",
            context,
        )

    def feedback_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        context = base_context(
            request,
            auth,
            "New Stakeholder Feedback",
            "pms-stakeholder-feedback",
            db=db,
        )
        context.update({"error": None, "form_data": {}})
        return templates.TemplateResponse(
            request,
            "people/perf/pms/stakeholder_feedback_form.html",
            context,
        )

    async def feedback_create_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PMSGovernanceService(db)
        try:
            title = self._text(form_data.get("title"))
            feedback_text = self._text(form_data.get("feedback_text"))
            if not title or not feedback_text:
                raise ValueError("Title and feedback text are required")

            svc.create_stakeholder_feedback(
                org_id,
                title=title,
                feedback_text=feedback_text,
                source_type=self._text(form_data.get("source_type")) or "SERVICOM",
                channel=self._text(form_data.get("channel")) or "PORTAL",
                submitted_by_name=self._text(form_data.get("submitted_by_name")) or None,
                submitted_by_contact=self._text(form_data.get("submitted_by_contact"))
                or None,
                inst_perf_id=parse_uuid(self._text(form_data.get("inst_perf_id"))),
            )
            db.commit()
            return RedirectResponse(
                "/people/perf/pms/stakeholder-feedback?saved=1",
                status_code=303,
            )
        except Exception as exc:
            logger.exception("Failed to create stakeholder feedback")
            db.rollback()
            context = base_context(
                request,
                auth,
                "New Stakeholder Feedback",
                "pms-stakeholder-feedback",
                db=db,
            )
            context.update({"error": str(exc), "form_data": dict(form_data)})
            return templates.TemplateResponse(
                request,
                "people/perf/pms/stakeholder_feedback_form.html",
                context,
            )


governance_web_service = GovernanceWebService()
