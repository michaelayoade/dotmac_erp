"""
Vehicle Document Pydantic Schemas.

Schemas for document API endpoints.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.fleet.enums import DocumentType


class DocumentBase(BaseModel):
    """Base document schema."""

    vehicle_id: UUID
    document_type: DocumentType
    document_number: str | None = Field(default=None, max_length=50)
    description: str = Field(max_length=200)
    issue_date: date | None = None
    expiry_date: date | None = None
    provider_name: str | None = Field(default=None, max_length=100)
    policy_number: str | None = Field(default=None, max_length=50)
    coverage_amount: Decimal | None = Field(default=None, ge=0)
    premium_amount: Decimal | None = Field(default=None, ge=0)
    reminder_days_before: int = Field(default=30, ge=0, le=365)
    notes: str | None = None


class DocumentCreate(DocumentBase):
    """Create document request."""

    pass


class DocumentUpdate(BaseModel):
    """Update document request."""

    document_type: DocumentType | None = None
    document_number: str | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=200)
    issue_date: date | None = None
    expiry_date: date | None = None
    provider_name: str | None = Field(default=None, max_length=100)
    policy_number: str | None = Field(default=None, max_length=50)
    coverage_amount: Decimal | None = Field(default=None, ge=0)
    premium_amount: Decimal | None = Field(default=None, ge=0)
    reminder_days_before: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = None


class DocumentRead(DocumentBase):
    """Document response."""

    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    organization_id: UUID
    file_path: str | None = None
    file_name: str | None = None
    reminder_sent: bool = False
    created_at: datetime
    updated_at: datetime | None = None


class DocumentBrief(BaseModel):
    """Brief document summary for lists."""

    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    vehicle_id: UUID
    document_type: DocumentType
    description: str
    expiry_date: date | None = None
    reminder_sent: bool = False


class DocumentWithStatus(DocumentRead):
    """Document with computed status."""

    model_config = ConfigDict(from_attributes=True)

    is_expired: bool = False
    expires_soon: bool = False
    days_until_expiry: int | None = None
    status_label: str = "Valid"


class DocumentListResponse(BaseModel):
    """Paginated document list response."""

    items: list[DocumentBrief]
    total: int
    offset: int
    limit: int
