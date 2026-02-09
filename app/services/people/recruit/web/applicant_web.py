"""
Recruit Web Service - Applicant methods.

Provides view-focused data and operations for applicant web routes.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.models.people.recruit import ApplicantStatus, JobOpeningStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.recruit import RecruitmentService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import (
    logger,
    parse_date_only,
    parse_int,
    parse_status,
    parse_uuid,
)


def _get_form_str(form: Any, key: str, default: str = "") -> str:
    value = form.get(key, default) if form is not None else default
    if value is None or isinstance(value, UploadFile):
        return default
    return str(value).strip()


class ApplicantWebService:
    """Web service methods for applicants."""

    # ─────────────────────────────────────────────────────────────────────────
    # Context Builders
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def list_applicants_context(
        db: Session,
        organization_id: UUID,
        search: str | None = None,
        status: str | None = None,
        job_opening_id: str | None = None,
        source: str | None = None,
        page: int = 1,
    ) -> dict:
        """Build context for applicants list page."""
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = RecruitmentService(db)

        status_enum = parse_status(status, ApplicantStatus)
        result = svc.list_applicants(
            organization_id,
            search=search,
            status=status_enum,
            job_opening_id=parse_uuid(job_opening_id),
            source=source,
            pagination=pagination,
        )

        job_openings = svc.list_job_openings(
            organization_id,
            pagination=PaginationParams(limit=200),
        ).items

        return {
            "applicants": result.items,
            "job_openings": job_openings,
            "search": search,
            "status": status,
            "job_opening_id": job_opening_id,
            "source": source,
            "statuses": [s.value for s in ApplicantStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }

    @staticmethod
    def applicant_form_context(
        db: Session,
        organization_id: UUID,
        applicant_id: str | None = None,
        job_opening_id: str | None = None,
    ) -> dict:
        """Build context for applicant create/edit form."""
        svc = RecruitmentService(db)

        # For new applicants, only show open jobs
        if applicant_id:
            job_openings = svc.list_job_openings(
                organization_id,
                pagination=PaginationParams(limit=200),
            ).items
        else:
            job_openings = svc.list_job_openings(
                organization_id,
                status=JobOpeningStatus.OPEN,
                pagination=PaginationParams(limit=200),
            ).items

        applicant = None
        if applicant_id:
            try:
                applicant = svc.get_applicant(
                    organization_id, coerce_uuid(applicant_id)
                )
            except Exception:
                applicant = None

        form_data = {}
        if job_opening_id and not applicant:
            form_data["job_opening_id"] = job_opening_id

        return {
            "applicant": applicant,
            "job_openings": job_openings,
            "form_data": form_data,
        }

    @staticmethod
    def applicant_detail_context(
        db: Session,
        organization_id: UUID,
        applicant_id: str,
    ) -> dict:
        """Build context for applicant detail page."""
        svc = RecruitmentService(db)

        try:
            applicant = svc.get_applicant(organization_id, coerce_uuid(applicant_id))
        except Exception:
            return {"applicant": None, "interviews": [], "offers": []}

        interviews = svc.list_interviews(
            organization_id,
            applicant_id=coerce_uuid(applicant_id),
            pagination=PaginationParams(limit=50),
        ).items

        offers = svc.list_job_offers(
            organization_id,
            applicant_id=coerce_uuid(applicant_id),
            pagination=PaginationParams(limit=10),
        ).items

        return {
            "applicant": applicant,
            "interviews": interviews,
            "offers": offers,
            "statuses": [s.value for s in ApplicantStatus],
        }

    @staticmethod
    def build_applicant_input(form_data: dict) -> dict:
        """Build input kwargs for applicant from form data."""
        return {
            "first_name": form_data.get("first_name", ""),
            "last_name": form_data.get("last_name", ""),
            "email": form_data.get("email", ""),
            "phone": form_data.get("phone") or None,
            "date_of_birth": parse_date_only(form_data.get("date_of_birth")),
            "gender": form_data.get("gender") or None,
            "city": form_data.get("city") or None,
            "country_code": form_data.get("country_code") or None,
            "current_employer": form_data.get("current_employer") or None,
            "current_job_title": form_data.get("current_job_title") or None,
            "years_of_experience": parse_int(
                _get_form_str(form_data, "years_of_experience") or None
            ),
            "highest_qualification": form_data.get("highest_qualification") or None,
            "skills": form_data.get("skills") or None,
            "source": form_data.get("source") or None,
            "cover_letter": form_data.get("cover_letter") or None,
            "resume_url": form_data.get("resume_url") or None,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Response Methods
    # ─────────────────────────────────────────────────────────────────────────

    def list_applicants_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None = None,
        status: str | None = None,
        job_opening_id: str | None = None,
        source: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render applicants list page."""
        context = base_context(request, auth, "Applicants", "recruit", db=db)
        context["request"] = request
        context.update(
            self.list_applicants_context(
                db,
                coerce_uuid(auth.organization_id),
                search=search,
                status=status,
                job_opening_id=job_opening_id,
                source=source,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/applicants.html", context
        )

    def applicant_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        job_opening_id: str | None = None,
    ) -> HTMLResponse:
        """Render new applicant form."""
        context = base_context(request, auth, "New Applicant", "recruit", db=db)
        context["request"] = request
        context.update(
            self.applicant_form_context(
                db,
                coerce_uuid(auth.organization_id),
                job_opening_id=job_opening_id,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/applicant_form.html", context
        )

    def applicant_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        applicant_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render applicant detail page."""
        ctx = self.applicant_detail_context(
            db, coerce_uuid(auth.organization_id), applicant_id
        )

        if not ctx.get("applicant"):
            return RedirectResponse(url="/people/recruit/applicants", status_code=303)

        context = base_context(
            request, auth, ctx["applicant"].full_name, "recruit", db=db
        )
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(
            request, "people/recruit/applicant_detail.html", context
        )

    def applicant_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        applicant_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render applicant edit form."""
        ctx = self.applicant_form_context(
            db, coerce_uuid(auth.organization_id), applicant_id
        )

        if not ctx.get("applicant"):
            return RedirectResponse(url="/people/recruit/applicants", status_code=303)

        context = base_context(request, auth, "Edit Applicant", "recruit", db=db)
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(
            request, "people/recruit/applicant_form.html", context
        )

    async def create_applicant_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle applicant creation form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            job_opening_id = _get_form_str(form_data, "job_opening_id") or None
            input_kwargs = self.build_applicant_input(dict(form_data))
            applicant = svc.create_applicant(
                org_id,
                job_opening_id=coerce_uuid(job_opening_id),
                **input_kwargs,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/recruit/applicants/{applicant.applicant_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("create_applicant_response: failed")
            context = base_context(request, auth, "New Applicant", "recruit", db=db)
            context["request"] = request
            context.update(self.applicant_form_context(db, org_id))
            context["form_data"] = dict(form_data)
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "people/recruit/applicant_form.html", context
            )

    async def update_applicant_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        applicant_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle applicant update form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            input_kwargs = self.build_applicant_input(dict(form_data))
            # Include additional fields for update
            input_kwargs["notes"] = _get_form_str(form_data, "notes") or None
            input_kwargs["overall_rating"] = parse_int(
                _get_form_str(form_data, "overall_rating") or None
            )

            svc.update_applicant(org_id, coerce_uuid(applicant_id), **input_kwargs)
            db.commit()
            return RedirectResponse(
                url=f"/people/recruit/applicants/{applicant_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("update_applicant_response: failed")
            context = base_context(request, auth, "Edit Applicant", "recruit", db=db)
            context["request"] = request
            context.update(self.applicant_form_context(db, org_id, applicant_id))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "people/recruit/applicant_form.html", context
            )

    async def advance_applicant_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        applicant_id: str,
    ) -> RedirectResponse:
        """Handle applicant status advancement."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            to_status = _get_form_str(form_data, "to_status") or None
            notes = _get_form_str(form_data, "notes") or None
            status_enum = ApplicantStatus(to_status)
            svc.advance_applicant(
                org_id, coerce_uuid(applicant_id), status_enum, notes=notes
            )
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/applicants/{applicant_id}", status_code=303
        )

    async def reject_applicant_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        applicant_id: str,
    ) -> RedirectResponse:
        """Handle applicant rejection."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            reason = _get_form_str(form_data, "reason") or None
            svc.reject_applicant(org_id, coerce_uuid(applicant_id), reason=reason)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/applicants/{applicant_id}", status_code=303
        )

    def delete_applicant_response(
        self,
        auth: WebAuthContext,
        db: Session,
        applicant_id: str,
    ) -> RedirectResponse:
        """Handle applicant deletion."""
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            svc.delete_applicant(org_id, coerce_uuid(applicant_id))
            db.commit()
            return RedirectResponse(url="/people/recruit/applicants", status_code=303)
        except Exception:
            db.rollback()
            return RedirectResponse(
                url=f"/people/recruit/applicants/{applicant_id}", status_code=303
            )
