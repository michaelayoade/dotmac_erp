"""
Institutional Performance Web Service — OHCSF Performance Management System.

Handles list, detail, new form, and create responses for
InstitutionalPerformance records.
"""

from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.perf.pms_enums import InstitutionalPerfStatus, InstitutionType
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.perf.governance_service import (
    PMSGovernanceService,
    WORKFLOW_STAGES,
)
from app.services.people.perf.institutional_service import (
    InstitutionalPerfNotFoundError,
    InstitutionalPerformanceService,
    InstitutionalValidationError,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import parse_uuid

logger = logging.getLogger(__name__)


class InstitutionalWebService:
    """Web service for institutional performance list, detail, and create views."""

    @staticmethod
    def _form_text(value: object | None, default: str = "") -> str:
        if isinstance(value, str):
            return value.strip()
        return default

    def list_institutional_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None = None,
        cycle_id: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render institutional performance list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=25)
        svc = InstitutionalPerformanceService(db)

        inst_status: InstitutionalPerfStatus | None = None
        if status:
            try:
                inst_status = InstitutionalPerfStatus(status)
            except ValueError:
                inst_status = None

        cycle_uuid = parse_uuid(cycle_id)

        result = svc.list_institutional_perfs(
            org_id,
            status=inst_status,
            cycle_id=cycle_uuid,
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

        context = base_context(
            request, auth, "Institutional Performance", "pms-institutional", db=db
        )
        context["request"] = request
        context.update(
            {
                "records": result.items,
                "status": status,
                "cycle_id": cycle_id,
                "statuses": [s.value for s in InstitutionalPerfStatus],
                "cycles": cycles,
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/institutional.html", context
        )

    def institutional_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        inst_perf_id: str,
    ) -> HTMLResponse:
        """Render institutional performance detail page."""
        org_id = coerce_uuid(auth.organization_id)
        svc = InstitutionalPerformanceService(db)
        governance_svc = PMSGovernanceService(db)

        inst_uuid = parse_uuid(inst_perf_id)
        if inst_uuid is None:
            context = base_context(
                request, auth, "Record Not Found", "pms-institutional", db=db
            )
            context["request"] = request
            context.update({"record": None, "error": "Invalid record ID"})
            return templates.TemplateResponse(
                request,
                "people/perf/pms/institutional_detail.html",
                context,
                status_code=404,
            )

        try:
            record = svc.get_institutional_perf(org_id, inst_uuid)
        except Exception as e:
            context = base_context(
                request, auth, "Record Not Found", "pms-institutional", db=db
            )
            context["request"] = request
            context.update({"record": None, "error": str(e)})
            return templates.TemplateResponse(
                request,
                "people/perf/pms/institutional_detail.html",
                context,
                status_code=404,
            )

        # Parse criteria scores for display
        criteria_scores = record.criteria_scores or {}
        criteria_list = []
        if isinstance(criteria_scores, dict):
            for name, entry in criteria_scores.items():
                criteria_list.append(
                    {
                        "criteria": name,
                        "weight": entry.get("weight", 0),
                        "target": entry.get("target", ""),
                        "achievement": entry.get("achievement", ""),
                        "raw_score": entry.get("raw_score", ""),
                        "weighted_score": entry.get("weighted_score", ""),
                    }
                )
        elif isinstance(criteria_scores, list):
            criteria_list = criteria_scores

        context = base_context(
            request,
            auth,
            f"Institutional — {record.institution_type.value if record.institution_type else ''}",
            "pms-institutional",
            db=db,
        )
        from app.services.people.hr import EmployeeFilters
        from app.services.people.hr.employees import EmployeeService

        emp_svc = EmployeeService(db, org_id)
        employees = emp_svc.list_employees(
            EmployeeFilters(is_active=True), PaginationParams(limit=300)
        ).items

        context["request"] = request
        success = request.query_params.get("saved")
        error = request.query_params.get("error")
        context.update(
            {
                "record": record,
                "criteria_list": criteria_list,
                "success": success,
                "error": error,
                "employees": employees,
                "workflow_stages": WORKFLOW_STAGES,
                "governance_actions": governance_svc.list_governance_actions(
                    org_id, inst_perf_id=record.inst_perf_id
                ),
                "InstitutionalPerfStatus": InstitutionalPerfStatus,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/institutional_detail.html", context
        )

    def institutional_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new institutional performance form."""
        org_id = coerce_uuid(auth.organization_id)

        from sqlalchemy import select

        from app.models.people.perf.appraisal_cycle import AppraisalCycle
        from app.models.people.perf.institutional_performance import (
            InstitutionalCriteriaTemplate,
        )

        cycles = list(
            db.scalars(
                select(AppraisalCycle)
                .where(AppraisalCycle.organization_id == org_id)
                .order_by(AppraisalCycle.start_date.desc())
                .limit(50)
            ).all()
        )

        templates_list = list(
            db.scalars(
                select(InstitutionalCriteriaTemplate)
                .where(
                    InstitutionalCriteriaTemplate.organization_id == org_id,
                    InstitutionalCriteriaTemplate.is_active == True,  # noqa: E712
                )
                .order_by(
                    InstitutionalCriteriaTemplate.institution_type,
                    InstitutionalCriteriaTemplate.sequence,
                )
            ).all()
        )

        from app.services.people.hr import EmployeeFilters
        from app.services.people.hr.employees import EmployeeService

        emp_svc = EmployeeService(db, org_id)
        employees = emp_svc.list_employees(
            EmployeeFilters(is_active=True), PaginationParams(limit=500)
        ).items

        context = base_context(
            request,
            auth,
            "New Institutional Performance Record",
            "pms-institutional",
            db=db,
        )
        context["request"] = request
        context.update(
            {
                "record": None,
                "form_data": {},
                "error": None,
                "cycles": cycles,
                "criteria_templates": templates_list,
                "institution_types": [t.value for t in InstitutionType],
                "employees": employees,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/institutional_form.html", context
        )

    async def create_institutional_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle institutional performance record creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        svc = InstitutionalPerformanceService(db)

        try:
            cycle_id = parse_uuid(self._form_text(form_data.get("cycle_id")))
            if not cycle_id:
                raise ValueError("Appraisal cycle is required")

            institution_type_str = self._form_text(form_data.get("institution_type"))
            try:
                institution_type = InstitutionType(institution_type_str)
            except ValueError as exc:
                raise ValueError("Invalid institution type") from exc

            record = svc.create_for_cycle(
                org_id,
                cycle_id=cycle_id,
                institution_type=institution_type,
                department_id=parse_uuid(
                    self._form_text(form_data.get("department_id"))
                ),
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{record.inst_perf_id}?saved=1",
                status_code=303,
            )
        except Exception as e:
            db.rollback()

            from sqlalchemy import select

            from app.models.people.perf.appraisal_cycle import AppraisalCycle
            from app.models.people.perf.institutional_performance import (
                InstitutionalCriteriaTemplate,
            )

            cycles = list(
                db.scalars(
                    select(AppraisalCycle)
                    .where(AppraisalCycle.organization_id == org_id)
                    .order_by(AppraisalCycle.start_date.desc())
                    .limit(50)
                ).all()
            )
            templates_list = list(
                db.scalars(
                    select(InstitutionalCriteriaTemplate)
                    .where(
                        InstitutionalCriteriaTemplate.organization_id == org_id,
                        InstitutionalCriteriaTemplate.is_active == True,  # noqa: E712
                    )
                    .order_by(
                        InstitutionalCriteriaTemplate.institution_type,
                        InstitutionalCriteriaTemplate.sequence,
                    )
                ).all()
            )

            from app.services.people.hr import EmployeeFilters
            from app.services.people.hr.employees import EmployeeService

            emp_svc = EmployeeService(db, org_id)
            employees = emp_svc.list_employees(
                EmployeeFilters(), PaginationParams(limit=500)
            ).items

            context = base_context(
                request,
                auth,
                "New Institutional Performance Record",
                "pms-institutional",
                db=db,
            )
            context["request"] = request
            context.update(
                {
                    "record": None,
                    "form_data": dict(form_data),
                    "error": str(e),
                    "cycles": cycles,
                    "criteria_templates": templates_list,
                    "institution_types": [t.value for t in InstitutionType],
                    "employees": employees,
                }
            )
            return templates.TemplateResponse(
                request, "people/perf/pms/institutional_form.html", context
            )

    async def assign_governance_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        inst_perf_id: str,
    ) -> RedirectResponse:
        """Assign owner/reviewer/approver for institutional governance."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        inst_uuid = parse_uuid(inst_perf_id)
        if not inst_uuid:
            return RedirectResponse(
                url="/people/perf/pms/institutional?error=invalid_record",
                status_code=303,
            )

        gov = PMSGovernanceService(db)
        try:
            gov.assign_governance_roles(
                org_id,
                inst_perf_id=inst_uuid,
                actor_employee_id=auth.employee_id,
                actor_role="HRM",
                owner_id=parse_uuid(self._form_text(form_data.get("owner_id"))),
                reviewer_id=parse_uuid(self._form_text(form_data.get("reviewer_id"))),
                approver_id=parse_uuid(self._form_text(form_data.get("approver_id"))),
                note=self._form_text(form_data.get("workflow_note")) or None,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{inst_uuid}?saved=1",
                status_code=303,
            )
        except Exception:
            logger.exception(
                "Failed to assign governance roles for institutional record %s",
                inst_uuid,
            )
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{inst_uuid}?error=assign_failed",
                status_code=303,
            )

    async def transition_governance_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        inst_perf_id: str,
    ) -> RedirectResponse:
        """Transition institutional governance workflow stage."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        inst_uuid = parse_uuid(inst_perf_id)
        if not inst_uuid:
            return RedirectResponse(
                url="/people/perf/pms/institutional?error=invalid_record",
                status_code=303,
            )

        gov = PMSGovernanceService(db)
        target_stage = self._form_text(form_data.get("target_stage"))
        note = self._form_text(form_data.get("workflow_note")) or None
        stage_actor_role = {
            "INTERNAL_REVIEW": "MDA_PRS",
            "CENTRAL_REVIEW": "FMFBNP",
            "APPROVED": "OHCSF_PMD",
            "FINAL_SIGNOFF": "CDCU_OSGF",
            "RETURNED": "OHCSF_PMD",
        }.get(target_stage, "OHCSF_PMD")
        try:
            gov.transition_stage(
                org_id,
                inst_perf_id=inst_uuid,
                target_stage=target_stage,
                actor_employee_id=auth.employee_id,
                actor_role=stage_actor_role,
                note=note,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{inst_uuid}?saved=1",
                status_code=303,
            )
        except Exception:
            logger.exception(
                "Failed to transition stage for institutional record %s", inst_uuid
            )
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{inst_uuid}?error=transition_failed",
                status_code=303,
            )

    async def score_institutional_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        inst_perf_id: str,
    ) -> RedirectResponse:
        """Score criteria for an institutional performance record."""
        import json

        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        try:
            scores_raw = str(form_data.get("criteria_scores_json", "{}")).strip()
            try:
                criteria_scores = json.loads(scores_raw)
            except json.JSONDecodeError as exc:
                raise InstitutionalValidationError(
                    "Criteria scores data is not valid JSON"
                ) from exc
            svc = InstitutionalPerformanceService(db)
            svc.score_criteria(
                org_id, coerce_uuid(inst_perf_id), criteria_scores=criteria_scores
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{inst_perf_id}?saved=1",
                status_code=303,
            )
        except (InstitutionalPerfNotFoundError, InstitutionalValidationError) as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{inst_perf_id}?error={e}",
                status_code=303,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to score criteria for institutional record %s", inst_perf_id
            )
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{inst_perf_id}?error=An+unexpected+error+occurred",
                status_code=303,
            )

    async def reconcile_institutional_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        inst_perf_id: str,
    ) -> RedirectResponse:
        """Reconcile institutional performance with employee ratings."""
        from decimal import Decimal

        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)
        try:
            notes = str(form_data.get("notes", "")).strip()
            if not notes:
                raise InstitutionalValidationError("Notes are required")
            adjusted_str = str(form_data.get("adjusted_composite", "")).strip()
            adjusted_composite = Decimal(adjusted_str) if adjusted_str else None
            svc = InstitutionalPerformanceService(db)
            svc.reconcile_with_employee_ratings(
                org_id,
                coerce_uuid(inst_perf_id),
                reconciled_by_id=coerce_uuid(auth.person_id),
                notes=notes,
                adjusted_composite=adjusted_composite,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{inst_perf_id}?saved=1",
                status_code=303,
            )
        except (InstitutionalPerfNotFoundError, InstitutionalValidationError) as e:
            db.rollback()
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{inst_perf_id}?error={e}",
                status_code=303,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to reconcile institutional record %s", inst_perf_id
            )
            return RedirectResponse(
                url=f"/people/perf/pms/institutional/{inst_perf_id}?error=An+unexpected+error+occurred",
                status_code=303,
            )
