"""
PIP Web Service — OHCSF Performance Management System.

Handles list, detail, new form, and create responses for
PerformanceImprovementPlan records.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.perf.pms_enums import PIPCauseCategory, PIPOutcome, PIPStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.perf.pip_service import (
    PIPNotFoundError,
    PIPService,
    PIPStatusError,
    PIPValidationError,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import parse_date, parse_uuid

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class PIPWebService:
    """Web service for PIP list, detail, and create views."""

    @staticmethod
    def _form_text(value: object | None, default: str = "") -> str:
        if isinstance(value, str):
            return value.strip()
        return default

    def list_pips_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render PIPs list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=25)
        svc = PIPService(db)

        pip_status: PIPStatus | None = None
        if status:
            try:
                pip_status = PIPStatus(status)
            except ValueError:
                pip_status = None

        result = svc.list_pips(
            org_id,
            status=pip_status,
            search=self._form_text(search) if search else None,
            pagination=pagination,
        )

        context = base_context(
            request, auth, "Performance Improvement Plans", "pms-pips", db=db
        )
        context["request"] = request
        context.update(
            {
                "pips": result.items,
                "status": status,
                "search": search,
                "statuses": [s.value for s in PIPStatus],
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(request, "people/perf/pms/pips.html", context)

    def pip_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        pip_id: str,
    ) -> HTMLResponse:
        """Render PIP detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PIPService(db)

        pip_uuid = parse_uuid(pip_id)
        if pip_uuid is None:
            context = base_context(request, auth, "PIP Not Found", "pms-pips", db=db)
            context["request"] = request
            context.update({"pip": None, "error": "Invalid PIP ID"})
            return templates.TemplateResponse(
                request, "people/perf/pms/pip_detail.html", context, status_code=404
            )

        try:
            pip = svc.get_pip(org_id, pip_uuid)
        except Exception as e:
            context = base_context(request, auth, "PIP Not Found", "pms-pips", db=db)
            context["request"] = request
            context.update({"pip": None, "error": str(e)})
            return templates.TemplateResponse(
                request, "people/perf/pms/pip_detail.html", context, status_code=404
            )

        context = base_context(request, auth, f"PIP {pip.pip_code}", "pms-pips", db=db)
        context["request"] = request
        success = request.query_params.get("saved")
        context.update(
            {
                "pip": pip,
                "success": success,
                "improvement_areas": pip.improvement_areas or [],
                "review_intervals": pip.review_intervals or [],
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/pip_detail.html", context
        )

    def pip_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new PIP form."""
        org_id = coerce_uuid(auth.organization_id)

        # Load employees for dropdowns
        from app.services.people.hr import EmployeeFilters
        from app.services.people.hr.employees import EmployeeService

        emp_svc = EmployeeService(db, org_id)
        employees = emp_svc.list_employees(
            EmployeeFilters(is_active=True), PaginationParams(limit=500)
        ).items

        context = base_context(
            request, auth, "New Performance Improvement Plan", "pms-pips", db=db
        )
        context["request"] = request
        context.update(
            {
                "pip": None,
                "form_data": {},
                "error": None,
                "employees": employees,
                "cause_categories": [c.value for c in PIPCauseCategory],
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/pip_form.html", context
        )

    async def create_pip_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle PIP creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PIPService(db)

        try:
            start_date = parse_date(self._form_text(form_data.get("start_date")))
            end_date = parse_date(self._form_text(form_data.get("end_date")))
            if not start_date or not end_date:
                raise ValueError("Start and end dates are required")

            employee_id = parse_uuid(self._form_text(form_data.get("employee_id")))
            supervisor_id = parse_uuid(self._form_text(form_data.get("supervisor_id")))
            hr_officer_id = parse_uuid(self._form_text(form_data.get("hr_officer_id")))
            if not employee_id or not supervisor_id or not hr_officer_id:
                raise ValueError("Employee, supervisor, and HR officer are required")

            # Parse improvement areas from form (simple text areas)
            areas_text = self._form_text(form_data.get("improvement_areas"))
            improvement_areas = (
                [
                    {"area": line.strip(), "target": ""}
                    for line in areas_text.splitlines()
                    if line.strip()
                ]
                if areas_text
                else []
            )

            pip = svc.create_pip(
                org_id,
                employee_id=employee_id,
                supervisor_id=supervisor_id,
                hr_officer_id=hr_officer_id,
                pip_code=self._form_text(form_data.get("pip_code")),
                start_date=start_date,
                end_date=end_date,
                reason=self._form_text(form_data.get("reason")),
                cause_category=self._form_text(form_data.get("cause_category")),
                improvement_areas=improvement_areas,
                support_measures=self._form_text(form_data.get("support_measures"))
                or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip.pip_id}?saved=1",
                status_code=303,
            )
        except Exception as e:
            db.rollback()

            from app.services.people.hr import EmployeeFilters
            from app.services.people.hr.employees import EmployeeService

            emp_svc = EmployeeService(db, org_id)
            employees = emp_svc.list_employees(
                EmployeeFilters(is_active=True), PaginationParams(limit=500)
            ).items

            context = base_context(
                request, auth, "New Performance Improvement Plan", "perf", db=db
            )
            context["request"] = request
            context.update(
                {
                    "pip": None,
                    "form_data": dict(form_data),
                    "error": str(e),
                    "employees": employees,
                    "cause_categories": [c.value for c in PIPCauseCategory],
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/pms/pip_form.html", context
            )

    async def activate_pip_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        pip_id: str,
    ) -> RedirectResponse:
        """Activate a PIP."""
        org_id = coerce_uuid(auth.organization_id)
        try:
            svc = PIPService(db)
            svc.activate_pip(org_id, coerce_uuid(pip_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?saved=1", status_code=303
            )
        except (PIPStatusError, PIPNotFoundError, PIPValidationError) as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?error={e}", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to activate PIP %s", pip_id)
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?error=An+unexpected+error+occurred",
                status_code=303,
            )

    async def extend_pip_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        pip_id: str,
    ) -> RedirectResponse:
        """Extend a PIP end date."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        try:
            new_end_str = str(form_data.get("new_end_date", "")).strip()
            new_end = parse_date(new_end_str)
            if not new_end:
                raise PIPValidationError("New end date is required")
            reason = str(form_data.get("reason", "")).strip()
            if not reason:
                raise PIPValidationError("Reason is required")
            svc = PIPService(db)
            svc.grant_extension(
                org_id,
                coerce_uuid(pip_id),
                new_end_date=new_end,
                reason=reason,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?saved=1", status_code=303
            )
        except (PIPStatusError, PIPNotFoundError, PIPValidationError) as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?error={e}", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to extend PIP %s", pip_id)
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?error=An+unexpected+error+occurred",
                status_code=303,
            )

    async def record_review_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        pip_id: str,
    ) -> RedirectResponse:
        """Record a PIP interval review."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        try:
            review_date_str = str(form_data.get("review_date", "")).strip()
            review_date = parse_date(review_date_str)
            if not review_date:
                raise PIPValidationError("Review date is required")
            notes = str(form_data.get("notes", "")).strip()
            if not notes:
                raise PIPValidationError("Notes are required")
            progress_status = str(form_data.get("progress_status", "")).strip()
            if not progress_status:
                raise PIPValidationError("Progress status is required")
            svc = PIPService(db)
            svc.record_review(
                org_id,
                coerce_uuid(pip_id),
                review_date=review_date,
                notes=notes,
                progress_status=progress_status,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?saved=1", status_code=303
            )
        except (PIPStatusError, PIPNotFoundError, PIPValidationError) as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?error={e}", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to record review for PIP %s", pip_id)
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?error=An+unexpected+error+occurred",
                status_code=303,
            )

    async def complete_pip_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        pip_id: str,
    ) -> RedirectResponse:
        """Complete a PIP with an outcome."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        try:
            outcome_str = str(form_data.get("outcome", "")).strip()
            if not outcome_str:
                raise PIPValidationError("Outcome is required")
            try:
                outcome = PIPOutcome(outcome_str)
            except ValueError as exc:
                raise PIPValidationError(
                    f"Invalid outcome value: {outcome_str}"
                ) from exc
            notes = str(form_data.get("notes", "")).strip()
            if not notes:
                raise PIPValidationError("Notes are required")
            svc = PIPService(db)
            svc.complete_pip(
                org_id,
                coerce_uuid(pip_id),
                outcome=outcome,
                notes=notes,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?saved=1", status_code=303
            )
        except (PIPStatusError, PIPNotFoundError, PIPValidationError) as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?error={e}", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to complete PIP %s", pip_id)
            return RedirectResponse(
                url=f"/people/perf/pms/pips/{pip_id}?error=An+unexpected+error+occurred",
                status_code=303,
            )
