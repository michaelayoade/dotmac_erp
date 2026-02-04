"""
Requisition Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.procurement.enums import RequisitionStatus, UrgencyLevel


class RequisitionLineCreate(BaseModel):
    """Schema for creating a requisition line."""

    line_number: int = Field(ge=1)
    item_id: Optional[UUID] = None
    description: str
    quantity: Decimal = Field(gt=0)
    uom: Optional[str] = None
    estimated_unit_price: Decimal = Field(ge=0)
    estimated_amount: Decimal = Field(ge=0)
    expense_account_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    delivery_date: Optional[date] = None


class RequisitionLineResponse(BaseModel):
    """Schema for requisition line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    requisition_id: UUID
    line_number: int
    item_id: Optional[UUID] = None
    description: str
    quantity: Decimal
    uom: Optional[str] = None
    estimated_unit_price: Decimal
    estimated_amount: Decimal
    expense_account_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    delivery_date: Optional[date] = None


class RequisitionCreate(BaseModel):
    """Schema for creating a requisition."""

    requisition_number: str = Field(max_length=30)
    requisition_date: date
    requester_id: UUID
    department_id: Optional[UUID] = None
    urgency: UrgencyLevel = UrgencyLevel.NORMAL
    justification: Optional[str] = None
    currency_code: str = Field(default="NGN", max_length=3)
    material_request_id: Optional[UUID] = None
    plan_item_id: Optional[UUID] = None
    lines: List[RequisitionLineCreate] = Field(default_factory=list)


class RequisitionUpdate(BaseModel):
    """Schema for updating a requisition."""

    urgency: Optional[UrgencyLevel] = None
    justification: Optional[str] = None
    department_id: Optional[UUID] = None


class RequisitionResponse(BaseModel):
    """Schema for requisition response."""

    model_config = ConfigDict(from_attributes=True)

    requisition_id: UUID
    organization_id: UUID
    requisition_number: str
    requisition_date: date
    requester_id: UUID
    department_id: Optional[UUID] = None
    status: RequisitionStatus
    urgency: UrgencyLevel
    justification: Optional[str] = None
    total_estimated_amount: Decimal
    currency_code: str
    budget_verified: bool
    budget_verified_by_id: Optional[UUID] = None
    budget_verified_at: Optional[datetime] = None
    material_request_id: Optional[UUID] = None
    plan_item_id: Optional[UUID] = None
    approved_by_user_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None
    lines: List[RequisitionLineResponse] = Field(default_factory=list)
