"""
Vendor Prequalification API Endpoints.
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.schemas.procurement.vendor import (
    PrequalificationCreate,
    PrequalificationResponse,
    PrequalificationUpdate,
)
from app.services.common import NotFoundError
from app.services.procurement.vendor import VendorPrequalificationService

router = APIRouter(prefix="/vendors", tags=["procurement-vendors"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/prequalifications", response_model=List[PrequalificationResponse])
def list_prequalifications(
    organization_id: UUID = Depends(require_organization_id),
    status_filter: Optional[str] = Query(None, alias="status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List vendor prequalifications."""
    service = VendorPrequalificationService(db)
    preqs, _ = service.list_prequalifications(
        organization_id,
        status=status_filter,
        offset=offset,
        limit=limit,
    )
    return [PrequalificationResponse.model_validate(p) for p in preqs]


@router.get(
    "/prequalifications/{prequalification_id}", response_model=PrequalificationResponse
)
def get_prequalification(
    prequalification_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a prequalification by ID."""
    service = VendorPrequalificationService(db)
    preq = service.get_by_id(organization_id, prequalification_id)
    if not preq:
        raise HTTPException(status_code=404, detail="Prequalification not found")
    return PrequalificationResponse.model_validate(preq)


@router.post(
    "/prequalifications",
    response_model=PrequalificationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_prequalification(
    data: PrequalificationCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a prequalification record."""
    service = VendorPrequalificationService(db)
    preq = service.create(organization_id, data)
    db.commit()
    return PrequalificationResponse.model_validate(preq)


@router.patch(
    "/prequalifications/{prequalification_id}",
    response_model=PrequalificationResponse,
)
def update_prequalification(
    prequalification_id: UUID,
    data: PrequalificationUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a prequalification record."""
    service = VendorPrequalificationService(db)
    try:
        preq = service.update(organization_id, prequalification_id, data)
        db.commit()
        return PrequalificationResponse.model_validate(preq)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/prequalifications/{prequalification_id}/qualify",
    response_model=PrequalificationResponse,
)
def qualify_vendor(
    prequalification_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Qualify a vendor."""
    service = VendorPrequalificationService(db)
    try:
        preq = service.qualify(organization_id, prequalification_id, organization_id)
        db.commit()
        return PrequalificationResponse.model_validate(preq)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/prequalifications/{prequalification_id}/disqualify",
    response_model=PrequalificationResponse,
)
def disqualify_vendor(
    prequalification_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Disqualify a vendor."""
    service = VendorPrequalificationService(db)
    try:
        preq = service.disqualify(organization_id, prequalification_id, organization_id)
        db.commit()
        return PrequalificationResponse.model_validate(preq)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
