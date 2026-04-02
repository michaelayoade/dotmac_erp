"""
Contract Web Service — OHCSF PMS web view service for performance contracts.

Provides view-focused operations for contract list, detail, create, and
signing workflow within the PMS module.
"""

from __future__ import annotations

import json
import logging

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData, UploadFile

from app.models.people.perf.pms_enums import ContractStatus, ContractType
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import CompetencyService, EmployeeFilters, OrganizationService
from app.services.people.perf import PerformanceService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

from .base import parse_uuid

logger = logging.getLogger(__name__)


def _get_form_str(form: FormData | None, key: str, default: str = "") -> str:
    if form is None:
        return default
    value = form.get(key, default)
    if isinstance(value, UploadFile) or value is None:
        return default
    return str(value).strip()


def _parse_competency_rows(form: FormData | None) -> list[dict]:
    """Parse 5 competency rows from structured form fields."""
    if form is None:
        raise ValueError("Competencies are required")

    selected_focus_rows = {str(v).strip() for v in form.getlist("development_focus")}
    competencies: list[dict] = []

    for index in range(1, 6):
        competency_id = _get_form_str(form, f"competency_id_{index}")
        if not competency_id:
            raise ValueError(
                f"Competency row {index} is required. Select all 5 competencies."
            )
        competencies.append(
            {
                "competency_id": competency_id,
                "is_development_focus": str(index) in selected_focus_rows,
            }
        )

    return competencies


def _normalize_competency_rows(raw_rows: list | None) -> list[dict]:
    """
    Normalize stored competency payload for template prefill.

    Supports the current shape ``[{competency_id, is_development_focus}]`` and
    legacy list-of-ids payloads.
    """
    rows: list[dict] = []
    for item in raw_rows or []:
        if isinstance(item, dict):
            competency_id = str(item.get("competency_id") or "").strip()
            if competency_id:
                rows.append(
                    {
                        "competency_id": competency_id,
                        "is_development_focus": bool(
                            item.get("is_development_focus", False)
                        ),
                    }
                )
            continue

        competency_id = str(item or "").strip()
        if competency_id:
            rows.append(
                {
                    "competency_id": competency_id,
                    "is_development_focus": False,
                }
            )

    return rows[:5]


def _build_competency_rows(selected_rows: list[dict] | None) -> list[dict]:
    """Return exactly 5 rows for competency selector rendering."""
    normalized = _normalize_competency_rows(selected_rows)
    rows = [
        {"competency_id": "", "is_development_focus": False} for _ in range(5)
    ]
    for index, row in enumerate(normalized):
        if index >= 5:
            break
        rows[index] = row
    return rows


