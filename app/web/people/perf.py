"""
Performance web routes.

Lists appraisals and KPIs with full CRUD support.
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.perf import AppraisalStatus, KPIStatus
from app.services.common import PaginationParams, coerce_uuid
from app.services.people.hr import EmployeeFilters, OrganizationService
from app.services.people.perf import PerformanceService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_hr_access


router = APIRouter(prefix="/perf", tags=["people-perf-web"])


def _parse_uuid(value: Optional[str]) -> Optional[UUID]:
    if not value:
        return None
    try:
        return coerce_uuid(value)
    except Exception:
        return None


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/appraisals", response_class=HTMLResponse)
def list_appraisals(
    request: Request,
    status: Optional[str] = None,
    employee_id: Optional[str] = None,
    cycle_id: Optional[str] = None,
    manager_id: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisals list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = PerformanceService(db)

    status_enum = None
    if status:
        try:
            status_enum = AppraisalStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_appraisals(
        org_id,
        status=status_enum,
        employee_id=_parse_uuid(employee_id),
        cycle_id=_parse_uuid(cycle_id),
        manager_id=_parse_uuid(manager_id),
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
    return templates.TemplateResponse(request, "people/perf/appraisals.html", context)


@router.get("/goals", response_class=HTMLResponse)
def list_kpis(
    request: Request,
    status: Optional[str] = None,
    search: Optional[str] = None,
    employee_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KPIs list page."""
    org_id = coerce_uuid(auth.organization_id)
    pagination = PaginationParams.from_page(page, per_page=20)
    svc = PerformanceService(db)

    status_enum = None
    if status:
        try:
            status_enum = KPIStatus(status)
        except ValueError:
            status_enum = None

    result = svc.list_kpis(
        org_id,
        status=status_enum,
        search=search,
        employee_id=_parse_uuid(employee_id),
        from_date=_parse_date(start_date),
        to_date=_parse_date(end_date),
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


@router.get("/feedback", response_class=HTMLResponse)
def list_feedback(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Feedback placeholder page."""
    context = base_context(request, auth, "Feedback", "perf", db=db)
    context["request"] = request
    return templates.TemplateResponse(request, "people/perf/feedback.html", context)


# ===========================================================================
# Appraisal CRUD Routes
# ===========================================================================


@router.get("/appraisals/new", response_class=HTMLResponse)
def new_appraisal_form(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New appraisal form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    # Get active cycles
    cycles = svc.list_cycles(org_id, pagination=PaginationParams(limit=100)).items

    # Get active employees
    employees = org_svc.list_employees(
        EmployeeFilters(is_active=True),
        PaginationParams(limit=500),
    ).items

    # Get managers (same as employees for now)
    managers = employees

    # Get templates
    templates_list = svc.list_templates(
        org_id, is_active=True, pagination=PaginationParams(limit=100)
    ).items

    # Get KRAs for manual selection
    kras = svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items

    context = base_context(request, auth, "New Appraisal", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "cycles": cycles,
            "employees": employees,
            "managers": managers,
            "templates": templates_list,
            "kras": kras,
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_form.html", context)


@router.post("/appraisals/new", response_class=HTMLResponse)
def create_appraisal(
    request: Request,
    cycle_id: str = Form(...),
    employee_id: str = Form(...),
    manager_id: str = Form(...),
    template_id: Optional[str] = Form(None),
    kra_ids: Optional[list[str]] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new appraisal."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        # Prepare KRA scores if provided
        kra_scores = None
        if kra_ids:
            kra_scores = [{"kra_id": coerce_uuid(kra_id)} for kra_id in kra_ids]

        appraisal = svc.create_appraisal(
            org_id,
            cycle_id=coerce_uuid(cycle_id),
            employee_id=coerce_uuid(employee_id),
            manager_id=coerce_uuid(manager_id),
            template_id=coerce_uuid(template_id) if template_id else None,
            kra_scores=kra_scores,
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
                "cycles": svc.list_cycles(org_id, pagination=PaginationParams(limit=100)).items,
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "managers": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "templates": svc.list_templates(
                    org_id, is_active=True, pagination=PaginationParams(limit=100)
                ).items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
                "form_data": {
                    "cycle_id": cycle_id,
                    "employee_id": employee_id,
                    "manager_id": manager_id,
                    "template_id": template_id,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/appraisal_form.html", context)


@router.get("/appraisals/{appraisal_id}", response_class=HTMLResponse)
def appraisal_detail(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Appraisal detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
    except Exception as e:
        return RedirectResponse(url="/people/perf/appraisals", status_code=303)

    context = base_context(request, auth, "Appraisal Details", "perf", db=db)
    context["request"] = request
    context["appraisal"] = appraisal
    return templates.TemplateResponse(request, "people/perf/appraisal_detail.html", context)


@router.get("/appraisals/{appraisal_id}/edit", response_class=HTMLResponse)
def edit_appraisal_form(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit appraisal form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    try:
        appraisal = svc.get_appraisal(org_id, coerce_uuid(appraisal_id))
    except Exception:
        return RedirectResponse(url="/people/perf/appraisals", status_code=303)

    if appraisal.status != AppraisalStatus.DRAFT:
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}", status_code=303
        )

    context = base_context(request, auth, "Edit Appraisal", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "appraisal": appraisal,
            "cycles": svc.list_cycles(org_id, pagination=PaginationParams(limit=100)).items,
            "employees": org_svc.list_employees(
                EmployeeFilters(is_active=True), PaginationParams(limit=500)
            ).items,
            "managers": org_svc.list_employees(
                EmployeeFilters(is_active=True), PaginationParams(limit=500)
            ).items,
            "templates": svc.list_templates(
                org_id, is_active=True, pagination=PaginationParams(limit=100)
            ).items,
            "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/appraisal_form.html", context)


@router.post("/appraisals/{appraisal_id}/edit", response_class=HTMLResponse)
def update_appraisal(
    request: Request,
    appraisal_id: str,
    cycle_id: str = Form(...),
    employee_id: str = Form(...),
    manager_id: str = Form(...),
    template_id: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update an appraisal."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_appraisal(
            org_id,
            coerce_uuid(appraisal_id),
            cycle_id=coerce_uuid(cycle_id),
            manager_id=coerce_uuid(manager_id),
            template_id=coerce_uuid(template_id) if template_id else None,
        )
        db.commit()
        return RedirectResponse(
            url=f"/people/perf/appraisals/{appraisal_id}", status_code=303
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
                "cycles": svc.list_cycles(org_id, pagination=PaginationParams(limit=100)).items,
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "managers": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "templates": svc.list_templates(
                    org_id, is_active=True, pagination=PaginationParams(limit=100)
                ).items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
                "form_data": {
                    "cycle_id": cycle_id,
                    "manager_id": manager_id,
                    "template_id": template_id,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/appraisal_form.html", context)


@router.post("/appraisals/{appraisal_id}/cancel")
def cancel_appraisal(
    request: Request,
    appraisal_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Cancel an appraisal."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_appraisal(
            org_id, coerce_uuid(appraisal_id), status=AppraisalStatus.CANCELLED
        )
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/perf/appraisals/{appraisal_id}", status_code=303)


# ===========================================================================
# KPI/Goal CRUD Routes
# ===========================================================================


@router.get("/goals/new", response_class=HTMLResponse)
def new_kpi_form(
    request: Request,
    employee_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """New KPI form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    employees = org_svc.list_employees(
        EmployeeFilters(is_active=True),
        PaginationParams(limit=500),
    ).items

    kras = svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items

    context = base_context(request, auth, "New KPI", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "employees": employees,
            "kras": kras,
            "form_data": {"employee_id": employee_id} if employee_id else {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/kpi_form.html", context)


@router.post("/goals/new", response_class=HTMLResponse)
def create_kpi(
    request: Request,
    kpi_name: str = Form(...),
    employee_id: str = Form(...),
    period_start: str = Form(...),
    period_end: str = Form(...),
    target_value: str = Form(...),
    kra_id: Optional[str] = Form(None),
    unit_of_measure: Optional[str] = Form(None),
    threshold_value: Optional[str] = Form(None),
    stretch_value: Optional[str] = Form(None),
    weightage: Optional[str] = Form("0"),
    description: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Create a new KPI."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        kpi = svc.create_kpi(
            org_id,
            employee_id=coerce_uuid(employee_id),
            kpi_name=kpi_name,
            period_start=date.fromisoformat(period_start),
            period_end=date.fromisoformat(period_end),
            target_value=Decimal(target_value),
            kra_id=coerce_uuid(kra_id) if kra_id else None,
            unit_of_measure=unit_of_measure or None,
            threshold_value=Decimal(threshold_value) if threshold_value else None,
            stretch_value=Decimal(stretch_value) if stretch_value else None,
            weightage=Decimal(weightage) if weightage else Decimal("0"),
            description=description or None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(url=f"/people/perf/goals/{kpi.kpi_id}", status_code=303)
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)

        context = base_context(request, auth, "New KPI", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
                "form_data": {
                    "kpi_name": kpi_name,
                    "employee_id": employee_id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "target_value": target_value,
                    "kra_id": kra_id,
                    "unit_of_measure": unit_of_measure,
                    "threshold_value": threshold_value,
                    "stretch_value": stretch_value,
                    "weightage": weightage,
                    "description": description,
                    "notes": notes,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/kpi_form.html", context)


@router.get("/goals/{kpi_id}", response_class=HTMLResponse)
def kpi_detail(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KPI detail page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        kpi = svc.get_kpi(org_id, coerce_uuid(kpi_id))
    except Exception:
        return RedirectResponse(url="/people/perf/goals", status_code=303)

    context = base_context(request, auth, "KPI Details", "perf", db=db)
    context["request"] = request
    context["kpi"] = kpi
    return templates.TemplateResponse(request, "people/perf/kpi_detail.html", context)


@router.get("/goals/{kpi_id}/edit", response_class=HTMLResponse)
def edit_kpi_form(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Edit KPI form."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db, org_id)

    try:
        kpi = svc.get_kpi(org_id, coerce_uuid(kpi_id))
    except Exception:
        return RedirectResponse(url="/people/perf/goals", status_code=303)

    if kpi.status in [KPIStatus.ACHIEVED, KPIStatus.CANCELLED]:
        return RedirectResponse(url=f"/people/perf/goals/{kpi_id}", status_code=303)

    context = base_context(request, auth, "Edit KPI", "perf", db=db)
    context["request"] = request
    context.update(
        {
            "kpi": kpi,
            "employees": org_svc.list_employees(
                EmployeeFilters(is_active=True), PaginationParams(limit=500)
            ).items,
            "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
            "form_data": {},
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "people/perf/kpi_form.html", context)


@router.post("/goals/{kpi_id}/edit", response_class=HTMLResponse)
def update_kpi(
    request: Request,
    kpi_id: str,
    kpi_name: str = Form(...),
    employee_id: str = Form(...),
    period_start: str = Form(...),
    period_end: str = Form(...),
    target_value: str = Form(...),
    kra_id: Optional[str] = Form(None),
    unit_of_measure: Optional[str] = Form(None),
    threshold_value: Optional[str] = Form(None),
    stretch_value: Optional[str] = Form(None),
    weightage: Optional[str] = Form("0"),
    description: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update a KPI."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_kpi(
            org_id,
            coerce_uuid(kpi_id),
            kpi_name=kpi_name,
            period_start=date.fromisoformat(period_start),
            period_end=date.fromisoformat(period_end),
            target_value=Decimal(target_value),
            kra_id=coerce_uuid(kra_id) if kra_id else None,
            unit_of_measure=unit_of_measure or None,
            threshold_value=Decimal(threshold_value) if threshold_value else None,
            stretch_value=Decimal(stretch_value) if stretch_value else None,
            weightage=Decimal(weightage) if weightage else Decimal("0"),
            description=description or None,
            notes=notes or None,
        )
        db.commit()
        return RedirectResponse(url=f"/people/perf/goals/{kpi_id}", status_code=303)
    except Exception as e:
        db.rollback()
        org_svc = OrganizationService(db, org_id)
        kpi = svc.get_kpi(org_id, coerce_uuid(kpi_id))

        context = base_context(request, auth, "Edit KPI", "perf", db=db)
        context["request"] = request
        context.update(
            {
                "kpi": kpi,
                "employees": org_svc.list_employees(
                    EmployeeFilters(is_active=True), PaginationParams(limit=500)
                ).items,
                "kras": svc.list_kras(org_id, is_active=True, pagination=PaginationParams(limit=200)).items,
                "form_data": {
                    "kpi_name": kpi_name,
                    "period_start": period_start,
                    "period_end": period_end,
                    "target_value": target_value,
                    "kra_id": kra_id,
                    "unit_of_measure": unit_of_measure,
                    "threshold_value": threshold_value,
                    "stretch_value": stretch_value,
                    "weightage": weightage,
                    "description": description,
                    "notes": notes,
                },
                "error": str(e),
                "errors": {},
            }
        )
        return templates.TemplateResponse(request, "people/perf/kpi_form.html", context)


@router.post("/goals/{kpi_id}/update-progress")
def update_kpi_progress(
    request: Request,
    kpi_id: str,
    actual_value: str = Form(...),
    evidence: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Update KPI progress."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.update_kpi_progress(
            org_id,
            coerce_uuid(kpi_id),
            actual_value=Decimal(actual_value),
            evidence=evidence or None,
            notes=notes or None,
        )
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url=f"/people/perf/goals/{kpi_id}", status_code=303)


@router.post("/goals/{kpi_id}/delete")
def delete_kpi(
    request: Request,
    kpi_id: str,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Delete a KPI."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    try:
        svc.delete_kpi(org_id, coerce_uuid(kpi_id))
        db.commit()
        return RedirectResponse(url="/people/perf/goals", status_code=303)
    except Exception:
        db.rollback()
        return RedirectResponse(url=f"/people/perf/goals/{kpi_id}", status_code=303)


# ─────────────────────────────────────────────────────────────────────────────
# Performance Reports
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/reports/ratings", response_class=HTMLResponse)
def report_ratings(
    request: Request,
    cycle_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Performance ratings distribution report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    report = svc.get_ratings_distribution_report(
        org_id,
        cycle_id=_parse_uuid(cycle_id),
    )

    context = base_context(request, auth, "Ratings Report", "perf", db=db)
    context.update({
        "report": report,
        "cycle_id": cycle_id or "",
    })

    return templates.TemplateResponse(
        request,
        "people/perf/reports/ratings.html",
        context,
    )


@router.get("/reports/by-department", response_class=HTMLResponse)
def report_by_department(
    request: Request,
    cycle_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Performance by department report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    report = svc.get_performance_by_department_report(
        org_id,
        cycle_id=_parse_uuid(cycle_id),
    )

    # Get cycles for filter
    cycles = svc.list_cycles(org_id).items

    context = base_context(request, auth, "Performance by Department", "perf", db=db)
    context.update({
        "report": report,
        "cycles": cycles,
        "cycle_id": cycle_id or "",
    })

    return templates.TemplateResponse(
        request,
        "people/perf/reports/by_department.html",
        context,
    )


@router.get("/reports/kpi-achievement", response_class=HTMLResponse)
def report_kpi_achievement(
    request: Request,
    department_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """KPI achievement rates report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)
    org_svc = OrganizationService(db)

    report = svc.get_kpi_achievement_report(
        org_id,
        department_id=_parse_uuid(department_id),
    )

    # Get departments for filter
    departments = org_svc.list_departments(org_id).items

    context = base_context(request, auth, "KPI Achievement", "perf", db=db)
    context.update({
        "report": report,
        "departments": departments,
        "department_id": department_id or "",
    })

    return templates.TemplateResponse(
        request,
        "people/perf/reports/kpi_achievement.html",
        context,
    )


@router.get("/reports/trends", response_class=HTMLResponse)
def report_trends(
    request: Request,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Performance trends across cycles report."""
    org_id = coerce_uuid(auth.organization_id)
    svc = PerformanceService(db)

    report = svc.get_performance_trends_report(org_id)

    context = base_context(request, auth, "Performance Trends", "perf", db=db)
    context.update({
        "report": report,
    })

    return templates.TemplateResponse(
        request,
        "people/perf/reports/trends.html",
        context,
    )
