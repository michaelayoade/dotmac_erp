"""
Performance Web Service - Unified web view service for performance module.

Provides view-focused data and operations for all perf web routes including:
appraisals, feedback, goals/KPIs, cycles, KRAs, templates, scorecards, and reports.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.perf import AppraisalStatus, KPIStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import (
    DepartmentFilters,
    EmployeeFilters,
    OrganizationService,
)
from app.services.people.perf import PerformanceService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import (
    FEEDBACK_TYPES,
    KPI_MEASUREMENT_TYPES,
    parse_appraisal_status,
    parse_bool,
    parse_date,
    parse_decimal,
    parse_int,
    parse_kpi_status,
    parse_uuid,
)


def _get_form_str(form: Any, key: str, default: str = "") -> str:
    value = form.get(key, default) if form is not None else default
    if isinstance(value, UploadFile) or value is None:
        return default
    return str(value).strip()


class PerfWebService:
    """Unified Performance Web Service."""

    # ─────────────────────────────────────────────────────────────────────────
    # Appraisals
    # ─────────────────────────────────────────────────────────────────────────

    def list_appraisals_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None = None,
        employee_id: str | None = None,
        cycle_id: str | None = None,
        manager_id: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render appraisals list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = PerformanceService(db)

        result = svc.list_appraisals(
            org_id,
            status=parse_appraisal_status(status),
            employee_id=parse_uuid(employee_id),
            cycle_id=parse_uuid(cycle_id),
            manager_id=parse_uuid(manager_id),
            pagination=pagination,
        )

        context = base_context(request, auth, "Appraisals", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "appraisals": result.items,
                "status": status,
                "employee_id": employee_id,
                "cycle_id": cycle_id,
                "manager_id": manager_id,
                "statuses": [s.value for s in AppraisalStatus],
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/appraisals.html", context
        )

    def appraisal_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new appraisal form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        cycles = svc.list_cycles(org_id, pagination=PaginationParams(limit=100)).items
        employees = org_svc.list_employees(
            EmployeeFilters(is_active=True),
            PaginationParams(limit=500),
        ).items

        context = base_context(request, auth, "New Appraisal", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "appraisal": None,
                "cycles": cycles,
                "employees": employees,
                "form_data": {},
                "error": None,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/appraisal_form.html", context
        )

    async def create_appraisal_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle appraisal creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            employee_id = _get_form_str(form_data, "employee_id")
            cycle_id = _get_form_str(form_data, "cycle_id")
            manager_id = _get_form_str(form_data, "manager_id")
            template_id = _get_form_str(form_data, "template_id")

            if not employee_id:
                raise ValueError("Employee is required")
            if not cycle_id:
                raise ValueError("Cycle is required")
            if not manager_id:
                raise ValueError("Manager is required")

            appraisal = svc.create_appraisal(
                org_id,
                employee_id=coerce_uuid(employee_id),
                cycle_id=coerce_uuid(cycle_id),
                manager_id=coerce_uuid(manager_id),
                template_id=coerce_uuid(template_id) if template_id else None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/appraisals/{appraisal.appraisal_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            org_svc = OrganizationService(db, org_id)
            context = base_context(request, auth, "New Appraisal", "perf", db=db)
            context["request"] = request
            context.update(
                {
                    "appraisal": None,
                    "cycles": svc.list_cycles(
                        org_id, pagination=PaginationParams(limit=100)
                    ).items,
                    "employees": org_svc.list_employees(
                        EmployeeFilters(is_active=True), PaginationParams(limit=500)
                    ).items,
                    "form_data": dict(form_data),
                    "error": str(e),
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/appraisal_form.html", context
            )

    def appraisal_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
        success: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render appraisal detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
        except Exception:
            return RedirectResponse(url="/people/perf/appraisals", status_code=303)

        context = base_context(
            request,
            auth,
            f"Appraisal - {appraisal.employee.full_name if appraisal.employee else 'Unknown'}",
            "perf",
            db=db,
        )
        context["request"] = request
        context.update(
            {
                "appraisal": appraisal,
                "success": success,
                "error": None,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/appraisal_detail.html", context
        )

    def appraisal_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render appraisal edit form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        try:
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
        except Exception:
            return RedirectResponse(url="/people/perf/appraisals", status_code=303)

        context = base_context(request, auth, "Edit Appraisal", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "appraisal": appraisal,
                "cycles": svc.list_cycles(
                    org_id, pagination=PaginationParams(limit=100)
                ).items,
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "form_data": {},
                "error": None,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/appraisal_form.html", context
        )

    async def update_appraisal_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle appraisal update."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            manager_id = _get_form_str(form_data, "manager_id")
            svc.update_appraisal(
                org_id,
                coerce_uuid(appraisal_id),
                manager_id=coerce_uuid(manager_id) if manager_id else None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/appraisals/{appraisal_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            org_svc = OrganizationService(db, org_id)
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
            context = base_context(request, auth, "Edit Appraisal", "perf", db=db)
            context["request"] = request
            context.update(
                {
                    "appraisal": appraisal,
                    "cycles": svc.list_cycles(
                        org_id, pagination=PaginationParams(limit=100)
                    ).items,
                    "employees": org_svc.list_employees(
                        EmployeeFilters(is_active=True), PaginationParams(limit=500)
                    ).items,
                    "form_data": {},
                    "error": str(e),
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/appraisal_form.html", context
            )

    def cancel_appraisal_response(
        self,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> RedirectResponse:
        """Handle appraisal cancellation."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.update_appraisal(
                org_id,
                coerce_uuid(appraisal_id),
                status=AppraisalStatus.CANCELLED,
            )
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}", status_code=303
        )

    def start_self_assessment_response(
        self,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> RedirectResponse:
        """Start self-assessment phase."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.update_appraisal(
                org_id,
                coerce_uuid(appraisal_id),
                status=AppraisalStatus.SELF_ASSESSMENT,
            )
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}", status_code=303
        )

    def self_assessment_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render self-assessment form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
        except Exception:
            return RedirectResponse(url="/people/perf/appraisals", status_code=303)

        context = base_context(request, auth, "Self Assessment", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "appraisal": appraisal,
                "form_data": {},
                "error": None,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/self_assessment_form.html", context
        )

    async def submit_self_assessment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle self-assessment submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            self_rating = parse_int(_get_form_str(form_data, "self_rating") or None)
            if self_rating is None:
                raise ValueError("Self rating is required")
            svc.submit_self_assessment(
                org_id,
                coerce_uuid(appraisal_id),
                self_overall_rating=self_rating,
                self_summary=_get_form_str(form_data, "self_comments") or None,
                achievements=_get_form_str(form_data, "achievements") or None,
                challenges=_get_form_str(form_data, "challenges") or None,
                development_needs=_get_form_str(form_data, "development_needs") or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/appraisals/{appraisal_id}?success=Self+assessment+submitted",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
            context = base_context(request, auth, "Self Assessment", "perf", db=db)
            context["request"] = request
            context.update(
                {
                    "appraisal": appraisal,
                    "form_data": dict(form_data),
                    "error": str(e),
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/self_assessment_form.html", context
            )

    def manager_review_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render manager review form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
        except Exception:
            return RedirectResponse(url="/people/perf/appraisals", status_code=303)

        context = base_context(request, auth, "Manager Review", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "appraisal": appraisal,
                "form_data": {},
                "error": None,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/manager_review_form.html", context
        )

    async def submit_manager_review_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle manager review submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            manager_rating = parse_int(
                _get_form_str(form_data, "manager_rating") or None
            )
            if manager_rating is None:
                raise ValueError("Manager rating is required")

            manager_summary = _get_form_str(form_data, "manager_comments") or None
            strengths = _get_form_str(form_data, "strengths") or None
            areas_for_improvement = (
                _get_form_str(form_data, "areas_for_improvement") or None
            )

            if strengths or areas_for_improvement:
                extra_parts = []
                if strengths:
                    extra_parts.append(f"Strengths: {strengths}")
                if areas_for_improvement:
                    extra_parts.append(
                        f"Areas for improvement: {areas_for_improvement}"
                    )
                extra_text = "\n".join(extra_parts)
                manager_summary = (
                    f"{manager_summary}\n\n{extra_text}"
                    if manager_summary
                    else extra_text
                )

            svc.submit_manager_review(
                org_id,
                coerce_uuid(appraisal_id),
                manager_overall_rating=manager_rating,
                manager_summary=manager_summary,
                manager_recommendations=_get_form_str(form_data, "recommendations")
                or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/appraisals/{appraisal_id}?success=Manager+review+submitted",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
            context = base_context(request, auth, "Manager Review", "perf", db=db)
            context["request"] = request
            context.update(
                {
                    "appraisal": appraisal,
                    "form_data": dict(form_data),
                    "error": str(e),
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/manager_review_form.html", context
            )

    def calibration_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render calibration form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
        except Exception:
            return RedirectResponse(url="/people/perf/appraisals", status_code=303)

        context = base_context(request, auth, "Calibration", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "appraisal": appraisal,
                "form_data": {},
                "error": None,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/calibration_form.html", context
        )

    async def submit_calibration_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle calibration submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            final_rating = parse_int(_get_form_str(form_data, "final_rating") or None)
            if final_rating is None:
                raise ValueError("Final rating is required")
            svc.submit_calibration(
                org_id,
                coerce_uuid(appraisal_id),
                calibrated_rating=final_rating,
                calibration_notes=_get_form_str(form_data, "calibration_notes") or None,
                rating_label=_get_form_str(form_data, "rating_label") or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/appraisals/{appraisal_id}?success=Appraisal+calibrated",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
            context = base_context(request, auth, "Calibration", "perf", db=db)
            context["request"] = request
            context.update(
                {
                    "appraisal": appraisal,
                    "form_data": dict(form_data),
                    "error": str(e),
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/calibration_form.html", context
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Feedback
    # ─────────────────────────────────────────────────────────────────────────

    def list_feedback_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str | None = None,
        feedback_type: str | None = None,
        submitted: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render feedback list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = PerformanceService(db)

        result = svc.list_feedback(
            org_id,
            appraisal_id=parse_uuid(appraisal_id),
            feedback_type=feedback_type or None,
            submitted=parse_bool(submitted),
            pagination=pagination,
        )

        context = base_context(request, auth, "360° Feedback", "perf", db=db)
        context["request"] = request
        success = request.query_params.get("success")
        context.update(
            {
                "feedback_list": result.items,
                "appraisal_id": appraisal_id,
                "feedback_type": feedback_type,
                "submitted": submitted,
                "feedback_types": FEEDBACK_TYPES,
                "success": success,
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(request, "people/perf/feedback.html", context)

    def request_feedback_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appraisal_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render request feedback form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        try:
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
        except Exception:
            return RedirectResponse(url="/people/perf/appraisals", status_code=303)

        employees = org_svc.list_employees(
            EmployeeFilters(is_active=True),
            PaginationParams(limit=500),
        ).items

        context = base_context(request, auth, "Request Feedback", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "appraisal": appraisal,
                "employees": employees,
                "feedback_types": FEEDBACK_TYPES,
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/feedback_request_form.html", context
        )

    async def create_feedback_request_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle feedback request creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)
        appraisal_id = _get_form_str(form_data, "appraisal_id")
        feedback_from_id = _get_form_str(form_data, "feedback_from_id")
        feedback_type = _get_form_str(form_data, "feedback_type")

        try:
            if not appraisal_id:
                raise ValueError("Appraisal is required")
            if not feedback_from_id:
                raise ValueError("Feedback recipient is required")
            if not feedback_type:
                raise ValueError("Feedback type is required")
            svc.request_feedback(
                org_id,
                appraisal_id=coerce_uuid(appraisal_id),
                feedback_from_id=coerce_uuid(feedback_from_id),
                feedback_type=feedback_type,
                is_anonymous=_get_form_str(form_data, "is_anonymous") == "true",
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/appraisals/{appraisal_id}?success=Feedback+requested",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            org_svc = OrganizationService(db, org_id)
            appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
            context = base_context(request, auth, "Request Feedback", "perf", db=db)
            context["request"] = request
            context.update(
                {
                    "appraisal": appraisal,
                    "employees": org_svc.list_employees(
                        EmployeeFilters(is_active=True), PaginationParams(limit=500)
                    ).items,
                    "feedback_types": FEEDBACK_TYPES,
                    "form_data": dict(form_data),
                    "error": str(e),
                    "errors": {},
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/feedback_request_form.html", context
            )

    def feedback_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        feedback_id: str,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render feedback detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            feedback = svc.get_feedback(org_id, coerce_uuid(feedback_id))
        except Exception:
            return RedirectResponse(url="/people/perf/feedback", status_code=303)

        context = base_context(request, auth, "Feedback Details", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "feedback": feedback,
                "success": success,
                "error": error,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/feedback_detail.html", context
        )

    def submit_feedback_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        feedback_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render submit feedback form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            feedback = svc.get_feedback(org_id, coerce_uuid(feedback_id))
        except Exception:
            return RedirectResponse(url="/people/perf/feedback", status_code=303)

        if feedback.submitted_on:
            return RedirectResponse(
                url=f"/people/perf/feedback/{feedback_id}?error=Feedback+already+submitted",
                status_code=303,
            )

        context = base_context(request, auth, "Submit Feedback", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "feedback": feedback,
                "form_data": {},
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/feedback_submit_form.html", context
        )

    async def submit_feedback_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        feedback_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle feedback submission."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.submit_feedback(
                org_id,
                coerce_uuid(feedback_id),
                overall_rating=parse_int(
                    _get_form_str(form_data, "overall_rating") or None
                ),
                strengths=_get_form_str(form_data, "strengths") or None,
                areas_for_improvement=_get_form_str(form_data, "areas_for_improvement")
                or None,
                general_comments=_get_form_str(form_data, "general_comments") or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/feedback/{feedback_id}?success=Feedback+submitted+successfully",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            feedback = svc.get_feedback(org_id, coerce_uuid(feedback_id))
            context = base_context(request, auth, "Submit Feedback", "perf", db=db)
            context["request"] = request
            context.update(
                {
                    "feedback": feedback,
                    "form_data": dict(form_data),
                    "error": str(e),
                    "errors": {},
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/feedback_submit_form.html", context
            )

    def delete_feedback_response(
        self,
        auth: WebAuthContext,
        db: Session,
        feedback_id: str,
    ) -> RedirectResponse:
        """Handle feedback deletion."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.delete_feedback(org_id, coerce_uuid(feedback_id))
            db.commit()
            return RedirectResponse(url="/people/perf/feedback", status_code=303)
        except Exception:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/feedback/{feedback_id}", status_code=303
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Goals/KPIs
    # ─────────────────────────────────────────────────────────────────────────

    def list_goals_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None = None,
        search: str | None = None,
        employee_id: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render KPIs list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = PerformanceService(db)

        result = svc.list_kpis(
            org_id,
            status=parse_kpi_status(status),
            search=search,
            employee_id=parse_uuid(employee_id),
            from_date=parse_date(start_date),
            to_date=parse_date(end_date),
            pagination=pagination,
        )

        context = base_context(request, auth, "Goals & KPIs", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "kpis": result.items,
                "status": status,
                "search": search,
                "employee_id": employee_id,
                "start_date": start_date,
                "end_date": end_date,
                "statuses": [s.value for s in KPIStatus],
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(request, "people/perf/kpis.html", context)

    def goal_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str | None = None,
    ) -> HTMLResponse:
        """Render new KPI form."""
        org_id = coerce_uuid(auth.organization_id)
        org_svc = OrganizationService(db, org_id)
        svc = PerformanceService(db)

        employees = org_svc.list_employees(
            EmployeeFilters(is_active=True),
            PaginationParams(limit=500),
        ).items

        kras = svc.list_kras(org_id, pagination=PaginationParams(limit=100)).items

        context = base_context(request, auth, "New KPI", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "kpi": None,
                "employees": employees,
                "kras": kras,
                "measurement_types": KPI_MEASUREMENT_TYPES,
                "form_data": {"employee_id": employee_id} if employee_id else {},
                "error": None,
            }
        )
        return templates.TemplateResponse(request, "people/perf/kpi_form.html", context)

    async def create_goal_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle KPI creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            employee_id = _get_form_str(form_data, "employee_id")
            if not employee_id:
                raise ValueError("Employee is required")
            kpi_name = _get_form_str(form_data, "title")
            if not kpi_name:
                raise ValueError("KPI title is required")
            period_start = parse_date(_get_form_str(form_data, "period_start") or None)
            period_end = parse_date(_get_form_str(form_data, "period_end") or None)
            if period_start is None:
                raise ValueError("Period start is required")
            if period_end is None:
                raise ValueError("Period end is required")
            target_value = parse_decimal(
                _get_form_str(form_data, "target_value") or None
            )
            if target_value is None:
                raise ValueError("Target value is required")
            weightage = parse_decimal(
                _get_form_str(form_data, "weightage") or None
            ) or Decimal("0")
            kpi = svc.create_kpi(
                org_id,
                employee_id=coerce_uuid(employee_id),
                kra_id=coerce_uuid(_get_form_str(form_data, "kra_id"))
                if _get_form_str(form_data, "kra_id")
                else None,
                kpi_name=kpi_name,
                description=_get_form_str(form_data, "description") or None,
                period_start=period_start,
                period_end=period_end,
                target_value=target_value,
                unit_of_measure=_get_form_str(form_data, "unit_of_measure") or None,
                threshold_value=parse_decimal(
                    _get_form_str(form_data, "threshold_value") or None
                ),
                stretch_value=parse_decimal(
                    _get_form_str(form_data, "stretch_value") or None
                ),
                weightage=weightage,
                notes=_get_form_str(form_data, "notes") or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/goals/{kpi.kpi_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            org_svc = OrganizationService(db, org_id)
            context = base_context(request, auth, "New KPI", "perf", db=db)
            context["request"] = request
            context.update(
                {
                    "kpi": None,
                    "employees": org_svc.list_employees(
                        EmployeeFilters(is_active=True), PaginationParams(limit=500)
                    ).items,
                    "kras": svc.list_kras(
                        org_id, pagination=PaginationParams(limit=100)
                    ).items,
                    "measurement_types": KPI_MEASUREMENT_TYPES,
                    "form_data": dict(form_data),
                    "error": str(e),
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/kpi_form.html", context
            )

    def goal_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        kpi_id: str,
        success: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render KPI detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            kpi = svc.get_kpi(org_id, coerce_uuid(kpi_id))
        except Exception:
            return RedirectResponse(url="/people/perf/goals", status_code=303)

        context = base_context(request, auth, kpi.kpi_name, "perf", db=db)
        context["request"] = request
        context.update(
            {
                "kpi": kpi,
                "success": success,
                "error": None,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/kpi_detail.html", context
        )

    def goal_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        kpi_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render KPI edit form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        try:
            kpi = svc.get_kpi(org_id, coerce_uuid(kpi_id))
        except Exception:
            return RedirectResponse(url="/people/perf/goals", status_code=303)

        context = base_context(request, auth, f"Edit {kpi.kpi_name}", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "kpi": kpi,
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "kras": svc.list_kras(
                    org_id, pagination=PaginationParams(limit=100)
                ).items,
                "measurement_types": KPI_MEASUREMENT_TYPES,
                "form_data": {},
                "error": None,
            }
        )
        return templates.TemplateResponse(request, "people/perf/kpi_form.html", context)

    async def update_goal_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        kpi_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle KPI update."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            kpi_name = _get_form_str(form_data, "title")
            period_start = parse_date(_get_form_str(form_data, "period_start") or None)
            period_end = parse_date(_get_form_str(form_data, "period_end") or None)
            svc.update_kpi(
                org_id,
                coerce_uuid(kpi_id),
                kra_id=coerce_uuid(_get_form_str(form_data, "kra_id"))
                if _get_form_str(form_data, "kra_id")
                else None,
                kpi_name=kpi_name or None,
                description=_get_form_str(form_data, "description") or None,
                period_start=period_start,
                period_end=period_end,
                target_value=parse_decimal(
                    _get_form_str(form_data, "target_value") or None
                ),
                unit_of_measure=_get_form_str(form_data, "unit_of_measure") or None,
                threshold_value=parse_decimal(
                    _get_form_str(form_data, "threshold_value") or None
                ),
                stretch_value=parse_decimal(
                    _get_form_str(form_data, "stretch_value") or None
                ),
                weightage=parse_decimal(_get_form_str(form_data, "weightage") or None),
                notes=_get_form_str(form_data, "notes") or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/goals/{kpi_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            org_svc = OrganizationService(db, org_id)
            kpi = svc.get_kpi(org_id, coerce_uuid(kpi_id))
            context = base_context(request, auth, f"Edit {kpi.kpi_name}", "perf", db=db)
            context["request"] = request
            context.update(
                {
                    "kpi": kpi,
                    "employees": org_svc.list_employees(
                        EmployeeFilters(is_active=True), PaginationParams(limit=500)
                    ).items,
                    "kras": svc.list_kras(
                        org_id, pagination=PaginationParams(limit=100)
                    ).items,
                    "measurement_types": KPI_MEASUREMENT_TYPES,
                    "form_data": {},
                    "error": str(e),
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/kpi_form.html", context
            )

    async def update_goal_progress_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        kpi_id: str,
    ) -> RedirectResponse:
        """Handle KPI progress update."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            actual_value = parse_decimal(
                _get_form_str(form_data, "actual_value") or None
            )
            if actual_value is None:
                raise ValueError("Actual value is required")
            svc.update_kpi_progress(
                org_id,
                coerce_uuid(kpi_id),
                actual_value=actual_value,
                notes=_get_form_str(form_data, "progress_notes") or None,
            )
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(url=f"/people/perf/goals/{kpi_id}", status_code=303)

    def delete_goal_response(
        self,
        auth: WebAuthContext,
        db: Session,
        kpi_id: str,
    ) -> RedirectResponse:
        """Handle KPI deletion."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.delete_kpi(org_id, coerce_uuid(kpi_id))
            db.commit()
            return RedirectResponse(url="/people/perf/goals", status_code=303)
        except Exception:
            db.rollback()
            return RedirectResponse(url=f"/people/perf/goals/{kpi_id}", status_code=303)

    # ─────────────────────────────────────────────────────────────────────────
    # Reports
    # ─────────────────────────────────────────────────────────────────────────

    def ratings_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str | None = None,
    ) -> HTMLResponse:
        """Render ratings distribution report."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        report = svc.get_ratings_distribution_report(
            org_id,
            cycle_id=parse_uuid(cycle_id),
        )

        cycles = svc.list_cycles(org_id, pagination=PaginationParams(limit=50)).items

        context = base_context(request, auth, "Ratings Distribution", "perf", db=db)
        context.update(
            {
                "report": report,
                "cycles": cycles,
                "cycle_id": cycle_id,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/reports/ratings.html", context
        )

    def by_department_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str | None = None,
    ) -> HTMLResponse:
        """Render by-department report."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        report = svc.get_performance_by_department_report(
            org_id,
            cycle_id=parse_uuid(cycle_id),
        )

        cycles = svc.list_cycles(org_id, pagination=PaginationParams(limit=50)).items

        context = base_context(
            request, auth, "Performance by Department", "perf", db=db
        )
        context.update(
            {
                "report": report,
                "cycles": cycles,
                "cycle_id": cycle_id,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/reports/by_department.html", context
        )

    def kpi_achievement_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: str | None = None,
        end_date: str | None = None,
        department_id: str | None = None,
    ) -> HTMLResponse:
        """Render KPI achievement report."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        report = svc.get_kpi_achievement_report(
            org_id,
            start_date=parse_date(start_date),
            end_date=parse_date(end_date),
            department_id=parse_uuid(department_id),
        )

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=100),
        ).items

        context = base_context(request, auth, "KPI Achievement", "perf", db=db)
        context.update(
            {
                "report": report,
                "departments": departments,
                "start_date": start_date or "",
                "end_date": end_date or "",
                "department_id": department_id,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/reports/kpi_achievement.html", context
        )

    def trends_report_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        department_id: str | None = None,
    ) -> HTMLResponse:
        """Render performance trends report."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        report = svc.get_performance_trends_report(
            org_id,
            department_id=parse_uuid(department_id),
        )

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=100),
        ).items

        context = base_context(request, auth, "Performance Trends", "perf", db=db)
        context.update(
            {
                "report": report,
                "departments": departments,
                "department_id": department_id,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/reports/trends.html", context
        )
