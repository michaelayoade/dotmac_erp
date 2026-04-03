"""
Monthly Review Web Service — OHCSF PMS web view service for monthly reviews.

Provides view-focused operations for review list, detail, create, submit,
and acknowledgement workflow within the PMS module.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile

from app.models.people.perf.pms_enums import MonthlyReviewStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import EmployeeFilters, OrganizationService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import parse_date, parse_uuid

logger = logging.getLogger(__name__)


def _get_form_str(form: FormData | None, key: str, default: str = "") -> str:
    if form is None:
        return default
    value = form.get(key, default)
    if isinstance(value, UploadFile) or value is None:
        return default
    return str(value).strip()


class MonthlyReviewWebService:
    """Web service for OHCSF monthly review pages."""

    # ─────────────────────────────────────────────────────────────────────────
    # List
    # ─────────────────────────────────────────────────────────────────────────

    def list_reviews_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None = None,
        contract_id: str | None = None,
        employee_id: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render the monthly reviews list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)

        from app.services.people.perf.monthly_review_service import (
            MonthlyReviewService,
        )

        svc = MonthlyReviewService(db)

        parsed_status: MonthlyReviewStatus | None = None
        if status:
            try:
                parsed_status = MonthlyReviewStatus(status)
            except ValueError:
                parsed_status = None

        result = svc.list_reviews(
            org_id,
            status=parsed_status,
            contract_id=parse_uuid(contract_id),
            employee_id=parse_uuid(employee_id),
            search=search,
            pagination=pagination,
        )

        context = base_context(request, auth, "Monthly Reviews", "pms-reviews", db=db)
        context["request"] = request
        context.update(
            {
                "reviews": result.items,
                "status": status,
                "contract_id": contract_id,
                "employee_id": employee_id,
                "search": search,
                "statuses": [s.value for s in MonthlyReviewStatus],
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/reviews.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Detail
    # ─────────────────────────────────────────────────────────────────────────

    def review_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        review_id: str,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render the monthly review detail page."""
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.monthly_review_service import (
            MonthlyReviewService,
        )

        svc = MonthlyReviewService(db)
        try:
            review = svc.get_review(org_id, coerce_uuid(review_id))
        except Exception:
            return RedirectResponse(url="/people/perf/pms/reviews", status_code=303)

        context = base_context(
            request,
            auth,
            f"Monthly Review — {review.review_month.strftime('%b %Y') if review.review_month else ''}",
            "pms-reviews",
            db=db,
        )
        context["request"] = request
        context.update(
            {
                "review": review,
                "success": success,
                "error": error,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/review_detail.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Form (create)
    # ─────────────────────────────────────────────────────────────────────────

    def review_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        contract_id: str | None = None,
        employee_id: str | None = None,
        form_data: dict | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        """Render the new monthly review form."""
        org_id = coerce_uuid(auth.organization_id)
        org_svc = OrganizationService(db, org_id)

        from app.services.people.perf.contract_service import (
            PerformanceContractService,
        )

        contract_svc = PerformanceContractService(db)
        employees = org_svc.list_employees(
            EmployeeFilters(is_active=True),
            PaginationParams(limit=500),
        ).items

        # Prefill contract if given
        prefill_contract = None
        if contract_id:
            try:
                prefill_contract = contract_svc.get_contract(
                    org_id, coerce_uuid(contract_id)
                )
            except Exception:
                prefill_contract = None

        # Load active contracts for the employee selector
        from app.models.people.perf.pms_enums import ContractStatus

        contracts = contract_svc.list_contracts(
            org_id,
            status=ContractStatus.ACTIVE,
            pagination=PaginationParams(limit=200),
        ).items

        context = base_context(request, auth, "New Monthly Review", "pms-reviews", db=db)
        context["request"] = request
        context.update(
            {
                "review": None,
                "employees": employees,
                "contracts": contracts,
                "prefill_contract": prefill_contract,
                "prefill_contract_id": contract_id,
                "prefill_employee_id": employee_id,
                "form_data": form_data or {},
                "error": error,
                "today": date.today().isoformat(),
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/review_form.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Create (POST)
    # ─────────────────────────────────────────────────────────────────────────

    async def create_review_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle monthly review creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.monthly_review_service import (
            MonthlyReviewService,
        )

        svc = MonthlyReviewService(db)

        try:
            employee_id_str = _get_form_str(form_data, "employee_id")
            reviewer_id_str = _get_form_str(form_data, "reviewer_id")
            contract_id_str = _get_form_str(form_data, "contract_id")
            review_month_str = _get_form_str(form_data, "review_month")
            challenges = _get_form_str(form_data, "challenges") or None
            support_required = _get_form_str(form_data, "support_required") or None

            if not employee_id_str:
                raise ValueError("Employee is required")
            if not reviewer_id_str:
                raise ValueError("Reviewer is required")
            if not contract_id_str:
                raise ValueError("Contract is required")
            if not review_month_str:
                raise ValueError("Review month is required")

            parsed_month = parse_date(review_month_str)
            if parsed_month is None:
                raise ValueError("Invalid review month date")
            # Normalise to first of month
            review_month = date(parsed_month.year, parsed_month.month, 1)

            review = svc.create_review(
                org_id,
                employee_id=coerce_uuid(employee_id_str),
                reviewer_id=coerce_uuid(reviewer_id_str),
                contract_id=coerce_uuid(contract_id_str),
                review_month=review_month,
                challenges=challenges,
                support_required=support_required,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/reviews/{review.review_id}?saved=1",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            return self.review_form_response(
                request,
                auth,
                db,
                form_data=dict(form_data),
                error=str(e),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Submit (POST)
    # ─────────────────────────────────────────────────────────────────────────

    async def submit_review_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        review_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle review submission by reviewer."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.monthly_review_service import (
            MonthlyReviewService,
        )

        svc = MonthlyReviewService(db)

        try:
            reviewer_feedback = _get_form_str(form_data, "reviewer_feedback") or None
            agreed_actions = _get_form_str(form_data, "agreed_actions") or None
            challenges = _get_form_str(form_data, "challenges") or None
            support_required = _get_form_str(form_data, "support_required") or None

            svc.submit_review(
                org_id,
                coerce_uuid(review_id),
                objective_progress={},
                challenges=challenges,
                support_required=support_required,
                reviewer_feedback=reviewer_feedback,
                agreed_actions=agreed_actions,
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Submit review failed for %s: %s", review_id, e)
            return RedirectResponse(
                url=f"/people/perf/pms/reviews/{review_id}?error=submit_failed",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/people/perf/pms/reviews/{review_id}?saved=1",
            status_code=303,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Acknowledge (POST)
    # ─────────────────────────────────────────────────────────────────────────

    def acknowledge_review_response(
        self,
        auth: WebAuthContext,
        db: Session,
        review_id: str,
    ) -> RedirectResponse:
        """Handle review acknowledgement by employee."""
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.monthly_review_service import (
            MonthlyReviewService,
        )

        svc = MonthlyReviewService(db)
        try:
            svc.acknowledge_review(org_id, coerce_uuid(review_id))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Acknowledge review failed for %s: %s", review_id, e)
            return RedirectResponse(
                url=f"/people/perf/pms/reviews/{review_id}?error=ack_failed",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/people/perf/pms/reviews/{review_id}?saved=1",
            status_code=303,
        )
