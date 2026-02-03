"""
Document API Endpoints.

REST API for vehicle document management.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id
from app.db import SessionLocal
from app.models.fleet.enums import DocumentType
from app.schemas.fleet.document import (
    DocumentBrief,
    DocumentCreate,
    DocumentListResponse,
    DocumentRead,
    DocumentUpdate,
)
from app.services.common import NotFoundError, PaginationParams
from app.services.fleet.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["fleet-documents"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("", response_model=DocumentListResponse)
def list_documents(
    organization_id: UUID = Depends(require_organization_id),
    vehicle_id: Optional[UUID] = None,
    document_type: Optional[str] = None,
    expired_only: bool = False,
    expiring_soon: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List documents with optional filtering."""
    type_enum = DocumentType(document_type) if document_type else None

    service = DocumentService(db, organization_id)
    result = service.list_documents(
        vehicle_id=vehicle_id,
        document_type=type_enum,
        expired_only=expired_only,
        expiring_soon=expiring_soon,
        params=PaginationParams(offset=offset, limit=limit),
    )

    return DocumentListResponse(
        items=[DocumentBrief.model_validate(d) for d in result.items],
        total=result.total,
        offset=result.offset,
        limit=result.limit,
    )


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Get document details."""
    service = DocumentService(db, organization_id)
    try:
        doc = service.get_or_raise(document_id)
        return DocumentRead.model_validate(doc)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def create_document(
    data: DocumentCreate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Create a new document record."""
    service = DocumentService(db, organization_id)
    try:
        doc = service.create(data)
        db.commit()
        return DocumentRead.model_validate(doc)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: UUID,
    data: DocumentUpdate,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Update a document record."""
    service = DocumentService(db, organization_id)
    try:
        doc = service.update(document_id, data)
        db.commit()
        return DocumentRead.model_validate(doc)
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db),
):
    """Delete a document record."""
    service = DocumentService(db, organization_id)
    try:
        service.delete(document_id)
        db.commit()
    except NotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
