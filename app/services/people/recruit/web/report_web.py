"""
Recruit Web Service - Report methods.

Provides view-focused data and operations for recruitment report web routes.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid
from app.services.people.recruit import RecruitmentService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import logger, parse_date_only, parse_uuid

logger = logging.getLogger(__name__)


class ReportWebService:
    """Web service methods for recruitment reports."""

    # ─────────────────────────────────────────────────────────────────────────
    # Context Builders
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def pipeline_report_context(
        db: Session,
        organization_id: UUID,
        job_opening_id: Optional[str] = None,
    ) -> dict:
        """Build context for pipeline report page."""
        svc = RecruitmentService(db)

        report = svc.get_recruitment_pipeline_report(
            organization_id,
            job_opening_id=parse_uuid(job_opening_id),
        )

        return {
            "report": report,
            "job_opening_id": job_opening_id,
        }

    @staticmethod
    def time_to_hire_report_context(
        db: Session,
        organization_id: UUID,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Build context for time to hire report page."""
        svc = RecruitmentService(db)

        report = svc.get_time_to_hire_report(
            organization_id,
            start_date=parse_date_only(start_date),
            end_date=parse_date_only(end_date),
        )

        return {
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }

    @staticmethod
    def source_analysis_report_context(
        db: Session,
        organization_id: UUID,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Build context for source analysis report page."""
        svc = RecruitmentService(db)

        report = svc.get_source_analysis_report(
            organization_id,
            start_date=parse_date_only(start_date),
            end_date=parse_date_only(end_date),
        )

        return {
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }

    @staticmethod
    def overview_report_context(
        db: Session,
        organization_id: UUID,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Build context for recruitment overview report page."""
        svc = RecruitmentService(db)

        report = svc.get_recruitment_overview_report(
            organization_id,
            start_date=parse_date_only(start_date),
            end_date=parse_date_only(end_date),
        )

        return {
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Response Methods
    # ─────────────────────────────────────────────────────────────────────────

    def pipeline_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        job_opening_id: Optional[str] = None,
    ) -> HTMLResponse:
        """Render pipeline report page."""
        context = base_context(request, auth, "Pipeline Report", "recruit", db=db)
        context["request"] = request
        context.update(
            self.pipeline_report_context(
                db,
                coerce_uuid(auth.organization_id),
                job_opening_id=job_opening_id,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/reports/pipeline.html", context
        )

    def time_to_hire_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> HTMLResponse:
        """Render time to hire report page."""
        context = base_context(request, auth, "Time to Hire", "recruit", db=db)
        context["request"] = request
        context.update(
            self.time_to_hire_report_context(
                db,
                coerce_uuid(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/reports/time_to_hire.html", context
        )

    def source_analysis_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> HTMLResponse:
        """Render source analysis report page."""
        context = base_context(request, auth, "Source Analysis", "recruit", db=db)
        context["request"] = request
        context.update(
            self.source_analysis_report_context(
                db,
                coerce_uuid(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/reports/sources.html", context
        )

    def overview_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> HTMLResponse:
        """Render recruitment overview report page."""
        context = base_context(request, auth, "Recruitment Overview", "recruit", db=db)
        context["request"] = request
        context.update(
            self.overview_report_context(
                db,
                coerce_uuid(auth.organization_id),
                start_date=start_date,
                end_date=end_date,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/reports/overview.html", context
        )
