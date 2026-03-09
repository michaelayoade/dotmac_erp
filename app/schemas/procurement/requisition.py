"""
Requisition Schemas.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.models.procurement.enums import RequisitionStatus, UrgencyLevel


class RequisitionLineCreate(BaseModel):
    """Schema for creating a requisition line."""

    line_number: int = Field(ge=1)
    item_id: UUID | None = None
    description: str
    quantity: Decimal = Field(gt=0)
    uom: str | None = None
    estimated_unit_price: Decimal = Field(ge=0)
    estimated_amount: Decimal = Field(ge=0)
    expense_account_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None
    delivery_date: date | None = None


class RequisitionLineResponse(BaseModel):
    """Schema for requisition line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    requisition_id: UUID
    line_number: int
    item_id: UUID | None = None
    description: str
    quantity: Decimal
    uom: str | None = None
    estimated_unit_price: Decimal
    estimated_amount: Decimal
    expense_account_id: UUID | None = None
    cost_center_id: UUID | None = None
    project_id: UUID | None = None
    delivery_date: date | None = None


class RequisitionCreate(BaseModel):
    """Schema for creating a requisition."""

    requisition_number: str = Field(max_length=30)
    requisition_date: date
    requester_id: UUID
    department_id: UUID | None = None
    urgency: UrgencyLevel = UrgencyLevel.NORMAL
    justification: str | None = None
    currency_code: str = Field(
        default=settings.default_functional_currency_code, max_length=3
    )
    material_request_id: UUID | None = None
    plan_item_id: UUID | None = None
    lines: list[RequisitionLineCreate] = Field(default_factory=list)


class RequisitionUpdate(BaseModel):
    """Schema for updating a requisition."""

    urgency: UrgencyLevel | None = None
    justification: str | None = None
    department_id: UUID | None = None


class RequisitionResponse(BaseModel):
    """Schema for requisition response."""

    model_config = ConfigDict(from_attributes=True)

    requisition_id: UUID
    organization_id: UUID
    requisition_number: str
    requisition_date: date
    requester_id: UUID
    department_id: UUID | None = None
    status: RequisitionStatus
    urgency: UrgencyLevel
    justification: str | None = None
    total_estimated_amount: Decimal
    currency_code: str
    budget_verified: bool
    budget_verified_by_id: UUID | None = None
    budget_verified_at: datetime | None = None
    material_request_id: UUID | None = None
    plan_item_id: UUID | None = None
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime | None = None
    lines: list[RequisitionLineResponse] = Field(default_factory=list)
