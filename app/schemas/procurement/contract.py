"""
Contract Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.procurement.enums import ContractStatus


class ContractCreate(BaseModel):
    """Schema for creating a procurement contract."""

    contract_number: str = Field(max_length=30)
    title: str = Field(max_length=200)
    supplier_id: UUID
    rfq_id: Optional[UUID] = None
    evaluation_id: Optional[UUID] = None
    contract_date: date
    start_date: date
    end_date: date
    contract_value: Decimal = Field(gt=0)
    currency_code: str = Field(default="NGN", max_length=3)
    bpp_clearance_number: Optional[str] = None
    bpp_clearance_date: Optional[date] = None
    payment_terms: Optional[str] = None
    terms_and_conditions: Optional[str] = None
    performance_bond_required: bool = False
    performance_bond_amount: Optional[Decimal] = None
    retention_percentage: Optional[Decimal] = None


class ContractUpdate(BaseModel):
    """Schema for updating a procurement contract."""

    title: Optional[str] = Field(default=None, max_length=200)
    end_date: Optional[date] = None
    bpp_clearance_number: Optional[str] = None
    bpp_clearance_date: Optional[date] = None
    payment_terms: Optional[str] = None
    terms_and_conditions: Optional[str] = None
    performance_bond_amount: Optional[Decimal] = None
    retention_percentage: Optional[Decimal] = None


class ContractResponse(BaseModel):
    """Schema for contract response."""

    model_config = ConfigDict(from_attributes=True)

    contract_id: UUID
    organization_id: UUID
    contract_number: str
    title: str
    supplier_id: UUID
    rfq_id: Optional[UUID] = None
    evaluation_id: Optional[UUID] = None
    purchase_order_id: Optional[UUID] = None
    contract_date: date
    start_date: date
    end_date: date
    contract_value: Decimal
    currency_code: str
    status: ContractStatus
    bpp_clearance_number: Optional[str] = None
    bpp_clearance_date: Optional[date] = None
    payment_terms: Optional[str] = None
    terms_and_conditions: Optional[str] = None
    performance_bond_required: bool
    performance_bond_amount: Optional[Decimal] = None
    retention_percentage: Optional[Decimal] = None
    amount_paid: Decimal
    completion_date: Optional[date] = None
    completion_certificate_issued: bool
    created_by_user_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None
