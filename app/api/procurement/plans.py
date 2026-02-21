"""
Procurement Plan API Endpoints.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.schemas.procurement.procurement_plan import (
    ProcurementPlanCreate,
    ProcurementPlanResponse,
    ProcurementPlanUpdate,
)
from app.services.common import NotFoundError, ValidationError
from app.services.procurement.procurement_plan import ProcurementPlanService

router = APIRouter(prefix="/plans", tags=["procurement-plans"])


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("", response_model=list[ProcurementPlanResponse])
def list_plans(
    organization_id: UUID = Depends(require_organization_id),
    status_filter: str | None = Query(None, alias="status"),
    fiscal_year: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List procurement plans."""
    service = ProcurementPlanService(db)
    plans, _ = service.list_plans(
        organization_id,
        status=status_filter,
        fiscal_year=fiscal_year,
        offset=offset,
        limit=limit,
    )
    return [ProcurementPlanResponse.model_validate(p) for p in plans]


@router.get("/{plan_id}", response_model=ProcurementPlanResponse)
def get_plan(
    plan_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a procurement plan by ID."""
    service = ProcurementPlanService(db)
    plan = service.get_by_id(organization_id, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return ProcurementPlanResponse.model_validate(plan)


@router.post(
    "", response_model=ProcurementPlanResponse, status_code=status.HTTP_201_CREATED
)
def create_plan(
    data: ProcurementPlanCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Create a new procurement plan."""
    service = ProcurementPlanService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    try:
        plan = service.create(organization_id, data, user_id)
        return ProcurementPlanResponse.model_validate(plan)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{plan_id}", response_model=ProcurementPlanResponse)
def update_plan(
    plan_id: UUID,
    data: ProcurementPlanUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a procurement plan."""
    service = ProcurementPlanService(db)
    try:
        plan = service.update(organization_id, plan_id, data)
        return ProcurementPlanResponse.model_validate(plan)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{plan_id}/submit", response_model=ProcurementPlanResponse)
def submit_plan(
    plan_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Submit a plan for approval."""
    service = ProcurementPlanService(db)
    try:
        plan = service.submit(organization_id, plan_id)
        return ProcurementPlanResponse.model_validate(plan)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{plan_id}/approve", response_model=ProcurementPlanResponse)
def approve_plan(
    plan_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Approve a procurement plan."""
    service = ProcurementPlanService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    try:
        plan = service.approve(organization_id, plan_id, user_id)
        return ProcurementPlanResponse.model_validate(plan)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
