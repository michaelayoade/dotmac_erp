"""
Contract Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.procurement.enums import ContractStatus


class ContractCreate(BaseModel):
    """Schema for creating a procurement contract."""

    contract_number: str = Field(max_length=30)
    title: str = Field(max_length=200)
    supplier_id: UUID
    rfq_id: UUID | None = None
    evaluation_id: UUID | None = None
    contract_date: date
    start_date: date
    end_date: date
    contract_value: Decimal = Field(gt=0)
    currency_code: str = Field(default="NGN", max_length=3)
    bpp_clearance_number: str | None = None
    bpp_clearance_date: date | None = None
    payment_terms: str | None = None
    terms_and_conditions: str | None = None
    performance_bond_required: bool = False
    performance_bond_amount: Decimal | None = None
    retention_percentage: Decimal | None = None


class ContractUpdate(BaseModel):
    """Schema for updating a procurement contract."""

    title: str | None = Field(default=None, max_length=200)
    end_date: date | None = None
    bpp_clearance_number: str | None = None
    bpp_clearance_date: date | None = None
    payment_terms: str | None = None
    terms_and_conditions: str | None = None
    performance_bond_amount: Decimal | None = None
    retention_percentage: Decimal | None = None


class ContractResponse(BaseModel):
    """Schema for contract response."""

    model_config = ConfigDict(from_attributes=True)

    contract_id: UUID
    organization_id: UUID
    contract_number: str
    title: str
    supplier_id: UUID
    rfq_id: UUID | None = None
    evaluation_id: UUID | None = None
    purchase_order_id: UUID | None = None
    contract_date: date
    start_date: date
    end_date: date
    contract_value: Decimal
    currency_code: str
    status: ContractStatus
    bpp_clearance_number: str | None = None
    bpp_clearance_date: date | None = None
    payment_terms: str | None = None
    terms_and_conditions: str | None = None
    performance_bond_required: bool
    performance_bond_amount: Decimal | None = None
    retention_percentage: Decimal | None = None
    amount_paid: Decimal
    completion_date: date | None = None
    completion_certificate_issued: bool
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime | None = None
