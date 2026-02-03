"""
RFQ Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.procurement.enums import ProcurementMethod, RFQStatus


class RFQInvitationCreate(BaseModel):
    """Schema for inviting a vendor to an RFQ."""

    supplier_id: UUID


class RFQInvitationResponse(BaseModel):
    """Schema for RFQ invitation response."""

    model_config = ConfigDict(from_attributes=True)

    invitation_id: UUID
    rfq_id: UUID
    supplier_id: UUID
    invited_at: datetime
    responded: bool
    response_date: Optional[datetime] = None


class RFQCreate(BaseModel):
    """Schema for creating an RFQ."""

    rfq_number: str = Field(max_length=30)
    title: str = Field(max_length=200)
    rfq_date: date
    closing_date: date
    procurement_method: ProcurementMethod = ProcurementMethod.OPEN_COMPETITIVE
    requisition_id: Optional[UUID] = None
    plan_item_id: Optional[UUID] = None
    evaluation_criteria: Optional[List[Dict[str, Any]]] = None
    terms_and_conditions: Optional[str] = None
    estimated_value: Optional[Decimal] = None
    currency_code: str = Field(default="NGN", max_length=3)


class RFQUpdate(BaseModel):
    """Schema for updating an RFQ."""

    title: Optional[str] = Field(default=None, max_length=200)
    closing_date: Optional[date] = None
    evaluation_criteria: Optional[List[Dict[str, Any]]] = None
    terms_and_conditions: Optional[str] = None
    estimated_value: Optional[Decimal] = None


class RFQResponse(BaseModel):
    """Schema for RFQ response."""

    model_config = ConfigDict(from_attributes=True)

    rfq_id: UUID
    organization_id: UUID
    rfq_number: str
    title: str
    rfq_date: date
    closing_date: date
    status: RFQStatus
    procurement_method: ProcurementMethod
    requisition_id: Optional[UUID] = None
    plan_item_id: Optional[UUID] = None
    evaluation_criteria: Optional[List[Dict[str, Any]]] = None
    terms_and_conditions: Optional[str] = None
    estimated_value: Optional[Decimal] = None
    currency_code: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None
    invitations: List[RFQInvitationResponse] = Field(default_factory=list)
