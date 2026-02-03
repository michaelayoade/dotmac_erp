"""
Quotation Response Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.procurement.enums import QuotationResponseStatus


class QuotationLineCreate(BaseModel):
    """Schema for creating a quotation line."""

    requisition_line_id: Optional[UUID] = None
    line_number: int = Field(ge=1)
    description: str
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    line_amount: Decimal = Field(ge=0)
    delivery_date: Optional[date] = None


class QuotationLineSchema(BaseModel):
    """Schema for quotation line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    response_id: UUID
    requisition_line_id: Optional[UUID] = None
    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_amount: Decimal
    delivery_date: Optional[date] = None


class QuotationResponseCreate(BaseModel):
    """Schema for creating a quotation response."""

    rfq_id: UUID
    supplier_id: UUID
    response_number: str = Field(max_length=30)
    response_date: date
    total_amount: Decimal = Field(ge=0)
    currency_code: str = Field(default="NGN", max_length=3)
    delivery_period_days: Optional[int] = None
    validity_days: Optional[int] = None
    technical_proposal: Optional[str] = None
    notes: Optional[str] = None
    lines: List[QuotationLineCreate] = Field(default_factory=list)


class QuotationResponseUpdate(BaseModel):
    """Schema for updating a quotation response."""

    total_amount: Optional[Decimal] = None
    delivery_period_days: Optional[int] = None
    validity_days: Optional[int] = None
    technical_proposal: Optional[str] = None
    notes: Optional[str] = None


class QuotationResponseSchema(BaseModel):
    """Schema for quotation response."""

    model_config = ConfigDict(from_attributes=True)

    response_id: UUID
    rfq_id: UUID
    organization_id: UUID
    supplier_id: UUID
    response_number: str
    response_date: date
    total_amount: Decimal
    currency_code: str
    delivery_period_days: Optional[int] = None
    validity_days: Optional[int] = None
    technical_proposal: Optional[str] = None
    notes: Optional[str] = None
    status: QuotationResponseStatus
    received_at: datetime
    created_at: datetime
    updated_at: Optional[datetime] = None
    lines: List[QuotationLineSchema] = Field(default_factory=list)
