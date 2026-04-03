"""
Strategic Objective Web Service — OHCSF Performance Management System.

Handles list, detail, new form, and create responses for
StrategicObjective records.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.common import PaginationParams, coerce_uuid
from app.services.people.perf.strategic_objective_service import (
    StrategicObjectiveService,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import parse_decimal, parse_uuid

logger = logging.getLogger(__name__)


class StrategicObjectiveWebService:
    """Web service for strategic objectives list, detail, and create views."""

    @staticmethod
    def _form_text(value: object | None, default: str = "") -> str:
        if isinstance(value, str):
            return value.strip()
        return default

    def list_objectives_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render strategic objectives list/cascade view."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=50)
        svc = StrategicObjectiveService(db)

        cycle_uuid = parse_uuid(cycle_id)

        result = svc.list_objectives(
            org_id,
            cycle_id=cycle_uuid,
            search=self._form_text(search) if search else None,
            pagination=pagination,
        )

        # Load cycles for filter
        from sqlalchemy import select

        from app.models.people.perf.appraisal_cycle import AppraisalCycle

        cycles = list(
            db.scalars(
                select(AppraisalCycle)
                .where(AppraisalCycle.organization_id == org_id)
                .order_by(AppraisalCycle.start_date.desc())
                .limit(50)
            ).all()
        )

        # Build parent-child hierarchy for tree view
        all_objectives = result.items
        root_objectives = [o for o in all_objectives if o.parent_objective_id is None]
        child_map: dict = {}
        for obj in all_objectives:
            if obj.parent_objective_id is not None:
                key = str(obj.parent_objective_id)
                child_map.setdefault(key, []).append(obj)

        context = base_context(request, auth, "Strategic Objectives", "pms-objectives", db=db)
        context["request"] = request
        context.update(
            {
                "objectives": all_objectives,
                "root_objectives": root_objectives,
                "child_map": child_map,
                "cycle_id": cycle_id,
                "search": search,
                "cycles": cycles,
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/objectives.html", context
        )

    def objective_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new strategic objective form."""
        org_id = coerce_uuid(auth.organization_id)

        from sqlalchemy import select

        from app.models.people.perf.appraisal_cycle import AppraisalCycle
        from app.models.people.perf.strategic_objective import StrategicObjective

        cycles = list(
            db.scalars(
                select(AppraisalCycle)
                .where(AppraisalCycle.organization_id == org_id)
                .order_by(AppraisalCycle.start_date.desc())
                .limit(50)
            ).all()
        )

        parent_objectives = list(
            db.scalars(
                select(StrategicObjective)
                .where(StrategicObjective.organization_id == org_id)
                .order_by(StrategicObjective.objective_code)
                .limit(200)
            ).all()
        )

        from app.services.people.hr import DepartmentFilters, OrganizationService

        org_svc = OrganizationService(db, org_id)
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=200),
        ).items

        context = base_context(request, auth, "New Strategic Objective", "pms-objectives", db=db)
        context["request"] = request
        context.update(
            {
                "objective": None,
                "form_data": {},
                "error": None,
                "cycles": cycles,
                "parent_objectives": parent_objectives,
                "departments": departments,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/objective_form.html", context
        )

    async def create_objective_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle strategic objective creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = StrategicObjectiveService(db)

        try:
            cycle_id = parse_uuid(self._form_text(form_data.get("cycle_id")))
            if not cycle_id:
                raise ValueError("Appraisal cycle is required")

            objective_code = self._form_text(form_data.get("objective_code"))
            description = self._form_text(form_data.get("description"))
            if not objective_code or not description:
                raise ValueError("Objective code and description are required")

            svc.create_objective(
                org_id,
                cycle_id=cycle_id,
                objective_code=objective_code,
                description=description,
                department_id=parse_uuid(
                    self._form_text(form_data.get("department_id"))
                ),
                parent_objective_id=parse_uuid(
                    self._form_text(form_data.get("parent_objective_id"))
                ),
                source_document=self._form_text(form_data.get("source_document"))
                or None,
                target_description=self._form_text(form_data.get("target_description"))
                or None,
                weight=parse_decimal(self._form_text(form_data.get("weight"))),
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/objectives?cycle_id={cycle_id}&saved=1",
                status_code=303,
            )
        except Exception as e:
            db.rollback()

            from sqlalchemy import select

            from app.models.people.perf.appraisal_cycle import AppraisalCycle
            from app.models.people.perf.strategic_objective import StrategicObjective

            cycles = list(
                db.scalars(
                    select(AppraisalCycle)
                    .where(AppraisalCycle.organization_id == org_id)
                    .order_by(AppraisalCycle.start_date.desc())
                    .limit(50)
                ).all()
            )
            parent_objectives = list(
                db.scalars(
                    select(StrategicObjective)
                    .where(StrategicObjective.organization_id == org_id)
                    .order_by(StrategicObjective.objective_code)
                    .limit(200)
                ).all()
            )

            from app.services.people.hr import DepartmentFilters, OrganizationService

            org_svc = OrganizationService(db, org_id)
            departments = org_svc.list_departments(
                DepartmentFilters(is_active=True),
                PaginationParams(limit=200),
            ).items

            context = base_context(
                request, auth, "New Strategic Objective", "pms-objectives", db=db
            )
            context["request"] = request
            context.update(
                {
                    "objective": None,
                    "form_data": dict(form_data),
                    "error": str(e),
                    "cycles": cycles,
                    "parent_objectives": parent_objectives,
                    "departments": departments,
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/pms/objective_form.html", context
            )
