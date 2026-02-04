"""
Procurement Plan Schemas.
"""

from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.procurement.enums import (
    PlanItemStatus,
    ProcurementMethod,
    ProcurementPlanStatus,
)


class PlanItemCreate(BaseModel):
    """Schema for creating a plan line item."""

    line_number: int = Field(ge=1)
    description: str
    budget_line_code: Optional[str] = None
    budget_id: Optional[UUID] = None
    estimated_value: Decimal = Field(ge=0)
    procurement_method: ProcurementMethod = ProcurementMethod.OPEN_COMPETITIVE
    planned_quarter: int = Field(ge=1, le=4)
    category: Optional[str] = None


class PlanItemResponse(BaseModel):
    """Schema for plan item response."""

    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    plan_id: UUID
    organization_id: UUID
    line_number: int
    description: str
    budget_line_code: Optional[str] = None
    budget_id: Optional[UUID] = None
    estimated_value: Decimal
    procurement_method: ProcurementMethod
    planned_quarter: int
    approving_authority: Optional[str] = None
    category: Optional[str] = None
    status: PlanItemStatus


class ProcurementPlanCreate(BaseModel):
    """Schema for creating a procurement plan."""

    plan_number: str = Field(max_length=30)
    fiscal_year: str = Field(max_length=10)
    title: str = Field(max_length=200)
    currency_code: str = Field(default="NGN", max_length=3)
    items: List[PlanItemCreate] = Field(default_factory=list)


class ProcurementPlanUpdate(BaseModel):
    """Schema for updating a procurement plan."""

    title: Optional[str] = Field(default=None, max_length=200)
    fiscal_year: Optional[str] = Field(default=None, max_length=10)
    currency_code: Optional[str] = Field(default=None, max_length=3)


class ProcurementPlanResponse(BaseModel):
    """Schema for procurement plan response."""

    model_config = ConfigDict(from_attributes=True)

    plan_id: UUID
    organization_id: UUID
    plan_number: str
    fiscal_year: str
    title: str
    status: ProcurementPlanStatus
    total_estimated_value: Decimal
    currency_code: str
    approved_by_user_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None
    items: List[PlanItemResponse] = Field(default_factory=list)
