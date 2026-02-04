"""
RFQ API Endpoints.
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.db import SessionLocal
from app.schemas.procurement.rfq import (
    RFQCreate,
    RFQInvitationCreate,
    RFQInvitationResponse,
    RFQResponse,
    RFQUpdate,
)
from app.services.common import NotFoundError, ValidationError
from app.services.procurement.rfq import RFQService

router = APIRouter(prefix="/rfqs", tags=["procurement-rfqs"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=List[RFQResponse])
def list_rfqs(
    organization_id: UUID = Depends(require_organization_id),
    status_filter: Optional[str] = Query(None, alias="status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List RFQs."""
    service = RFQService(db)
    rfqs, _ = service.list_rfqs(
        organization_id,
        status=status_filter,
        offset=offset,
        limit=limit,
    )
    return [RFQResponse.model_validate(r) for r in rfqs]


@router.get("/{rfq_id}", response_model=RFQResponse)
def get_rfq(
    rfq_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get an RFQ by ID."""
    service = RFQService(db)
    rfq = service.get_by_id(organization_id, rfq_id)
    if not rfq:
        raise HTTPException(status_code=404, detail="RFQ not found")
    return RFQResponse.model_validate(rfq)


@router.post("", response_model=RFQResponse, status_code=status.HTTP_201_CREATED)
def create_rfq(
    data: RFQCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(get_db),
):
    """Create a new RFQ."""
    service = RFQService(db)
    person_id = auth.get("person_id")
    if not person_id:
        raise HTTPException(status_code=400, detail="Missing person_id")
    user_id = UUID(person_id)
    try:
        rfq = service.create(organization_id, data, user_id)
        db.commit()
        return RFQResponse.model_validate(rfq)
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch("/{rfq_id}", response_model=RFQResponse)
def update_rfq(
    rfq_id: UUID,
    data: RFQUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update an RFQ."""
    service = RFQService(db)
    try:
        rfq = service.update(organization_id, rfq_id, data)
        db.commit()
        return RFQResponse.model_validate(rfq)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{rfq_id}/publish", response_model=RFQResponse)
def publish_rfq(
    rfq_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Publish an RFQ."""
    service = RFQService(db)
    try:
        rfq = service.publish(organization_id, rfq_id)
        db.commit()
        return RFQResponse.model_validate(rfq)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{rfq_id}/close", response_model=RFQResponse)
def close_rfq(
    rfq_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Close bidding on an RFQ."""
    service = RFQService(db)
    try:
        rfq = service.close_bidding(organization_id, rfq_id)
        db.commit()
        return RFQResponse.model_validate(rfq)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{rfq_id}/invite", response_model=RFQInvitationResponse)
def invite_vendor(
    rfq_id: UUID,
    data: RFQInvitationCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Invite a vendor to an RFQ."""
    service = RFQService(db)
    try:
        invitation = service.invite_vendor(organization_id, rfq_id, data.supplier_id)
        db.commit()
        return RFQInvitationResponse.model_validate(invitation)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
