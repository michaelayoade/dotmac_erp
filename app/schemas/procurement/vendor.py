"""
Vendor Prequalification Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.models.procurement.enums import PrequalificationStatus


class PrequalificationCreate(BaseModel):
    """Schema for creating a prequalification record."""

    supplier_id: UUID
    application_date: date
    categories: Optional[List[Dict[str, Any]]] = None
    documents_verified: bool = False
    tax_clearance_valid: bool = False
    pension_compliance: bool = False
    itf_compliance: bool = False
    nsitf_compliance: bool = False


class PrequalificationUpdate(BaseModel):
    """Schema for updating a prequalification record."""

    categories: Optional[List[Dict[str, Any]]] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    documents_verified: Optional[bool] = None
    tax_clearance_valid: Optional[bool] = None
    pension_compliance: Optional[bool] = None
    itf_compliance: Optional[bool] = None
    nsitf_compliance: Optional[bool] = None
    financial_capability_score: Optional[Decimal] = None
    technical_capability_score: Optional[Decimal] = None
    overall_score: Optional[Decimal] = None
    review_notes: Optional[str] = None


class PrequalificationResponse(BaseModel):
    """Schema for prequalification response."""

    model_config = ConfigDict(from_attributes=True)

    prequalification_id: UUID
    organization_id: UUID
    supplier_id: UUID
    application_date: date
    status: PrequalificationStatus
    categories: Optional[List[Dict[str, Any]]] = None
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    documents_verified: bool
    tax_clearance_valid: bool
    pension_compliance: bool
    itf_compliance: bool
    nsitf_compliance: bool
    financial_capability_score: Optional[Decimal] = None
    technical_capability_score: Optional[Decimal] = None
    overall_score: Optional[Decimal] = None
    review_notes: Optional[str] = None
    reviewed_by_user_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    blacklisted: bool
    blacklist_reason: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
