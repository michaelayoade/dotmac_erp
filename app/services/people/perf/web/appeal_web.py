"""
Appeal Web Service — OHCSF Performance Management System.

Handles list, detail, new form, and create responses for
AppraisalAppeal records.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.perf.pms_enums import AppealDecision, AppealStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.perf.appeal_service import (
    AppealNotFoundError,
    AppealValidationError,
    AppraisalAppealService,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import parse_date, parse_uuid

logger = logging.getLogger(__name__)


class AppealWebService:
    """Web service for appraisal appeal list, detail, and create views."""

    @staticmethod
    def _form_text(value: object | None, default: str = "") -> str:
        if isinstance(value, str):
            return value.strip()
        return default

    def list_appeals_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render appeals list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=25)
        svc = AppraisalAppealService(db)

        appeal_status: AppealStatus | None = None
        if status:
            try:
                appeal_status = AppealStatus(status)
            except ValueError:
                appeal_status = None

        result = svc.list_appeals(
            org_id,
            status=appeal_status,
            search=self._form_text(search) if search else None,
            pagination=pagination,
        )

        context = base_context(request, auth, "Appraisal Appeals", "pms-appeals", db=db)
        context["request"] = request
        context.update(
            {
                "appeals": result.items,
                "status": status,
                "search": search,
                "statuses": [s.value for s in AppealStatus],
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/appeals.html", context
        )

    def appeal_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appeal_id: str,
    ) -> HTMLResponse:
        """Render appeal detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = AppraisalAppealService(db)

        appeal_uuid = parse_uuid(appeal_id)
        if appeal_uuid is None:
            context = base_context(request, auth, "Appeal Not Found", "pms-appeals", db=db)
            context["request"] = request
            context.update({"appeal": None, "error": "Invalid appeal ID"})
            return templates.TemplateResponse(
                request, "people/perf/pms/appeal_detail.html", context, status_code=404
            )

        try:
            appeal = svc.get_appeal(org_id, appeal_uuid)
        except Exception as e:
            context = base_context(request, auth, "Appeal Not Found", "pms-appeals", db=db)
            context["request"] = request
            context.update({"appeal": None, "error": str(e)})
            return templates.TemplateResponse(
                request, "people/perf/pms/appeal_detail.html", context, status_code=404
            )

        context = base_context(
            request, auth, f"Appeal — {appeal.appeal_id}", "pms-appeals", db=db
        )
        context["request"] = request
        success = request.query_params.get("saved")
        context.update(
            {
                "appeal": appeal,
                "success": success,
                "AppealStatus": AppealStatus,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/appeal_detail.html", context
        )

    def appeal_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new appeal form."""
        org_id = coerce_uuid(auth.organization_id)

        # Load appraisals that can have an appeal filed
        from sqlalchemy import select

        from app.models.people.perf import AppraisalStatus
        from app.models.people.perf.appraisal import Appraisal

        stmt = (
            select(Appraisal)
            .where(
                Appraisal.organization_id == org_id,
                Appraisal.status == AppraisalStatus.COMPLETED,
            )
            .order_by(Appraisal.created_at.desc())
            .limit(200)
        )
        appraisals = list(db.scalars(stmt).all())

        from app.services.people.hr import EmployeeFilters
        from app.services.people.hr.employees import EmployeeService

        emp_svc = EmployeeService(db, org_id)
        employees = emp_svc.list_employees(
            EmployeeFilters(is_active=True), PaginationParams(limit=500)
        ).items

        context = base_context(request, auth, "File an Appeal", "pms-appeals", db=db)
        context["request"] = request
        context.update(
            {
                "appeal": None,
                "form_data": {},
                "error": None,
                "appraisals": appraisals,
                "employees": employees,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/appeal_form.html", context
        )

    async def create_appeal_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle appeal creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = AppraisalAppealService(db)

        try:
            filed_date = parse_date(self._form_text(form_data.get("filed_date")))
            if not filed_date:
                raise ValueError("Filed date is required")

            appraisal_id = parse_uuid(self._form_text(form_data.get("appraisal_id")))
            employee_id = parse_uuid(self._form_text(form_data.get("employee_id")))
            if not appraisal_id or not employee_id:
                raise ValueError("Appraisal and employee are required")

            appeal = svc.file_appeal(
                org_id,
                appraisal_id=appraisal_id,
                employee_id=employee_id,
                filed_date=filed_date,
                reason=self._form_text(form_data.get("reason")),
                requested_outcome=self._form_text(form_data.get("requested_outcome"))
                or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal.appeal_id}?saved=1",
                status_code=303,
            )
        except Exception as e:
            db.rollback()

            from sqlalchemy import select

            from app.models.people.perf import AppraisalStatus
            from app.models.people.perf.appraisal import Appraisal

            stmt = (
                select(Appraisal)
                .where(
                    Appraisal.organization_id == org_id,
                    Appraisal.status == AppraisalStatus.COMPLETED,
                )
                .order_by(Appraisal.created_at.desc())
                .limit(200)
            )
            appraisals = list(db.scalars(stmt).all())

            from app.services.people.hr import EmployeeFilters
            from app.services.people.hr.employees import EmployeeService

            emp_svc = EmployeeService(db, org_id)
            employees = emp_svc.list_employees(
                EmployeeFilters(), PaginationParams(limit=500)
            ).items

            context = base_context(request, auth, "File an Appeal", "pms-appeals", db=db)
            context["request"] = request
            context.update(
                {
                    "appeal": None,
                    "form_data": dict(form_data),
                    "error": str(e),
                    "appraisals": appraisals,
                    "employees": employees,
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/pms/appeal_form.html", context
            )

    async def assign_mediator_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appeal_id: str,
    ) -> RedirectResponse:
        """Assign a mediator to an appeal."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        try:
            mediator_id_str = str(form_data.get("mediator_id", "")).strip()
            if not mediator_id_str:
                raise AppealValidationError("Mediator is required")
            svc = AppraisalAppealService(db)
            svc.assign_mediator(
                org_id,
                coerce_uuid(appeal_id),
                mediator_id=coerce_uuid(mediator_id_str),
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?saved=1", status_code=303
            )
        except (AppealNotFoundError, AppealValidationError) as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?error={e}", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to assign mediator for appeal %s", appeal_id)
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?error=An+unexpected+error+occurred",
                status_code=303,
            )

    async def record_mediation_outcome_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appeal_id: str,
    ) -> RedirectResponse:
        """Record the outcome of mediation for an appeal."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        try:
            outcome = str(form_data.get("outcome", "")).strip()
            if not outcome:
                raise AppealValidationError("Outcome is required")
            resolved_str = str(form_data.get("resolved", "")).strip().lower()
            resolved = resolved_str in ("true", "1", "yes")
            svc = AppraisalAppealService(db)
            svc.record_mediation_outcome(
                org_id,
                coerce_uuid(appeal_id),
                outcome=outcome,
                resolved=resolved,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?saved=1", status_code=303
            )
        except (AppealNotFoundError, AppealValidationError) as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?error={e}", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to record mediation outcome for appeal %s", appeal_id
            )
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?error=An+unexpected+error+occurred",
                status_code=303,
            )

    async def record_committee_decision_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appeal_id: str,
    ) -> RedirectResponse:
        """Record an appeal committee decision."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        try:
            decision_str = str(form_data.get("decision", "")).strip()
            if not decision_str:
                raise AppealValidationError("Decision is required")
            try:
                decision = AppealDecision(decision_str)
            except ValueError as exc:
                raise AppealValidationError(
                    f"Invalid decision value: {decision_str}"
                ) from exc
            notes = str(form_data.get("notes", "")).strip()
            if not notes:
                raise AppealValidationError("Notes are required")
            adjusted_rating_str = str(form_data.get("adjusted_rating", "")).strip()
            adjusted_rating = int(adjusted_rating_str) if adjusted_rating_str else None
            svc = AppraisalAppealService(db)
            svc.record_committee_decision(
                org_id,
                coerce_uuid(appeal_id),
                decision=decision,
                notes=notes,
                adjusted_rating=adjusted_rating,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?saved=1", status_code=303
            )
        except (AppealNotFoundError, AppealValidationError) as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?error={e}", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to record committee decision for appeal %s", appeal_id
            )
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?error=An+unexpected+error+occurred",
                status_code=303,
            )

    async def communicate_decision_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        appeal_id: str,
    ) -> RedirectResponse:
        """Communicate the final decision on an appeal to the appellant."""
        org_id = coerce_uuid(auth.organization_id)
        try:
            svc = AppraisalAppealService(db)
            svc.communicate_decision(org_id, coerce_uuid(appeal_id))
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?saved=1", status_code=303
            )
        except (AppealNotFoundError, AppealValidationError) as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?error={e}", status_code=303
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to communicate decision for appeal %s", appeal_id)
            return RedirectResponse(
                url=f"/people/perf/pms/appeals/{appeal_id}?error=An+unexpected+error+occurred",
                status_code=303,
            )
