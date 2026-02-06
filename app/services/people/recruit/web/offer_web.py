"""
Recruit Web Service - Job Offer methods.

Provides view-focused data and operations for job offer web routes.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from datetime import date
from uuid import UUID

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.recruit import OfferStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import (
    DepartmentFilters,
    DesignationFilters,
    OrganizationService,
)
from app.services.people.recruit import RecruitmentService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import (
    EMPLOYMENT_TYPES,
    PAY_FREQUENCIES,
    logger,
    parse_date_only,
    parse_decimal,
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


class OfferWebService:
    """Web service methods for job offers."""

    # ─────────────────────────────────────────────────────────────────────────
    # Context Builders
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def list_offers_context(
        db: Session,
        organization_id: UUID,
        status: Optional[str] = None,
        job_opening_id: Optional[str] = None,
        applicant_id: Optional[str] = None,
        page: int = 1,
    ) -> dict:
        """Build context for job offers list page."""
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = RecruitmentService(db)

        status_enum = parse_status(status, OfferStatus)
        result = svc.list_job_offers(
            organization_id,
            status=status_enum,
            applicant_id=parse_uuid(applicant_id),
            job_opening_id=parse_uuid(job_opening_id),
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
            "offers": result.items,
            "job_openings": job_openings,
            "applicants": applicants,
            "status": status,
            "job_opening_id": job_opening_id,
            "applicant_id": applicant_id,
            "statuses": [s.value for s in OfferStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }

    @staticmethod
    def offer_form_context(
        db: Session,
        organization_id: UUID,
        offer_id: Optional[str] = None,
        applicant_id: Optional[str] = None,
    ) -> dict:
        """Build context for job offer create/edit form."""
        svc = RecruitmentService(db)
        org_svc = OrganizationService(db, organization_id)

        applicants = svc.list_applicants(
            organization_id,
            pagination=PaginationParams(limit=200),
        ).items

        job_openings = svc.list_job_openings(
            organization_id,
            pagination=PaginationParams(limit=200),
        ).items

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        designations = org_svc.list_designations(
            DesignationFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        offer = None
        if offer_id:
            try:
                offer = svc.get_job_offer(organization_id, coerce_uuid(offer_id))
            except Exception:
                offer = None

        form_data = {}
        if applicant_id and not offer:
            form_data["applicant_id"] = applicant_id

        return {
            "offer": offer,
            "applicants": applicants,
            "job_openings": job_openings,
            "departments": departments,
            "designations": designations,
            "employment_types": EMPLOYMENT_TYPES,
            "pay_frequencies": PAY_FREQUENCIES,
            "form_data": form_data,
        }

    @staticmethod
    def offer_detail_context(
        db: Session,
        organization_id: UUID,
        offer_id: str,
    ) -> dict:
        """Build context for job offer detail page."""
        svc = RecruitmentService(db)

        try:
            offer = svc.get_job_offer(organization_id, coerce_uuid(offer_id))
        except Exception:
            return {"offer": None}

        return {
            "offer": offer,
            "statuses": [s.value for s in OfferStatus],
            "today": date.today(),
        }

    @staticmethod
    def build_offer_input(form_data: dict) -> dict:
        """Build input kwargs for job offer from form data."""
        return {
            "designation_id": coerce_uuid(form_data["designation_id"])
            if form_data.get("designation_id")
            else None,
            "department_id": coerce_uuid(form_data["department_id"])
            if form_data.get("department_id")
            else None,
            "offer_date": parse_date_only(form_data.get("offer_date")),
            "valid_until": parse_date_only(form_data.get("valid_until")),
            "expected_joining_date": parse_date_only(
                form_data.get("expected_joining_date")
            ),
            "base_salary": parse_decimal(form_data.get("base_salary")),
            "currency_code": form_data.get("currency_code", "NGN"),
            "pay_frequency": form_data.get("pay_frequency", "MONTHLY"),
            "signing_bonus": parse_decimal(form_data.get("signing_bonus")),
            "relocation_allowance": parse_decimal(
                form_data.get("relocation_allowance")
            ),
            "other_benefits": form_data.get("other_benefits") or None,
            "employment_type": form_data.get("employment_type", "FULL_TIME"),
            "probation_months": parse_int(form_data.get("probation_months")) or 3,
            "notice_period_days": parse_int(form_data.get("notice_period_days")) or 30,
            "terms_and_conditions": form_data.get("terms_and_conditions") or None,
            "notes": form_data.get("notes") or None,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Response Methods
    # ─────────────────────────────────────────────────────────────────────────

    def list_offers_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str] = None,
        job_opening_id: Optional[str] = None,
        applicant_id: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render job offers list page."""
        context = base_context(request, auth, "Job Offers", "recruit", db=db)
        context["request"] = request
        context.update(
            self.list_offers_context(
                db,
                coerce_uuid(auth.organization_id),
                status=status,
                job_opening_id=job_opening_id,
                applicant_id=applicant_id,
                page=page,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/offers.html", context
        )

    def offer_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        applicant_id: Optional[str] = None,
    ) -> HTMLResponse:
        """Render new job offer form."""
        context = base_context(request, auth, "Create Job Offer", "recruit", db=db)
        context["request"] = request
        context.update(
            self.offer_form_context(
                db,
                coerce_uuid(auth.organization_id),
                applicant_id=applicant_id,
            )
        )
        return templates.TemplateResponse(
            request, "people/recruit/offer_form.html", context
        )

    def offer_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        offer_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render job offer detail page."""
        ctx = self.offer_detail_context(db, coerce_uuid(auth.organization_id), offer_id)

        if not ctx.get("offer"):
            return RedirectResponse(url="/people/recruit/offers", status_code=303)

        context = base_context(
            request, auth, f"Offer {ctx['offer'].offer_number}", "recruit", db=db
        )
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(
            request, "people/recruit/offer_detail.html", context
        )

    def offer_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        offer_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render job offer edit form."""
        ctx = self.offer_form_context(db, coerce_uuid(auth.organization_id), offer_id)

        if not ctx.get("offer"):
            return RedirectResponse(url="/people/recruit/offers", status_code=303)

        context = base_context(request, auth, "Edit Job Offer", "recruit", db=db)
        context["request"] = request
        context.update(ctx)
        return templates.TemplateResponse(
            request, "people/recruit/offer_form.html", context
        )

    async def create_offer_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle job offer creation form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            applicant_id = form_data.get("applicant_id")
            job_opening_id = form_data.get("job_opening_id")
            input_kwargs = self.build_offer_input(dict(form_data))
            offer = svc.create_job_offer(
                org_id,
                applicant_id=coerce_uuid(applicant_id),
                job_opening_id=coerce_uuid(job_opening_id),
                **input_kwargs,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/recruit/offers/{offer.offer_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("create_offer_response: failed")
            context = base_context(request, auth, "Create Job Offer", "recruit", db=db)
            context["request"] = request
            context.update(self.offer_form_context(db, org_id))
            context["form_data"] = dict(form_data)
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "people/recruit/offer_form.html", context
            )

    async def update_offer_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        offer_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle job offer update form submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            input_kwargs = self.build_offer_input(dict(form_data))
            svc.update_job_offer(org_id, coerce_uuid(offer_id), **input_kwargs)
            db.commit()
            return RedirectResponse(
                url=f"/people/recruit/offers/{offer_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("update_offer_response: failed")
            context = base_context(request, auth, "Edit Job Offer", "recruit", db=db)
            context["request"] = request
            context.update(self.offer_form_context(db, org_id, offer_id))
            context["error"] = str(e)
            return templates.TemplateResponse(
                request, "people/recruit/offer_form.html", context
            )

    def extend_offer_response(
        self,
        auth: WebAuthContext,
        db: Session,
        offer_id: str,
    ) -> RedirectResponse:
        """Handle extending offer to candidate."""
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            svc.extend_offer(org_id, coerce_uuid(offer_id))
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/offers/{offer_id}", status_code=303
        )

    def accept_offer_response(
        self,
        auth: WebAuthContext,
        db: Session,
        offer_id: str,
    ) -> RedirectResponse:
        """Handle marking offer as accepted."""
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            svc.accept_offer(org_id, coerce_uuid(offer_id))
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/offers/{offer_id}", status_code=303
        )

    async def decline_offer_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        offer_id: str,
    ) -> RedirectResponse:
        """Handle marking offer as declined."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            reason = _get_form_str(form_data, "reason") or None
            svc.decline_offer(org_id, coerce_uuid(offer_id), reason=reason)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/offers/{offer_id}", status_code=303
        )

    async def withdraw_offer_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        offer_id: str,
    ) -> RedirectResponse:
        """Handle withdrawing an offer."""
        org_id = coerce_uuid(auth.organization_id)
        svc = RecruitmentService(db)

        try:
            svc.withdraw_offer(org_id, coerce_uuid(offer_id))
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/recruit/offers/{offer_id}", status_code=303
        )
