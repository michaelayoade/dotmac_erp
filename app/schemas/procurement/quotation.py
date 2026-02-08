"""
Quotation Response Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.procurement.enums import QuotationResponseStatus


class QuotationLineCreate(BaseModel):
    """Schema for creating a quotation line."""

    requisition_line_id: UUID | None = None
    line_number: int = Field(ge=1)
    description: str
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    line_amount: Decimal = Field(ge=0)
    delivery_date: date | None = None


class QuotationLineSchema(BaseModel):
    """Schema for quotation line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    response_id: UUID
    requisition_line_id: UUID | None = None
    line_number: int
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_amount: Decimal
    delivery_date: date | None = None


class QuotationResponseCreate(BaseModel):
    """Schema for creating a quotation response."""

    rfq_id: UUID
    supplier_id: UUID
    response_number: str = Field(max_length=30)
    response_date: date
    total_amount: Decimal = Field(ge=0)
    currency_code: str = Field(default="NGN", max_length=3)
    delivery_period_days: int | None = None
    validity_days: int | None = None
    technical_proposal: str | None = None
    notes: str | None = None
    lines: list[QuotationLineCreate] = Field(default_factory=list)


class QuotationResponseUpdate(BaseModel):
    """Schema for updating a quotation response."""

    total_amount: Decimal | None = None
    delivery_period_days: int | None = None
    validity_days: int | None = None
    technical_proposal: str | None = None
    notes: str | None = None


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
    delivery_period_days: int | None = None
    validity_days: int | None = None
    technical_proposal: str | None = None
    notes: str | None = None
    status: QuotationResponseStatus
    received_at: datetime
    created_at: datetime
    updated_at: datetime | None = None
    lines: list[QuotationLineSchema] = Field(default_factory=list)