class ContractWebService:
    """Web service for OHCSF performance contract pages."""

    # ─────────────────────────────────────────────────────────────────────────
    # List
    # ─────────────────────────────────────────────────────────────────────────

    def list_contracts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: str | None = None,
        cycle_id: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
        """Render the performance contracts list page."""
        org_id = coerce_uuid(auth.organization_id)
        pagination = PaginationParams.from_page(page, per_page=20)

        from app.services.people.perf.contract_service import (
            PerformanceContractService,
        )

        svc = PerformanceContractService(db)
        perf_svc = PerformanceService(db)

        parsed_status: ContractStatus | None = None
        if status:
            try:
                parsed_status = ContractStatus(status)
            except ValueError:
                parsed_status = None

        result = svc.list_contracts(
            org_id,
            status=parsed_status,
            cycle_id=parse_uuid(cycle_id),
            search=search if search else None,
            pagination=pagination,
        )

        cycles = perf_svc.list_cycles(
            org_id, pagination=PaginationParams(limit=100)
        ).items

        context = base_context(
            request, auth, "Performance Contracts", "pms-contracts", db=db
        )
        context["request"] = request
        context.update(
            {
                "contracts": result.items,
                "status": status,
                "cycle_id": cycle_id,
                "search": search,
                "statuses": [s.value for s in ContractStatus],
                "cycles": cycles,
                "page": result.page,
                "total_pages": result.total_pages,
                "total": result.total,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/contracts.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Detail
    # ─────────────────────────────────────────────────────────────────────────

    def contract_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        contract_id: str,
        success: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse | RedirectResponse:
        """Render the performance contract detail page."""
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.contract_service import (
            PerformanceContractService,
        )
        from app.services.people.perf.monthly_review_service import (
            MonthlyReviewService,
        )

        svc = PerformanceContractService(db)
        review_svc = MonthlyReviewService(db)
        org_svc = OrganizationService(db, org_id)

        try:
            contract = svc.get_contract(org_id, coerce_uuid(contract_id))
        except Exception:
            return RedirectResponse(url="/people/perf/pms/contracts", status_code=303)

        # Fetch linked monthly reviews for the detail view
        reviews = review_svc.list_reviews(
            org_id,
            contract_id=contract.contract_id,
            pagination=PaginationParams(limit=50),
        ).items
        employees = org_svc.list_employees(
            EmployeeFilters(is_active=True),
            PaginationParams(limit=500),
        ).items
        competencies = CompetencyService(db, org_id).list_competencies(
            is_active=True,
            pagination=PaginationParams(limit=250),
        ).items
        amendment_workflow = svc.get_active_amendment_workflow(
            org_id, contract.contract_id
        )

        context = base_context(
            request,
            auth,
            f"Contract {contract.contract_code}",
            "pms-contracts",
            db=db,
        )
        context["request"] = request
        context.update(
            {
                "contract": contract,
                "reviews": reviews,
                "employees": employees,
                "competencies": competencies,
                "selected_competencies": _normalize_competency_rows(
                    contract.competency_ids
                ),
                "amendment_workflow": amendment_workflow,
                "success": success,
                "error": error,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/contract_detail.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Form (create)
    # ─────────────────────────────────────────────────────────────────────────

    def contract_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        cycle_id: str | None = None,
        employee_id: str | None = None,
        form_data: dict | None = None,
        selected_competencies: list[dict] | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        """Render the new performance contract form."""
        org_id = coerce_uuid(auth.organization_id)
        perf_svc = PerformanceService(db)
        org_svc = OrganizationService(db, org_id)

        cycles = perf_svc.list_cycles(
            org_id, pagination=PaginationParams(limit=100)
        ).items
        employees = org_svc.list_employees(
            EmployeeFilters(is_active=True),
            PaginationParams(limit=500),
        ).items
        competencies = CompetencyService(db, org_id).list_competencies(
            is_active=True,
            pagination=PaginationParams(limit=250),
        ).items
        competency_rows = _build_competency_rows(selected_competencies)

        context = base_context(
            request, auth, "New Performance Contract", "pms-contracts", db=db
        )
        context["request"] = request
        context.update(
            {
                "contract": None,
                "cycles": cycles,
                "employees": employees,
                "competencies": competencies,
                "contract_types": [ct.value for ct in ContractType],
                "prefill_cycle_id": cycle_id,
                "prefill_employee_id": employee_id,
                "competency_rows": competency_rows,
                "form_data": form_data or {},
                "error": error,
            }
        )
        return templates.TemplateResponse(
            request, "people/perf/pms/contract_form.html", context
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Create (POST)
    # ─────────────────────────────────────────────────────────────────────────

    async def create_contract_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Handle performance contract creation."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.contract_service import (
            PerformanceContractService,
        )

        svc = PerformanceContractService(db)

        try:
            cycle_id_str = _get_form_str(form_data, "cycle_id")
            employee_id_str = _get_form_str(form_data, "employee_id")
            supervisor_id_str = _get_form_str(form_data, "supervisor_id")
            contract_code = _get_form_str(form_data, "contract_code")
            contract_type_str = _get_form_str(form_data, "contract_type")
            development_plan = _get_form_str(form_data, "development_plan") or None

            if not cycle_id_str:
                raise ValueError("Appraisal cycle is required")
            if not employee_id_str:
                raise ValueError("Employee is required")
            if not supervisor_id_str:
                raise ValueError("Supervisor is required")
            if not contract_code:
                raise ValueError("Contract code is required")
            if not contract_type_str:
                raise ValueError("Contract type is required")

            try:
                contract_type = ContractType(contract_type_str)
            except ValueError:
                raise ValueError(f"Invalid contract type: {contract_type_str}")

            # Parse objectives from JSON textarea
            objectives_raw = _get_form_str(form_data, "objectives_json", "[]")
            try:
                objectives = json.loads(objectives_raw) if objectives_raw else []
            except json.JSONDecodeError:
                raise ValueError("Objectives data is not valid JSON")

            competency_ids = _parse_competency_rows(form_data)

            contract = svc.create_contract(
                org_id,
                cycle_id=coerce_uuid(cycle_id_str),
                employee_id=coerce_uuid(employee_id_str),
                supervisor_id=coerce_uuid(supervisor_id_str),
                contract_code=contract_code,
                contract_type=contract_type,
                objectives=objectives,
                competency_ids=competency_ids,
                development_plan=development_plan,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/contracts/{contract.contract_id}?saved=1",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            try:
                selected_competencies = _parse_competency_rows(form=form_data)
            except Exception:
                selected_focus_rows = {
                    str(v).strip() for v in form_data.getlist("development_focus")
                }
                selected_competencies = _normalize_competency_rows(
                    [
                        {
                            "competency_id": _get_form_str(
                                form_data, f"competency_id_{idx}"
                            ),
                            "is_development_focus": str(idx) in selected_focus_rows,
                        }
                        for idx in range(1, 6)
                    ]
                )
            return self.contract_form_response(
                request,
                auth,
                db,
                form_data=dict(form_data),
                selected_competencies=selected_competencies,
                error=str(e),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # Sign actions
    # ─────────────────────────────────────────────────────────────────────────

    def sign_employee_response(
        self,
        auth: WebAuthContext,
        db: Session,
        contract_id: str,
    ) -> RedirectResponse:
        """Record employee signature on a contract."""
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.contract_service import (
            PerformanceContractService,
        )

        svc = PerformanceContractService(db)
        try:
            svc.sign_employee(
                org_id,
                coerce_uuid(contract_id),
                actor_person_id=coerce_uuid(auth.person_id),
            )
            db.commit()
            success_msg = "Employee signature recorded."
        except Exception as e:
            db.rollback()
            logger.warning("Employee sign failed for contract %s: %s", contract_id, e)
            success_msg = None

        return RedirectResponse(
            url=f"/people/perf/pms/contracts/{contract_id}"
            + ("?saved=1" if success_msg else "?error=sign_failed"),
            status_code=303,
        )

    def sign_supervisor_response(
        self,
        auth: WebAuthContext,
        db: Session,
        contract_id: str,
    ) -> RedirectResponse:
        """Record supervisor signature on a contract."""
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.contract_service import (
            PerformanceContractService,
        )

        svc = PerformanceContractService(db)
        try:
            svc.sign_supervisor(
                org_id,
                coerce_uuid(contract_id),
                actor_person_id=coerce_uuid(auth.person_id),
            )
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Supervisor sign failed for contract %s: %s", contract_id, e)
            return RedirectResponse(
                url=f"/people/perf/pms/contracts/{contract_id}?error=sign_failed",
                status_code=303,
            )

        return RedirectResponse(
            url=f"/people/perf/pms/contracts/{contract_id}?saved=1",
            status_code=303,
        )

    async def amend_contract_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        contract_id: str,
    ) -> RedirectResponse:
        """Create an amendment contract and start staged signoff workflow."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.contract_service import PerformanceContractService

        svc = PerformanceContractService(db)
        try:
            objectives_raw = _get_form_str(form_data, "new_objectives_json", "[]")
            amendment_reason = _get_form_str(form_data, "amendment_reason")
            hod_id_str = _get_form_str(form_data, "hod_id")
            hr_head_id_str = _get_form_str(form_data, "hr_head_id")

            if not amendment_reason:
                raise ValueError("Amendment reason is required")
            if not hod_id_str or not hr_head_id_str:
                raise ValueError("HoD and HR Head are required for signoff chain")

            try:
                new_objectives = json.loads(objectives_raw) if objectives_raw else []
            except json.JSONDecodeError as exc:
                raise ValueError("Objectives data is not valid JSON") from exc

            competency_ids = _parse_competency_rows(form_data)

            new_contract = svc.amend_contract(
                org_id,
                coerce_uuid(contract_id),
                new_objectives=new_objectives,
                amendment_reason=amendment_reason,
                competency_ids=competency_ids,
            )
            svc.create_amendment_workflow(
                org_id,
                original_contract_id=coerce_uuid(contract_id),
                new_contract_id=new_contract.contract_id,
                hod_id=coerce_uuid(hod_id_str),
                hr_head_id=coerce_uuid(hr_head_id_str),
                signoff_note=_get_form_str(form_data, "signoff_note") or None,
            )

            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/contracts/{new_contract.contract_id}?saved=1",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("Failed to create contract amendment for %s", contract_id)
            return RedirectResponse(
                url=f"/people/perf/pms/contracts/{contract_id}?error={str(e)}",
                status_code=303,
            )

    def approve_amendment_stage_response(
        self,
        auth: WebAuthContext,
        db: Session,
        contract_id: str,
        *,
        stage: str,
        note: str | None = None,
    ) -> RedirectResponse:
        """Approve one stage of amendment signoff workflow."""
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.contract_service import PerformanceContractService

        svc = PerformanceContractService(db)
        try:
            svc.approve_amendment_stage(
                org_id,
                coerce_uuid(contract_id),
                stage=stage,
                actor_person_id=coerce_uuid(auth.person_id),
                note=note,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/contracts/{contract_id}?saved=1",
                status_code=303,
            )
        except Exception:
            db.rollback()
            logger.exception(
                "Failed to approve amendment stage %s for contract %s", stage, contract_id
            )
            return RedirectResponse(
                url=f"/people/perf/pms/contracts/{contract_id}?error=amendment_signoff_failed",
                status_code=303,
            )

    async def reject_amendment_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        contract_id: str,
        *,
        stage: str,
    ) -> RedirectResponse:
        """Reject pending amendment at a given stage."""
        form_data = await request.form()
        org_id = coerce_uuid(auth.organization_id)

        from app.services.people.perf.contract_service import PerformanceContractService

        svc = PerformanceContractService(db)
        try:
            reason = _get_form_str(form_data, "rejection_reason")
            if not reason:
                raise ValueError("Rejection reason is required")
            svc.reject_amendment(
                org_id,
                coerce_uuid(contract_id),
                stage=stage,
                actor_person_id=coerce_uuid(auth.person_id),
                reason=reason,
            )
            db.commit()
            return RedirectResponse(
                url=f"/people/perf/pms/contracts/{contract_id}?saved=1",
                status_code=303,
            )
        except Exception:
            db.rollback()
            logger.exception("Failed to reject amendment for contract %s", contract_id)
            return RedirectResponse(
                url=f"/people/perf/pms/contracts/{contract_id}?error=amendment_reject_failed",
                status_code=303,
            )
