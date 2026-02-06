"""
Recruit Web Service - Interview methods.

Provides view-focused data and operations for interview web routes.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.hr.employee import EmployeeStatus
from app.models.people.recruit import InterviewStatus
from app.models.people.recruit.interview import InterviewRound
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import EmployeeFilters, EmployeeService
from app.services.people.recruit import RecruitmentService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import (
    INTERVIEW_TYPES,
    logger,
    parse_date,
    parse_int,
    parse_status,
    parse_uuid,
)

logger = logging.getLogger(__name__)


def _get_form_str(form: Any, key: str, default: str = "") -> str:
    value = form.get(key, default) if form is not None else default
    if value is None or isinstance(value, UploadFile):
        return default
    return str(value).strip()


class InterviewWebService:
    """Web service methods for interviews."""

    # ─────────────────────────────────────────────────────────────────────────
    # Context Builders
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def list_interviews_context(
        db: Session,
        organization_id: UUID,
        status: Optional[str] = None,
        job_opening_id: Optional[str] = None,
        applicant_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        page: int = 1,
    ) -> dict:
        """Build context for interviews list page."""
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = RecruitmentService(db)

        status_enum = parse_status(status, InterviewStatus)
        result = svc.list_interviews(
            organization_id,
            status=status_enum,
            applicant_id=parse_uuid(applicant_id),
            job_opening_id=parse_uuid(job_opening_id),
            from_date=parse_date(start_date),
            to_date=parse_date(end_date, end_of_day=True),
            pagination=pagination,
        )

        job_openings = svc.list_job_openings(
            organization_id,
            pagination=PaginationParams(limit=200),
        ).items

        applicants = svc.list_applicants(
            organization_id,
            pagination=PaginationParams(limit=200),
        ).items

        return {
            "interviews": result.items,
            "job_openings": job_openings,
            "applicants": applicants,
            "status": status,
            "job_opening_id": job_opening_id,
            "applicant_id": applicant_id,
            "start_date": start_date,
            "end_date": end_date,
            "statuses": [s.value for s in InterviewStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }

    @staticmethod
    def interview_form_context(
        db: Session,
        organization_id: UUID,
        interview_id: Optional[str] = None,
        applicant_id: Optional[str] = None,
    ) -> dict:
        """Build context for interview create/edit form."""
        svc = RecruitmentService(db)
        emp_svc = EmployeeService(db, organization_id)

        applicants = svc.list_applicants(
            organization_id,
            pagination=PaginationParams(limit=200),
        ).items

        employees = emp_svc.list_employees(
            EmployeeFilters(status=EmployeeStatus.ACTIVE),
            PaginationParams(limit=500),
        ).items

        interview = None
        if interview_id:
            try:
                interview = svc.get_interview(
                    organization_id, coerce_uuid(interview_id)
                )
            except Exception:
                interview = None

        form_data = {}
        if applicant_id and not interview:
            form_data["applicant_id"] = applicant_id

        return {
            "interview": interview,
            "applicants": applicants,
            "employees": employees,
            "rounds": [r.value for r in InterviewRound],
            "interview_types": INTERVIEW_TYPES,
            "form_data": form_data,
        }

    @staticmethod
    def interview_detail_context(
        db: Session,
        organization_id: UUID,
        interview_id: str,
    ) -> dict:
        """Build context for interview detail page."""
        svc = RecruitmentService(db)

        try:
            interview = svc.get_interview(organization_id, coerce_uuid(interview_id))
        except Exception:
            return {"interview": None}

        return {
            "interview": interview,
            "statuses": [s.value for s in InterviewStatus],
        }

    @staticmethod
    def build_interview_input(form_data: dict) -> dict:
        """Build input kwargs for interview from form data."""
        scheduled_date = form_data.get("scheduled_date")
        scheduled_time_from = form_data.get("scheduled_time_from")
        scheduled_time_to = form_data.get("scheduled_time_to")

        scheduled_from = None
        scheduled_to = None
        if scheduled_date and scheduled_time_from and scheduled_time_to:
            scheduled_from = datetime.fromisoformat(
                f"{scheduled_date}T{scheduled_time_from}"
            )
            scheduled_to = datetime.fromisoformat(
                f"{scheduled_date}T{scheduled_time_to}"
            )

        return {
            "round": InterviewRound(form_data.get("round")),
            "interview_type": form_data.get("interview_type", "IN_PERSON"),
            "scheduled_from": scheduled_from,
            "scheduled_to": scheduled_to,
            "interviewer_id": coerce_uuid(form_data["interviewer_id"])
            if form_data.get("interviewer_id")
            else None,
            "location": form_data.get("location") or None,
            "meeting_link": form_data.get("meeting_link") or None,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Response Methods
    # ─────────────────────────────────────────────────────────────────────────

    def list_interviews_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str] = None,
        job_opening_id: Optional[str] = None,
        applicant_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render interviews list page."""
        context = base_context(request, auth, "Interviews", "recruit", db=db)
        context["request"] = request
        context.update(
            self.list_interviews_context(
                db,
                coerce_uuid(auth.organization_id),
                status=status,
                job_opening_id=job_opening_id,
                applicant_id=applicant_id,
                start_date=start_date,
                end_date=end_date,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/interviews.html", context
        )

    def interview_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        applicant_id: Optional[str] = None,
    ) -> HTMLResponse:
        """Render new interview form."""
        context = base_context(request, auth, "Schedule Interview", "recruit", db=db)
        context["request"] = request
        context.update(
            self.interview_form_context(
                db,
                coerce_uuid(auth.organization_id),
                applicant_id=applicant_id,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/interview_form.html", context
        )

    def interview_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        interview_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render interview detail page."""
        ctx = self.interview_detail_context(
            db, coerce_uuid(auth.organization_id), interview_id
        )

        if not ctx.get("interview"):
            return RedirectResponse(url="/people/recruit/interviews", status_code=303)

        context = base_context(request, auth, "Interview Details", "recruit", db=db)
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(
            request, "people/recruit/interview_detail.html", context
        )

    def interview_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        interview_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render interview edit form."""
        ctx = self.interview_form_context(
            db, coerce_uuid(auth.organization_id), interview_id
        )

        if not ctx.get("interview"):
            return RedirectResponse(url="/people/recruit/interviews", status_code=303)

        context = base_context(request, auth, "Edit Interview", "recruit", db=db)
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(
            request, "people/recruit/interview_form.html", context
        )

    async def create_interview_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle interview creation form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            applicant_id = form_data.get("applicant_id")
            input_kwargs = self.build_interview_input(dict(form_data))
            interview = svc.schedule_interview(
                org_id,
                applicant_id=coerce_uuid(applicant_id),
                **input_kwargs,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/recruit/interviews/{interview.interview_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("create_interview_response: failed")
            context = base_context(
                request, auth, "Schedule Interview", "recruit", db=db
            )
            context["request"] = request
            context.update(self.interview_form_context(db, org_id))
            context["form_data"] = dict(form_data)
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "people/recruit/interview_form.html", context
            )

    async def update_interview_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        interview_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle interview update form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            input_kwargs = self.build_interview_input(dict(form_data))
            svc.update_interview(org_id, coerce_uuid(interview_id), **input_kwargs)
            db.commit()
            return RedirectResponse(
                url=f"/people/recruit/interviews/{interview_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("update_interview_response: failed")
            context = base_context(request, auth, "Edit Interview", "recruit", db=db)
            context["request"] = request
            context.update(self.interview_form_context(db, org_id, interview_id))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "people/recruit/interview_form.html", context
            )

    async def cancel_interview_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        interview_id: str,
    ) -> RedirectResponse:
        """Handle interview cancellation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            reason = _get_form_str(form_data, "reason") or None
            svc.cancel_interview(org_id, coerce_uuid(interview_id), reason=reason)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/interviews/{interview_id}", status_code=303
        )

    async def record_interview_feedback_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        interview_id: str,
    ) -> RedirectResponse:
        """Handle recording interview feedback."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            svc.update_interview(
                org_id,
                coerce_uuid(interview_id),
                rating=parse_int(_get_form_str(form_data, "rating") or None),
                recommendation=form_data.get("recommendation") or None,
                feedback=form_data.get("feedback") or None,
                strengths=form_data.get("strengths") or None,
                weaknesses=form_data.get("weaknesses") or None,
                status=InterviewStatus.COMPLETED,
            )
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/interviews/{interview_id}", status_code=303
        )
