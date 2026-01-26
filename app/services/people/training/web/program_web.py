"""
Training Web Service - Program methods.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.training import TrainingProgramStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import OrganizationService
from app.services.people.training import TrainingService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import logger, parse_uuid, parse_decimal, parse_int, parse_program_status

logger = logging.getLogger(__name__)


class ProgramWebService:
    """Web service methods for training programs."""

    @staticmethod
    def list_programs_context(
        db: Session,
        organization_id: UUID,
        search: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        page: int = 1,
    ) -> dict:
        """Build context for programs list page."""
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = TrainingService(db)

        result = svc.list_programs(
            organization_id,
            search=search,
            status=parse_program_status(status),
            category=category,
            pagination=pagination,
        )

        return {
            "programs": result.items,
            "search": search,
            "status": status,
            "category": category,
            "statuses": [s.value for s in TrainingProgramStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }

    @staticmethod
    def program_form_context(
        db: Session,
        organization_id: UUID,
        program_id: Optional[str] = None,
    ) -> dict:
        """Build context for program create/edit form."""
        org_svc = OrganizationService(db, organization_id)
        departments = org_svc.list_departments(pagination=PaginationParams(limit=200)).items

        program = None
        if program_id:
            svc = TrainingService(db)
            try:
                program = svc.get_program(organization_id, coerce_uuid(program_id))
            except Exception:
                program = None

        return {
            "program": program,
            "departments": departments,
            "form_data": {},
            "error": None,
        }

    @staticmethod
    def program_detail_context(
        db: Session,
        organization_id: UUID,
        program_id: str,
    ) -> dict:
        """Build context for program detail page."""
        svc = TrainingService(db)

        try:
            program = svc.get_program(organization_id, coerce_uuid(program_id))
        except Exception:
            return {"program": None, "events": []}

        events = svc.list_events(
            organization_id,
            program_id=coerce_uuid(program_id),
            pagination=PaginationParams(limit=10),
        ).items

        return {
            "program": program,
            "events": events,
            "error": None,
        }

    @staticmethod
    def build_program_input(form_data: dict) -> dict:
        """Build input kwargs for program from form data."""
        return {
            "program_code": form_data.get("program_code", ""),
            "program_name": form_data.get("program_name", ""),
            "training_type": form_data.get("training_type", "INTERNAL"),
            "category": form_data.get("category") or None,
            "department_id": coerce_uuid(form_data["department_id"]) if form_data.get("department_id") else None,
            "description": form_data.get("description") or None,
            "duration_hours": parse_int(form_data.get("duration_hours")),
            "duration_days": parse_int(form_data.get("duration_days")),
            "cost_per_attendee": parse_decimal(form_data.get("cost_per_attendee")),
            "currency_code": form_data.get("currency_code", "NGN"),
            "provider_name": form_data.get("provider_name") or None,
            "provider_contact": form_data.get("provider_contact") or None,
            "objectives": form_data.get("objectives") or None,
            "prerequisites": form_data.get("prerequisites") or None,
            "syllabus": form_data.get("syllabus") or None,
        }

    # Response methods

    def list_programs_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render programs list page."""
        context = base_context(request, auth, "Training Programs", "training", db=db)
        context["request"] = request
        context.update(
            self.list_programs_context(
                db, coerce_uuid(auth.organization_id), search, status, category, page
            )
        )
        return templates.TemplateResponse(request, "people/training/programs.html", context)

    def program_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new program form."""
        context = base_context(request, auth, "New Training Program", "training", db=db)
        context["request"] = request
        context.update(self.program_form_context(db, coerce_uuid(auth.organization_id)))
        return templates.TemplateResponse(request, "people/training/program_form.html", context)

    def program_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        program_id: str,
        success: Optional[str] = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render program detail page."""
        ctx = self.program_detail_context(db, coerce_uuid(auth.organization_id), program_id)

        if not ctx.get("program"):
            return RedirectResponse(url="/people/training/programs", status_code=303)

        context = base_context(request, auth, ctx["program"].program_name, "training", db=db)
        context["request"] = request
        context.update(ctx)
        context["success"] = success
        return templates.TemplateResponse(request, "people/training/program_detail.html", context)

    def program_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        program_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render program edit form."""
        ctx = self.program_form_context(db, coerce_uuid(auth.organization_id), program_id)

        if not ctx.get("program"):
            return RedirectResponse(url="/people/training/programs", status_code=303)

        context = base_context(request, auth, f"Edit {ctx['program'].program_code}", "training", db=db)
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(request, "people/training/program_form.html", context)

    async def create_program_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle program creation form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            input_kwargs = self.build_program_input(dict(form_data))
            program = svc.create_program(org_id, **input_kwargs)
            db.commit()
            return RedirectResponse(
                url=f"/people/training/programs/{program.program_id}?success=Program+created+successfully",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("create_program_response: failed")
            context = base_context(request, auth, "New Training Program", "training", db=db)
            context["request"] = request
            context.update(self.program_form_context(db, org_id))
            context["form_data"] = dict(form_data)
            context["error"] = str(e)
            return templates.TemplateResponse(request, "people/training/program_form.html", context)

    async def update_program_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        program_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle program update form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            input_kwargs = self.build_program_input(dict(form_data))
            svc.update_program(org_id, coerce_uuid(program_id), **input_kwargs)
            db.commit()
            return RedirectResponse(
                url=f"/people/training/programs/{program_id}?success=Program+updated+successfully",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("update_program_response: failed")
            context = base_context(request, auth, "Edit Program", "training", db=db)
            context["request"] = request
            context.update(self.program_form_context(db, org_id, program_id))
            context["error"] = str(e)
            return templates.TemplateResponse(request, "people/training/program_form.html", context)

    def activate_program_response(
        self,
        auth: WebAuthContext,
        db: Session,
        program_id: str,
    ) -> RedirectResponse:
        """Handle program activation."""
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            svc.activate_program(org_id, coerce_uuid(program_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/training/programs/{program_id}?success=Program+activated",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/training/programs/{program_id}?error={str(e)}",
                status_code=303,
            )

    def retire_program_response(
        self,
        auth: WebAuthContext,
        db: Session,
        program_id: str,
    ) -> RedirectResponse:
        """Handle program retirement."""
        org_id = coerce_uuid(auth.organization_id)
        svc = TrainingService(db)

        try:
            svc.retire_program(org_id, coerce_uuid(program_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/training/programs/{program_id}?success=Program+retired",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/training/programs/{program_id}?error={str(e)}",
                status_code=303,
            )
