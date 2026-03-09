"""
RFQ Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
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
    response_date: datetime | None = None


class RFQCreate(BaseModel):
    """Schema for creating an RFQ."""

    rfq_number: str = Field(max_length=30)
    title: str = Field(max_length=200)
    rfq_date: date
    closing_date: date
    procurement_method: ProcurementMethod = ProcurementMethod.OPEN_COMPETITIVE
    requisition_id: UUID | None = None
    plan_item_id: UUID | None = None
    evaluation_criteria: list[dict[str, Any]] | None = None
    terms_and_conditions: str | None = None
    estimated_value: Decimal | None = None
    currency_code: str = Field(
        default=settings.default_functional_currency_code, max_length=3
    )


class RFQUpdate(BaseModel):
    """Schema for updating an RFQ."""

    title: str | None = Field(default=None, max_length=200)
    closing_date: date | None = None
    evaluation_criteria: list[dict[str, Any]] | None = None
    terms_and_conditions: str | None = None
    estimated_value: Decimal | None = None


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
    requisition_id: UUID | None = None
    plan_item_id: UUID | None = None
    evaluation_criteria: list[dict[str, Any]] | None = None
    terms_and_conditions: str | None = None
    estimated_value: Decimal | None = None
    currency_code: str
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime | None = None
    invitations: list[RFQInvitationResponse] = Field(default_factory=list)
