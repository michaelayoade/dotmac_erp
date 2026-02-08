"""
Vendor Prequalification Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.procurement.enums import PrequalificationStatus


class PrequalificationCreate(BaseModel):
    """Schema for creating a prequalification record."""

    supplier_id: UUID
    application_date: date
    categories: list[dict[str, Any]] | None = None
    documents_verified: bool = False
    tax_clearance_valid: bool = False
    pension_compliance: bool = False
    itf_compliance: bool = False
    nsitf_compliance: bool = False


class PrequalificationUpdate(BaseModel):
    """Schema for updating a prequalification record."""

    categories: list[dict[str, Any]] | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    documents_verified: bool | None = None
    tax_clearance_valid: bool | None = None
    pension_compliance: bool | None = None
    itf_compliance: bool | None = None
    nsitf_compliance: bool | None = None
    financial_capability_score: Decimal | None = None
    technical_capability_score: Decimal | None = None
    overall_score: Decimal | None = None
    review_notes: str | None = None


class PrequalificationResponse(BaseModel):
    """Schema for prequalification response."""

    model_config = ConfigDict(from_attributes=True)

    prequalification_id: UUID
    organization_id: UUID
    supplier_id: UUID
    application_date: date
    status: PrequalificationStatus
    categories: list[dict[str, Any]] | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    documents_verified: bool
    tax_clearance_valid: bool
    pension_compliance: bool
    itf_compliance: bool
    nsitf_compliance: bool
    financial_capability_score: Decimal | None = None
    technical_capability_score: Decimal | None = None
    overall_score: Decimal | None = None
    review_notes: str | None = None
    reviewed_by_user_id: UUID | None = None
    reviewed_at: datetime | None = None
    blacklisted: bool
    blacklist_reason: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
