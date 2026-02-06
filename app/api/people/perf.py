"""
Performance Management API Router.

Thin API wrapper for Performance Management endpoints. All business logic is in services.
"""

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.models.people.perf import AppraisalCycleStatus, AppraisalStatus, KPIStatus
from app.schemas.people.perf import (
    # Appraisal Cycle
    AppraisalCycleCreate,
    AppraisalCycleUpdate,
    AppraisalCycleRead,
    AppraisalCycleListResponse,
    # Appraisal Template
    AppraisalTemplateCreate,
    AppraisalTemplateUpdate,
    AppraisalTemplateRead,
    AppraisalTemplateListResponse,
    # KRA
    KRACreate,
    KRAUpdate,
    KRARead,
    KRAListResponse,
    # KPI
    KPICreate,
    KPIUpdate,
    KPIRead,
    KPIListResponse,
    # Appraisal
    AppraisalCreate,
    AppraisalUpdate,
    AppraisalRead,
    AppraisalListResponse,
    SelfAssessmentRequest,
    ManagerReviewRequest,
    CalibrationRequest,
    # Scorecard
    ScorecardRead,
    ScorecardListResponse,
)
from app.services.people.perf import PerformanceService
from app.services.common import PaginationParams

router = APIRouter(
    prefix="/perf",
    tags=["performance"],
    dependencies=[Depends(require_tenant_auth)],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def parse_enum(value: Optional[str], enum_type, field_name: str):
    if value is None:
        return None
    try:
        return enum_type(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid {field_name}: {value}"
        ) from exc


# =============================================================================
# Appraisal Cycles
# =============================================================================


@router.get("/cycles", response_model=AppraisalCycleListResponse)
def list_appraisal_cycles(
    organization_id: UUID = Depends(require_organization_id),
    search: Optional[str] = None,
    status: Optional[str] = None,
    year: Optional[int] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List appraisal cycles."""
    svc = PerformanceService(db)
    status_enum = parse_enum(status, AppraisalCycleStatus, "status")
    result = svc.list_appraisal_cycles(
        org_id=organization_id,
        search=search,
        status=status_enum,
        year=year,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return AppraisalCycleListResponse(
        items=[AppraisalCycleRead.model_validate(c) for c in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/cycles", response_model=AppraisalCycleRead, status_code=status.HTTP_201_CREATED
)
def create_appraisal_cycle(
    payload: AppraisalCycleCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create an appraisal cycle."""
    svc = PerformanceService(db)
    cycle = svc.create_cycle(
        org_id=organization_id,
        cycle_code=payload.cycle_code,
        cycle_name=payload.cycle_name,
        description=payload.description,
        review_period_start=payload.review_period_start,
        review_period_end=payload.review_period_end,
        start_date=payload.start_date,
        end_date=payload.end_date,
        self_assessment_deadline=payload.self_assessment_deadline,
        manager_review_deadline=payload.manager_review_deadline,
        calibration_deadline=payload.calibration_deadline,
        include_probation_employees=payload.include_probation_employees,
        min_tenure_months=payload.min_tenure_months,
    )
    db.commit()
    return AppraisalCycleRead.model_validate(cycle)


@router.get("/cycles/{cycle_id}", response_model=AppraisalCycleRead)
def get_appraisal_cycle(
    cycle_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get an appraisal cycle by ID."""
    svc = PerformanceService(db)
    return AppraisalCycleRead.model_validate(svc.get_cycle(organization_id, cycle_id))


@router.patch("/cycles/{cycle_id}", response_model=AppraisalCycleRead)
def update_appraisal_cycle(
    cycle_id: UUID,
    payload: AppraisalCycleUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update an appraisal cycle."""
    svc = PerformanceService(db)
    update_data = payload.model_dump(exclude_unset=True)
    cycle = svc.update_cycle(organization_id, cycle_id, **update_data)
    db.commit()
    return AppraisalCycleRead.model_validate(cycle)


@router.delete("/cycles/{cycle_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appraisal_cycle(
    cycle_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete an appraisal cycle."""
    svc = PerformanceService(db)
    svc.delete_cycle(organization_id, cycle_id)
    db.commit()


# Cycle workflow actions
@router.post("/cycles/{cycle_id}/start", response_model=AppraisalCycleRead)
def start_cycle(
    cycle_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Start an appraisal cycle."""
    svc = PerformanceService(db)
    cycle = svc.start_cycle(organization_id, cycle_id)
    db.commit()
    return AppraisalCycleRead.model_validate(cycle)


@router.post("/cycles/{cycle_id}/close", response_model=AppraisalCycleRead)
def close_cycle(
    cycle_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Close an appraisal cycle."""
    svc = PerformanceService(db)
    cycle = svc.close_cycle(organization_id, cycle_id)
    db.commit()
    return AppraisalCycleRead.model_validate(cycle)


# =============================================================================
# Appraisal Templates
# =============================================================================


@router.get("/templates", response_model=AppraisalTemplateListResponse)
def list_appraisal_templates(
    organization_id: UUID = Depends(require_organization_id),
    search: Optional[str] = None,
    department_id: Optional[UUID] = None,
    designation_id: Optional[UUID] = None,
    is_active: Optional[bool] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List appraisal templates."""
    svc = PerformanceService(db)
    result = svc.list_templates(
        org_id=organization_id,
        search=search,
        department_id=department_id,
        designation_id=designation_id,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return AppraisalTemplateListResponse(
        items=[AppraisalTemplateRead.model_validate(t) for t in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/templates",
    response_model=AppraisalTemplateRead,
    status_code=status.HTTP_201_CREATED,
)
def create_appraisal_template(
    payload: AppraisalTemplateCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create an appraisal template."""
    svc = PerformanceService(db)
    template = svc.create_template(
        org_id=organization_id,
        template_code=payload.template_code,
        template_name=payload.template_name,
        description=payload.description,
        department_id=payload.department_id,
        designation_id=payload.designation_id,
        rating_scale_max=payload.rating_scale_max,
        is_active=payload.is_active,
        kras=[kra.model_dump() for kra in payload.kras],
    )
    db.commit()
    return AppraisalTemplateRead.model_validate(template)


@router.get("/templates/{template_id}", response_model=AppraisalTemplateRead)
def get_appraisal_template(
    template_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get an appraisal template by ID."""
    svc = PerformanceService(db)
    return AppraisalTemplateRead.model_validate(
        svc.get_template(organization_id, template_id)
    )


@router.patch("/templates/{template_id}", response_model=AppraisalTemplateRead)
def update_appraisal_template(
    template_id: UUID,
    payload: AppraisalTemplateUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update an appraisal template."""
    svc = PerformanceService(db)
    update_data = payload.model_dump(exclude_unset=True)
    if "kras" in update_data and update_data["kras"] is not None:
        update_data["kras"] = [kra.model_dump() for kra in update_data["kras"]]
    template = svc.update_template(organization_id, template_id, **update_data)
    db.commit()
    return AppraisalTemplateRead.model_validate(template)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appraisal_template(
    template_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete an appraisal template."""
    svc = PerformanceService(db)
    svc.delete_template(organization_id, template_id)
    db.commit()


# =============================================================================
# Key Result Areas (KRAs)
# =============================================================================


@router.get("/kras", response_model=KRAListResponse)
def list_kras(
    organization_id: UUID = Depends(require_organization_id),
    search: Optional[str] = None,
    department_id: Optional[UUID] = None,
    designation_id: Optional[UUID] = None,
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List Key Result Areas."""
    svc = PerformanceService(db)
    result = svc.list_kras(
        org_id=organization_id,
        search=search,
        department_id=department_id,
        designation_id=designation_id,
        category=category,
        is_active=is_active,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return KRAListResponse(
        items=[KRARead.model_validate(k) for k in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/kras", response_model=KRARead, status_code=status.HTTP_201_CREATED)
def create_kra(
    payload: KRACreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a Key Result Area."""
    svc = PerformanceService(db)
    kra = svc.create_kra(
        org_id=organization_id,
        kra_code=payload.kra_code,
        kra_name=payload.kra_name,
        description=payload.description,
        department_id=payload.department_id,
        designation_id=payload.designation_id,
        default_weightage=payload.default_weightage,
        category=payload.category,
        measurement_criteria=payload.measurement_criteria,
        is_active=payload.is_active,
    )
    db.commit()
    return KRARead.model_validate(kra)


@router.get("/kras/{kra_id}", response_model=KRARead)
def get_kra(
    kra_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a KRA by ID."""
    svc = PerformanceService(db)
    return KRARead.model_validate(svc.get_kra(organization_id, kra_id))


@router.patch("/kras/{kra_id}", response_model=KRARead)
def update_kra(
    kra_id: UUID,
    payload: KRAUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a KRA."""
    svc = PerformanceService(db)
    update_data = payload.model_dump(exclude_unset=True)
    kra = svc.update_kra(organization_id, kra_id, **update_data)
    db.commit()
    return KRARead.model_validate(kra)


@router.delete("/kras/{kra_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_kra(
    kra_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a KRA."""
    svc = PerformanceService(db)
    svc.delete_kra(organization_id, kra_id)
    db.commit()


# =============================================================================
# Key Performance Indicators (KPIs)
# =============================================================================


@router.get("/kpis", response_model=KPIListResponse)
def list_kpis(
    organization_id: UUID = Depends(require_organization_id),
    employee_id: Optional[UUID] = None,
    kra_id: Optional[UUID] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List Key Performance Indicators."""
    svc = PerformanceService(db)
    status_enum = parse_enum(status, KPIStatus, "status")
    result = svc.list_kpis(
        org_id=organization_id,
        employee_id=employee_id,
        kra_id=kra_id,
        status=status_enum,
        search=search,
        is_active=is_active,
        from_date=from_date,
        to_date=to_date,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return KPIListResponse(
        items=[KPIRead.model_validate(k) for k in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post("/kpis", response_model=KPIRead, status_code=status.HTTP_201_CREATED)
def create_kpi(
    payload: KPICreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a Key Performance Indicator."""
    svc = PerformanceService(db)
    kpi = svc.create_kpi(
        org_id=organization_id,
        employee_id=payload.employee_id,
        kra_id=payload.kra_id,
        kpi_name=payload.kpi_name,
        period_start=payload.period_start,
        period_end=payload.period_end,
        target_value=payload.target_value,
        unit_of_measure=payload.unit_of_measure,
        threshold_value=payload.threshold_value,
        stretch_value=payload.stretch_value,
        weightage=payload.weightage,
        notes=payload.notes,
        description=payload.description,
    )
    db.commit()
    return KPIRead.model_validate(kpi)


@router.get("/kpis/{kpi_id}", response_model=KPIRead)
def get_kpi(
    kpi_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a KPI by ID."""
    svc = PerformanceService(db)
    return KPIRead.model_validate(svc.get_kpi(organization_id, kpi_id))


@router.patch("/kpis/{kpi_id}", response_model=KPIRead)
def update_kpi(
    kpi_id: UUID,
    payload: KPIUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a KPI."""
    svc = PerformanceService(db)
    update_data = payload.model_dump(exclude_unset=True)
    kpi = svc.update_kpi(organization_id, kpi_id, **update_data)
    db.commit()
    return KPIRead.model_validate(kpi)


@router.delete("/kpis/{kpi_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_kpi(
    kpi_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a KPI."""
    svc = PerformanceService(db)
    svc.delete_kpi(organization_id, kpi_id)
    db.commit()


# =============================================================================
# Appraisals
# =============================================================================


@router.get("/appraisals", response_model=AppraisalListResponse)
def list_appraisals(
    organization_id: UUID = Depends(require_organization_id),
    cycle_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    manager_id: Optional[UUID] = None,
    status: Optional[str] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List appraisals."""
    svc = PerformanceService(db)
    status_enum = parse_enum(status, AppraisalStatus, "status")
    result = svc.list_appraisals(
        org_id=organization_id,
        cycle_id=cycle_id,
        employee_id=employee_id,
        manager_id=manager_id,
        status=status_enum,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return AppraisalListResponse(
        items=[AppraisalRead.model_validate(a) for a in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.post(
    "/appraisals", response_model=AppraisalRead, status_code=status.HTTP_201_CREATED
)
def create_appraisal(
    payload: AppraisalCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create an appraisal."""
    svc = PerformanceService(db)
    appraisal = svc.create_appraisal(
        org_id=organization_id,
        cycle_id=payload.cycle_id,
        employee_id=payload.employee_id,
        manager_id=payload.manager_id,
        template_id=payload.template_id,
        kra_scores=[score.model_dump() for score in payload.kra_scores],
    )
    db.commit()
    return AppraisalRead.model_validate(appraisal)


@router.get("/appraisals/{appraisal_id}", response_model=AppraisalRead)
def get_appraisal(
    appraisal_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get an appraisal by ID."""
    svc = PerformanceService(db)
    return AppraisalRead.model_validate(
        svc.get_appraisal(organization_id, appraisal_id)
    )


@router.patch("/appraisals/{appraisal_id}", response_model=AppraisalRead)
def update_appraisal(
    appraisal_id: UUID,
    payload: AppraisalUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update an appraisal."""
    svc = PerformanceService(db)
    update_data = payload.model_dump(exclude_unset=True)
    appraisal = svc.update_appraisal(organization_id, appraisal_id, **update_data)
    db.commit()
    return AppraisalRead.model_validate(appraisal)


@router.delete("/appraisals/{appraisal_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appraisal(
    appraisal_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete an appraisal (only draft status)."""
    svc = PerformanceService(db)
    svc.delete_appraisal(organization_id, appraisal_id)
    db.commit()


# =============================================================================
# Appraisal Workflow
# =============================================================================


@router.post("/appraisals/{appraisal_id}/self-assessment", response_model=AppraisalRead)
def submit_self_assessment(
    appraisal_id: UUID,
    payload: SelfAssessmentRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Submit employee self-assessment."""
    svc = PerformanceService(db)
    appraisal = svc.submit_self_assessment(
        org_id=organization_id,
        appraisal_id=appraisal_id,
        self_overall_rating=payload.self_overall_rating,
        self_summary=payload.self_summary,
        achievements=payload.achievements,
        challenges=payload.challenges,
        development_needs=payload.development_needs,
        kra_ratings=[r.model_dump() for r in payload.kra_ratings],
    )
    db.commit()
    return AppraisalRead.model_validate(appraisal)


@router.post("/appraisals/{appraisal_id}/manager-review", response_model=AppraisalRead)
def submit_manager_review(
    appraisal_id: UUID,
    payload: ManagerReviewRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Submit manager review."""
    svc = PerformanceService(db)
    appraisal = svc.submit_manager_review(
        org_id=organization_id,
        appraisal_id=appraisal_id,
        manager_overall_rating=payload.manager_overall_rating,
        manager_summary=payload.manager_summary,
        manager_recommendations=payload.manager_recommendations,
        kra_ratings=[r.model_dump() for r in payload.kra_ratings],
    )
    db.commit()
    return AppraisalRead.model_validate(appraisal)


@router.post("/appraisals/{appraisal_id}/calibration", response_model=AppraisalRead)
def submit_calibration(
    appraisal_id: UUID,
    payload: CalibrationRequest,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Submit calibration review."""
    svc = PerformanceService(db)
    appraisal = svc.submit_calibration(
        org_id=organization_id,
        appraisal_id=appraisal_id,
        calibrated_rating=payload.calibrated_rating,
        calibration_notes=payload.calibration_notes,
        rating_label=payload.rating_label,
    )
    db.commit()
    return AppraisalRead.model_validate(appraisal)


# =============================================================================
# Scorecards
# =============================================================================


@router.get("/scorecards", response_model=ScorecardListResponse)
def list_scorecards(
    organization_id: UUID = Depends(require_organization_id),
    cycle_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List performance scorecards."""
    svc = PerformanceService(db)
    result = svc.list_scorecards(
        org_id=organization_id,
        cycle_id=cycle_id,
        employee_id=employee_id,
        department_id=department_id,
        pagination=PaginationParams(offset=offset, limit=limit),
    )
    return ScorecardListResponse(
        items=[ScorecardRead.model_validate(s) for s in result.items],
        total=result.total,
        offset=offset,
        limit=limit,
    )


@router.get("/scorecards/{scorecard_id}", response_model=ScorecardRead)
def get_scorecard(
    scorecard_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a scorecard by ID."""
    svc = PerformanceService(db)
    return ScorecardRead.model_validate(
        svc.get_scorecard(organization_id, scorecard_id)
    )


@router.post(
    "/appraisals/{appraisal_id}/finalize-scorecard", response_model=ScorecardRead
)
def finalize_scorecard(
    appraisal_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Finalize and generate scorecard for a completed appraisal."""
    svc = PerformanceService(db)
    scorecard = svc.finalize_scorecard(organization_id, appraisal_id)
    db.commit()
    return ScorecardRead.model_validate(scorecard)


# =============================================================================
# Reporting
# =============================================================================


@router.get("/cycles/{cycle_id}/statistics")
def get_cycle_statistics(
    cycle_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get statistics for an appraisal cycle."""
    svc = PerformanceService(db)
    stats = svc.get_cycle_statistics(organization_id, cycle_id)
    return stats


@router.get("/stats")
def get_performance_stats(
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get performance dashboard statistics."""
    svc = PerformanceService(db)
    return svc.get_performance_stats(organization_id)
