"""
Training Web Service - Report methods.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.common import coerce_uuid
from app.services.people.training import TrainingService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import parse_date

logger = logging.getLogger(__name__)


class ReportWebService:
    """Web service methods for training reports."""

    @staticmethod
    def completion_report_context(
        db: Session,
        organization_id: UUID,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Build context for completion report."""
        svc = TrainingService(db)
        report = svc.get_training_completion_report(
            organization_id,
            start_date=parse_date(start_date),
            end_date=parse_date(end_date),
        )
        return {
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }

    @staticmethod
    def by_department_report_context(
        db: Session,
        organization_id: UUID,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Build context for by-department report."""
        svc = TrainingService(db)
        report = svc.get_training_by_department_report(
            organization_id,
            start_date=parse_date(start_date),
            end_date=parse_date(end_date),
        )
        return {
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }

    @staticmethod
    def cost_analysis_report_context(
        db: Session,
        organization_id: UUID,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Build context for cost analysis report."""
        svc = TrainingService(db)
        report = svc.get_training_cost_report(
            organization_id,
            start_date=parse_date(start_date),
            end_date=parse_date(end_date),
        )
        return {
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }

    @staticmethod
    def effectiveness_report_context(
        db: Session,
        organization_id: UUID,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """Build context for effectiveness report."""
        svc = TrainingService(db)
        report = svc.get_training_effectiveness_report(
            organization_id,
            start_date=parse_date(start_date),
            end_date=parse_date(end_date),
        )
        return {
            "report": report,
            "start_date": start_date or "",
            "end_date": end_date or "",
        }

    # Response methods

    def completion_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> HTMLResponse:
        """Render completion report page."""
        context = base_context(request, auth, "Training Completion Report", "training", db=db)
        context.update(
            self.completion_report_context(
                db, coerce_uuid(auth.organization_id), start_date, end_date
            )
        )
        return templates.TemplateResponse(request, "people/training/reports/completion.html", context)

    def by_department_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> HTMLResponse:
        """Render by-department report page."""
        context = base_context(request, auth, "Training by Department", "training", db=db)
        context.update(
            self.by_department_report_context(
                db, coerce_uuid(auth.organization_id), start_date, end_date
            )
        )
        return templates.TemplateResponse(request, "people/training/reports/by_department.html", context)

    def cost_analysis_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> HTMLResponse:
        """Render cost analysis report page."""
        context = base_context(request, auth, "Training Cost Analysis", "training", db=db)
        context.update(
            self.cost_analysis_report_context(
                db, coerce_uuid(auth.organization_id), start_date, end_date
            )
        )
        return templates.TemplateResponse(request, "people/training/reports/cost_analysis.html", context)

    def effectiveness_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> HTMLResponse:
        """Render effectiveness report page."""
        context = base_context(request, auth, "Training Effectiveness", "training", db=db)
        context.update(
            self.effectiveness_report_context(
                db, coerce_uuid(auth.organization_id), start_date, end_date
            )
        )
        return templates.TemplateResponse(request, "people/training/reports/effectiveness.html", context)
