"""
Performance Web Service - Cycle, KRA, Template, and Scorecard methods.
"""

from __future__ import annotations

import logging
from typing import Optional
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.perf.appraisal_cycle import AppraisalCycleStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import EmployeeFilters, OrganizationService, DepartmentFilters
from app.services.people.perf import PerformanceService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import (
    parse_uuid,
    parse_date,
    parse_int,
    parse_decimal,
    parse_cycle_status,
)

logger = logging.getLogger(__name__)


class CycleWebService:
    """Web service methods for appraisal cycles, KRAs, templates, and scorecards."""

    # ─────────────────────────────────────────────────────────────────────────
    # Cycles
    # ─────────────────────────────────────────────────────────────────────────

    def list_cycles_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str] = None,
        year: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render cycles list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = PerformanceService(db)

        result = svc.list_cycles(
            org_id,
            status=parse_cycle_status(status),
            year=parse_int(year),
            pagination=pagination,
        )

        context = base_context(request, auth, "Appraisal Cycles", "perf", db=db)
        context["request"] = request
        context.update({
            "cycles": result.items,
            "status": status,
            "year": year,
            "statuses": [s.value for s in AppraisalCycleStatus],
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        })
        return templates.TemplateResponse(request, "people/perf/cycles.html", context)

    def cycle_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new cycle form."""
        context = base_context(request, auth, "New Appraisal Cycle", "perf", db=db)
        context["request"] = request
        context.update({
            "cycle": None,
            "form_data": {},
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/cycle_form.html", context)

    async def create_cycle_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle cycle creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            cycle = svc.create_cycle(
                org_id,
                cycle_name=form_data.get("cycle_name", ""),
                cycle_type=form_data.get("cycle_type", "ANNUAL"),
                year=parse_int(form_data.get("year")),
                start_date=parse_date(form_data.get("start_date")),
                end_date=parse_date(form_data.get("end_date")),
                self_assessment_start=parse_date(form_data.get("self_assessment_start")),
                self_assessment_end=parse_date(form_data.get("self_assessment_end")),
                manager_review_start=parse_date(form_data.get("manager_review_start")),
                manager_review_end=parse_date(form_data.get("manager_review_end")),
                calibration_start=parse_date(form_data.get("calibration_start")),
                calibration_end=parse_date(form_data.get("calibration_end")),
                feedback_start=parse_date(form_data.get("feedback_start")),
                feedback_end=parse_date(form_data.get("feedback_end")),
                description=form_data.get("description") or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/cycles/{cycle.cycle_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            context = base_context(request, auth, "New Appraisal Cycle", "perf", db=db)
            context["request"] = request
            context.update({
                "cycle": None,
                "form_data": dict(form_data),
                "error": str(e),
            })
            return templates.TemplateResponse(request, "people/perf/cycle_form.html", context)

    def cycle_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str,
        success: Optional[str] = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render cycle detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            cycle = svc.get_cycle(org_id, coerce_uuid(cycle_id))
        except Exception:
            return RedirectResponse(url="/people/perf/cycles", status_code=303)

        appraisals = svc.list_appraisals(
            org_id,
            cycle_id=coerce_uuid(cycle_id),
            pagination=PaginationParams(limit=20),
        )

        context = base_context(request, auth, cycle.cycle_name, "perf", db=db)
        context["request"] = request
        context.update({
            "cycle": cycle,
            "appraisals": appraisals.items,
            "appraisals_total": appraisals.total,
            "success": success,
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/cycle_detail.html", context)

    def cycle_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render cycle edit form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            cycle = svc.get_cycle(org_id, coerce_uuid(cycle_id))
        except Exception:
            return RedirectResponse(url="/people/perf/cycles", status_code=303)

        context = base_context(request, auth, f"Edit {cycle.cycle_name}", "perf", db=db)
        context["request"] = request
        context.update({
            "cycle": cycle,
            "form_data": {},
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/cycle_form.html", context)

    async def update_cycle_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle cycle update."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.update_cycle(
                org_id,
                coerce_uuid(cycle_id),
                cycle_name=form_data.get("cycle_name", ""),
                cycle_type=form_data.get("cycle_type", "ANNUAL"),
                year=parse_int(form_data.get("year")),
                start_date=parse_date(form_data.get("start_date")),
                end_date=parse_date(form_data.get("end_date")),
                self_assessment_start=parse_date(form_data.get("self_assessment_start")),
                self_assessment_end=parse_date(form_data.get("self_assessment_end")),
                manager_review_start=parse_date(form_data.get("manager_review_start")),
                manager_review_end=parse_date(form_data.get("manager_review_end")),
                calibration_start=parse_date(form_data.get("calibration_start")),
                calibration_end=parse_date(form_data.get("calibration_end")),
                feedback_start=parse_date(form_data.get("feedback_start")),
                feedback_end=parse_date(form_data.get("feedback_end")),
                description=form_data.get("description") or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/cycles/{cycle_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            cycle = svc.get_cycle(org_id, coerce_uuid(cycle_id))
            context = base_context(request, auth, f"Edit {cycle.cycle_name}", "perf", db=db)
            context["request"] = request
            context.update({
                "cycle": cycle,
                "form_data": {},
                "error": str(e),
            })
            return templates.TemplateResponse(request, "people/perf/cycle_form.html", context)

    def activate_cycle_response(
        self,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str,
    ) -> RedirectResponse:
        """Handle cycle activation."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.activate_cycle(org_id, coerce_uuid(cycle_id))
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(url=f"/people/perf/cycles/{cycle_id}", status_code=303)

    def advance_cycle_response(
        self,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str,
    ) -> RedirectResponse:
        """Handle cycle phase advancement."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.advance_cycle_phase(org_id, coerce_uuid(cycle_id))
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(url=f"/people/perf/cycles/{cycle_id}", status_code=303)

    def cancel_cycle_response(
        self,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str,
    ) -> RedirectResponse:
        """Handle cycle cancellation."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.cancel_cycle(org_id, coerce_uuid(cycle_id))
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(url=f"/people/perf/cycles/{cycle_id}", status_code=303)

    def delete_cycle_response(
        self,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str,
    ) -> RedirectResponse:
        """Handle cycle deletion."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.delete_cycle(org_id, coerce_uuid(cycle_id))
            db.commit()
            return RedirectResponse(url="/people/perf/cycles", status_code=303)
        except Exception:
            db.rollback()
            return RedirectResponse(url=f"/people/perf/cycles/{cycle_id}", status_code=303)

    # ─────────────────────────────────────────────────────────────────────────
    # KRAs
    # ─────────────────────────────────────────────────────────────────────────

    def list_kras_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        is_active: Optional[str] = None,
        department_id: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render KRAs list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        active_filter = None
        if is_active == "true":
            active_filter = True
        elif is_active == "false":
            active_filter = False

        result = svc.list_kras(
            org_id,
            search=search,
            is_active=active_filter,
            department_id=parse_uuid(department_id),
            pagination=pagination,
        )

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=100),
        ).items

        context = base_context(request, auth, "Key Result Areas", "perf", db=db)
        context["request"] = request
        context.update({
            "kras": result.items,
            "search": search,
            "is_active": is_active,
            "department_id": department_id,
            "departments": departments,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        })
        return templates.TemplateResponse(request, "people/perf/kras.html", context)

    def kra_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new KRA form."""
        org_id = coerce_uuid(auth.organization_id)
        org_svc = OrganizationService(db, org_id)

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=100),
        ).items

        context = base_context(request, auth, "New KRA", "perf", db=db)
        context["request"] = request
        context.update({
            "kra": None,
            "departments": departments,
            "form_data": {},
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/kra_form.html", context)

    async def create_kra_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle KRA creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            kra = svc.create_kra(
                org_id,
                kra_name=form_data.get("kra_name", ""),
                description=form_data.get("description") or None,
                department_id=coerce_uuid(form_data["department_id"]) if form_data.get("department_id") else None,
                weight=parse_decimal(form_data.get("weight")),
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/kras/{kra.kra_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            org_svc = OrganizationService(db, org_id)
            context = base_context(request, auth, "New KRA", "perf", db=db)
            context["request"] = request
            context.update({
                "kra": None,
                "departments": org_svc.list_departments(DepartmentFilters(is_active=True), PaginationParams(limit=100)).items,
                "form_data": dict(form_data),
                "error": str(e),
            })
            return templates.TemplateResponse(request, "people/perf/kra_form.html", context)

    def kra_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        kra_id: str,
        success: Optional[str] = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render KRA detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            kra = svc.get_kra(org_id, coerce_uuid(kra_id))
        except Exception:
            return RedirectResponse(url="/people/perf/kras", status_code=303)

        context = base_context(request, auth, kra.kra_name, "perf", db=db)
        context["request"] = request
        context.update({
            "kra": kra,
            "success": success,
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/kra_detail.html", context)

    def kra_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        kra_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render KRA edit form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        try:
            kra = svc.get_kra(org_id, coerce_uuid(kra_id))
        except Exception:
            return RedirectResponse(url="/people/perf/kras", status_code=303)

        context = base_context(request, auth, f"Edit {kra.kra_name}", "perf", db=db)
        context["request"] = request
        context.update({
            "kra": kra,
            "departments": org_svc.list_departments(DepartmentFilters(is_active=True), PaginationParams(limit=100)).items,
            "form_data": {},
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/kra_form.html", context)

    async def update_kra_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        kra_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle KRA update."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.update_kra(
                org_id,
                coerce_uuid(kra_id),
                kra_name=form_data.get("kra_name", ""),
                description=form_data.get("description") or None,
                department_id=coerce_uuid(form_data["department_id"]) if form_data.get("department_id") else None,
                weight=parse_decimal(form_data.get("weight")),
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/kras/{kra_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            org_svc = OrganizationService(db, org_id)
            kra = svc.get_kra(org_id, coerce_uuid(kra_id))
            context = base_context(request, auth, f"Edit {kra.kra_name}", "perf", db=db)
            context["request"] = request
            context.update({
                "kra": kra,
                "departments": org_svc.list_departments(DepartmentFilters(is_active=True), PaginationParams(limit=100)).items,
                "form_data": {},
                "error": str(e),
            })
            return templates.TemplateResponse(request, "people/perf/kra_form.html", context)

    def toggle_kra_active_response(
        self,
        auth: WebAuthContext,
        db: Session,
        kra_id: str,
    ) -> RedirectResponse:
        """Handle KRA active toggle."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.toggle_kra_active(org_id, coerce_uuid(kra_id))
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(url=f"/people/perf/kras/{kra_id}", status_code=303)

    def delete_kra_response(
        self,
        auth: WebAuthContext,
        db: Session,
        kra_id: str,
    ) -> RedirectResponse:
        """Handle KRA deletion."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.delete_kra(org_id, coerce_uuid(kra_id))
            db.commit()
            return RedirectResponse(url="/people/perf/kras", status_code=303)
        except Exception:
            db.rollback()
            return RedirectResponse(url=f"/people/perf/kras/{kra_id}", status_code=303)

    # ─────────────────────────────────────────────────────────────────────────
    # Templates
    # ─────────────────────────────────────────────────────────────────────────

    def list_templates_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str] = None,
        is_active: Optional[str] = None,
        department_id: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render templates list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        active_filter = None
        if is_active == "true":
            active_filter = True
        elif is_active == "false":
            active_filter = False

        result = svc.list_templates(
            org_id,
            search=search,
            is_active=active_filter,
            department_id=parse_uuid(department_id),
            pagination=pagination,
        )

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=100),
        ).items

        context = base_context(request, auth, "Appraisal Templates", "perf", db=db)
        context["request"] = request
        context.update({
            "templates": result.items,
            "search": search,
            "is_active": is_active,
            "department_id": department_id,
            "departments": departments,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        })
        return templates.TemplateResponse(request, "people/perf/templates.html", context)

    def template_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new template form."""
        org_id = coerce_uuid(auth.organization_id)
        org_svc = OrganizationService(db, org_id)
        svc = PerformanceService(db)

        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True),
            PaginationParams(limit=100),
        ).items

        kras = svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=100)).items

        context = base_context(request, auth, "New Template", "perf", db=db)
        context["request"] = request
        context.update({
            "template": None,
            "departments": departments,
            "kras": kras,
            "form_data": {},
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/template_form.html", context)

    async def create_template_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle template creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            template = svc.create_template(
                org_id,
                template_name=form_data.get("template_name", ""),
                description=form_data.get("description") or None,
                department_id=coerce_uuid(form_data["department_id"]) if form_data.get("department_id") else None,
                rating_scale_max=parse_int(form_data.get("rating_scale_max")) or 5,
                self_assessment_enabled=form_data.get("self_assessment_enabled") == "true",
                manager_review_enabled=form_data.get("manager_review_enabled") == "true",
                peer_feedback_enabled=form_data.get("peer_feedback_enabled") == "true",
                calibration_enabled=form_data.get("calibration_enabled") == "true",
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/templates/{template.template_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            org_svc = OrganizationService(db, org_id)
            context = base_context(request, auth, "New Template", "perf", db=db)
            context["request"] = request
            context.update({
                "template": None,
                "departments": org_svc.list_departments(DepartmentFilters(is_active=True), PaginationParams(limit=100)).items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=100)).items,
                "form_data": dict(form_data),
                "error": str(e),
            })
            return templates.TemplateResponse(request, "people/perf/template_form.html", context)

    def template_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        template_id: str,
        success: Optional[str] = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render template detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            template = svc.get_template(org_id, coerce_uuid(template_id))
        except Exception:
            return RedirectResponse(url="/people/perf/templates", status_code=303)

        context = base_context(request, auth, template.template_name, "perf", db=db)
        context["request"] = request
        context.update({
            "template": template,
            "success": success,
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/template_detail.html", context)

    def template_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        template_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render template edit form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        try:
            template = svc.get_template(org_id, coerce_uuid(template_id))
        except Exception:
            return RedirectResponse(url="/people/perf/templates", status_code=303)

        context = base_context(request, auth, f"Edit {template.template_name}", "perf", db=db)
        context["request"] = request
        context.update({
            "template": template,
            "departments": org_svc.list_departments(DepartmentFilters(is_active=True), PaginationParams(limit=100)).items,
            "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=100)).items,
            "form_data": {},
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/template_form.html", context)

    async def update_template_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        template_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle template update."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.update_template(
                org_id,
                coerce_uuid(template_id),
                template_name=form_data.get("template_name", ""),
                description=form_data.get("description") or None,
                department_id=coerce_uuid(form_data["department_id"]) if form_data.get("department_id") else None,
                rating_scale_max=parse_int(form_data.get("rating_scale_max")) or 5,
                self_assessment_enabled=form_data.get("self_assessment_enabled") == "true",
                manager_review_enabled=form_data.get("manager_review_enabled") == "true",
                peer_feedback_enabled=form_data.get("peer_feedback_enabled") == "true",
                calibration_enabled=form_data.get("calibration_enabled") == "true",
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/templates/{template_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            org_svc = OrganizationService(db, org_id)
            template = svc.get_template(org_id, coerce_uuid(template_id))
            context = base_context(request, auth, f"Edit {template.template_name}", "perf", db=db)
            context["request"] = request
            context.update({
                "template": template,
                "departments": org_svc.list_departments(DepartmentFilters(is_active=True), PaginationParams(limit=100)).items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=100)).items,
                "form_data": {},
                "error": str(e),
            })
            return templates.TemplateResponse(request, "people/perf/template_form.html", context)

    def toggle_template_active_response(
        self,
        auth: WebAuthContext,
        db: Session,
        template_id: str,
    ) -> RedirectResponse:
        """Handle template active toggle."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.toggle_template_active(org_id, coerce_uuid(template_id))
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(url=f"/people/perf/templates/{template_id}", status_code=303)

    def delete_template_response(
        self,
        auth: WebAuthContext,
        db: Session,
        template_id: str,
    ) -> RedirectResponse:
        """Handle template deletion."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.delete_template(org_id, coerce_uuid(template_id))
            db.commit()
            return RedirectResponse(url="/people/perf/templates", status_code=303)
        except Exception:
            db.rollback()
            return RedirectResponse(url=f"/people/perf/templates/{template_id}", status_code=303)

    # ─────────────────────────────────────────────────────────────────────────
    # Scorecards
    # ─────────────────────────────────────────────────────────────────────────

    def list_scorecards_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: Optional[str] = None,
        cycle_id: Optional[str] = None,
        is_finalized: Optional[str] = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render scorecards list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)
        svc = PerformanceService(db)

        finalized_filter = None
        if is_finalized == "true":
            finalized_filter = True
        elif is_finalized == "false":
            finalized_filter = False

        result = svc.list_scorecards(
            org_id,
            employee_id=parse_uuid(employee_id),
            cycle_id=parse_uuid(cycle_id),
            is_finalized=finalized_filter,
            pagination=pagination,
        )

        cycles = svc.list_cycles(org_id, pagination=PaginationParams(limit=50)).items

        context = base_context(request, auth, "Scorecards", "perf", db=db)
        context["request"] = request
        context.update({
            "scorecards": result.items,
            "employee_id": employee_id,
            "cycle_id": cycle_id,
            "is_finalized": is_finalized,
            "cycles": cycles,
            "page": result.page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        })
        return templates.TemplateResponse(request, "people/perf/scorecards.html", context)

    def scorecard_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new scorecard form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        employees = org_svc.list_employees(
            EmployeeFilters(is_active=True),
            PaginationParams(limit=500),
        ).items

        cycles = svc.list_cycles(org_id, pagination=PaginationParams(limit=50)).items

        context = base_context(request, auth, "New Scorecard", "perf", db=db)
        context["request"] = request
        context.update({
            "scorecard": None,
            "employees": employees,
            "cycles": cycles,
            "form_data": {},
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/scorecard_form.html", context)

    async def create_scorecard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle scorecard creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            scorecard = svc.create_scorecard(
                org_id,
                employee_id=coerce_uuid(form_data["employee_id"]),
                cycle_id=coerce_uuid(form_data["cycle_id"]) if form_data.get("cycle_id") else None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/scorecards/{scorecard.scorecard_id}",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            org_svc = OrganizationService(db, org_id)
            context = base_context(request, auth, "New Scorecard", "perf", db=db)
            context["request"] = request
            context.update({
                "scorecard": None,
                "employees": org_svc.list_employees(EmployeeFilters(is_active=True), PaginationParams(limit=500)).items,
                "cycles": svc.list_cycles(org_id, pagination=PaginationParams(limit=50)).items,
                "form_data": dict(form_data),
                "error": str(e),
            })
            return templates.TemplateResponse(request, "people/perf/scorecard_form.html", context)

    def scorecard_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        scorecard_id: str,
        success: Optional[str] = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render scorecard detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            scorecard = svc.get_scorecard(org_id, coerce_uuid(scorecard_id))
        except Exception:
            return RedirectResponse(url="/people/perf/scorecards", status_code=303)

        context = base_context(request, auth, f"Scorecard - {scorecard.employee.full_name if scorecard.employee else 'Unknown'}", "perf", db=db)
        context["request"] = request
        context.update({
            "scorecard": scorecard,
            "success": success,
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/scorecard_detail.html", context)

    def scorecard_update_item_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        scorecard_id: str,
        item_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render scorecard item update form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            scorecard = svc.get_scorecard(org_id, coerce_uuid(scorecard_id))
            item = next((i for i in scorecard.items if str(i.item_id) == item_id), None)
            if not item:
                return RedirectResponse(url=f"/people/perf/scorecards/{scorecard_id}", status_code=303)
        except Exception:
            return RedirectResponse(url="/people/perf/scorecards", status_code=303)

        context = base_context(request, auth, "Update Scorecard Item", "perf", db=db)
        context["request"] = request
        context.update({
            "scorecard": scorecard,
            "item": item,
            "form_data": {},
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/scorecard_item_form.html", context)

    async def update_scorecard_item_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        scorecard_id: str,
        item_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle scorecard item update."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.update_scorecard_item(
                org_id,
                coerce_uuid(scorecard_id),
                coerce_uuid(item_id),
                actual_value=parse_decimal(form_data.get("actual_value")),
                rating=parse_int(form_data.get("rating")),
                comments=form_data.get("comments") or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/scorecards/{scorecard_id}?success=Item+updated",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            scorecard = svc.get_scorecard(org_id, coerce_uuid(scorecard_id))
            item = next((i for i in scorecard.items if str(i.item_id) == item_id), None)
            context = base_context(request, auth, "Update Scorecard Item", "perf", db=db)
            context["request"] = request
            context.update({
                "scorecard": scorecard,
                "item": item,
                "form_data": dict(form_data),
                "error": str(e),
            })
            return templates.TemplateResponse(request, "people/perf/scorecard_item_form.html", context)

    def scorecard_finalize_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        scorecard_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Render scorecard finalize form."""
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            scorecard = svc.get_scorecard(org_id, coerce_uuid(scorecard_id))
        except Exception:
            return RedirectResponse(url="/people/perf/scorecards", status_code=303)

        context = base_context(request, auth, "Finalize Scorecard", "perf", db=db)
        context["request"] = request
        context.update({
            "scorecard": scorecard,
            "form_data": {},
            "error": None,
        })
        return templates.TemplateResponse(request, "people/perf/scorecard_finalize_form.html", context)

    async def finalize_scorecard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        scorecard_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle scorecard finalization."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = PerformanceService(db)

        try:
            svc.finalize_scorecard(
                org_id,
                coerce_uuid(scorecard_id),
                final_score=parse_decimal(form_data.get("final_score")),
                final_rating=parse_int(form_data.get("final_rating")),
                manager_comments=form_data.get("manager_comments") or None,
                finalized_by=auth.user_id,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/scorecards/{scorecard_id}?success=Scorecard+finalized",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            scorecard = svc.get_scorecard(org_id, coerce_uuid(scorecard_id))
            context = base_context(request, auth, "Finalize Scorecard", "perf", db=db)
            context["request"] = request
            context.update({
                "scorecard": scorecard,
                "form_data": dict(form_data),
                "error": str(e),
            })
            return templates.TemplateResponse(request, "people/perf/scorecard_finalize_form.html", context)
