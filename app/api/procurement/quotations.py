"""
Quotation Response API Endpoints.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.schemas.procurement.quotation import (
    QuotationResponseCreate,
    QuotationResponseSchema,
    QuotationResponseUpdate,
)
from app.services.common import NotFoundError
from app.services.procurement.quotation import QuotationResponseService

router = APIRouter(prefix="/quotations", tags=["procurement-quotations"])


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


@router.get("/rfq/{rfq_id}", response_model=list[QuotationResponseSchema])
def list_for_rfq(
    rfq_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """List quotation responses for an RFQ."""
    service = QuotationResponseService(db)
    responses = service.list_for_rfq(organization_id, rfq_id)
    return [QuotationResponseSchema.model_validate(r) for r in responses]


@router.get("/{response_id}", response_model=QuotationResponseSchema)
def get_quotation(
    response_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get a quotation response by ID."""
    service = QuotationResponseService(db)
    response = service.get_by_id(organization_id, response_id)
    if not response:
        raise HTTPException(status_code=404, detail="Quotation response not found")
    return QuotationResponseSchema.model_validate(response)


@router.post(
    "", response_model=QuotationResponseSchema, status_code=status.HTTP_201_CREATED
)
def create_quotation(
    data: QuotationResponseCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Record a vendor quotation response."""
    service = QuotationResponseService(db)
    response = service.create(organization_id, data)
    return QuotationResponseSchema.model_validate(response)


@router.patch("/{response_id}", response_model=QuotationResponseSchema)
def update_quotation(
    response_id: UUID,
    data: QuotationResponseUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a quotation response."""
    service = QuotationResponseService(db)
    try:
        response = service.update(organization_id, response_id, data)
        return QuotationResponseSchema.model_validate(response)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
