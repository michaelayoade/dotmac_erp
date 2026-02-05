"""
Requisition API Endpoints.
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.schemas.procurement.requisition import (
    RequisitionCreate,
    RequisitionResponse,
    RequisitionUpdate,
)
from app.services.common import NotFoundError, ValidationError
from app.services.procurement.requisition import RequisitionService

router = APIRouter(prefix="/requisitions", tags=["procurement-requisitions"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=List[RequisitionResponse])
def list_requisitions(
    organization_id: UUID = Depends(require_organization_id),
    status_filter: Optional[str] = Query(None, alias="status"),
    urgency_filter: Optional[str] = Query(None, alias="urgency"),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List requisitions."""
    service = RequisitionService(db)
    reqs, _ = service.list_requisitions(
        organization_id,
        status=status_filter,
        urgency=urgency_filter,
        offset=offset,
        limit=limit,
    )
    return [RequisitionResponse.model_validate(r) for r in reqs]


@router.get("/{requisition_id}", response_model=RequisitionResponse)
def get_requisition(
    requisition_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a requisition by ID."""
    service = RequisitionService(db)
    req = service.get_by_id(organization_id, requisition_id)
    if not req:
        raise HTTPException(status_code=404, detail="Requisition not found")
    return RequisitionResponse.model_validate(req)


@router.post(
    "", response_model=RequisitionResponse, status_code=status.HTTP_201_CREATED
)
def create_requisition(
    data: RequisitionCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Create a new requisition."""
    service = RequisitionService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    try:
        req = service.create(organization_id, data, user_id)
        db.commit()
        return RequisitionResponse.model_validate(req)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{requisition_id}", response_model=RequisitionResponse)
def update_requisition(
    requisition_id: UUID,
    data: RequisitionUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a requisition."""
    service = RequisitionService(db)
    try:
        req = service.update(organization_id, requisition_id, data)
        db.commit()
        return RequisitionResponse.model_validate(req)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{requisition_id}/submit", response_model=RequisitionResponse)
def submit_requisition(
    requisition_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Submit a requisition."""
    service = RequisitionService(db)
    try:
        req = service.submit(organization_id, requisition_id)
        db.commit()
        return RequisitionResponse.model_validate(req)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{requisition_id}/verify-budget", response_model=RequisitionResponse)
def verify_budget(
    requisition_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Verify budget for a requisition."""
    service = RequisitionService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    try:
        req = service.verify_budget(organization_id, requisition_id, user_id)
        db.commit()
        return RequisitionResponse.model_validate(req)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{requisition_id}/approve", response_model=RequisitionResponse)
def approve_requisition(
    requisition_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Approve a requisition."""
    service = RequisitionService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    try:
        req = service.approve(organization_id, requisition_id, user_id)
        db.commit()
        return RequisitionResponse.model_validate(req)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{requisition_id}/reject", response_model=RequisitionResponse)
def reject_requisition(
    requisition_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Reject a requisition."""
    service = RequisitionService(db)
    try:
        req = service.reject(organization_id, requisition_id)
        db.commit()
        return RequisitionResponse.model_validate(req)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
