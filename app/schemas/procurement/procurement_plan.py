"""
Procurement Plan Schemas.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.models.procurement.enums import (
    PlanItemStatus,
    ProcurementMethod,
    ProcurementPlanStatus,
)


class PlanItemCreate(BaseModel):
    """Schema for creating a plan line item."""

    line_number: int = Field(ge=1)
    description: str
    budget_line_code: str | None = None
    budget_id: UUID | None = None
    estimated_value: Decimal = Field(ge=0)
    procurement_method: ProcurementMethod = ProcurementMethod.OPEN_COMPETITIVE
    planned_quarter: int = Field(ge=1, le=4)
    category: str | None = None


class PlanItemResponse(BaseModel):
    """Schema for plan item response."""

    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    plan_id: UUID
    organization_id: UUID
    line_number: int
    description: str
    budget_line_code: str | None = None
    budget_id: UUID | None = None
    estimated_value: Decimal
    procurement_method: ProcurementMethod
    planned_quarter: int
    approving_authority: str | None = None
    category: str | None = None
    status: PlanItemStatus


class ProcurementPlanCreate(BaseModel):
    """Schema for creating a procurement plan."""

    plan_number: str = Field(max_length=30)
    fiscal_year: str = Field(max_length=10)
    title: str = Field(max_length=200)
    currency_code: str = Field(
        default=settings.default_functional_currency_code, max_length=3
    )
    items: list[PlanItemCreate] = Field(default_factory=list)


class ProcurementPlanUpdate(BaseModel):
    """Schema for updating a procurement plan."""

    title: str | None = Field(default=None, max_length=200)
    fiscal_year: str | None = Field(default=None, max_length=10)
    currency_code: str | None = Field(default=None, max_length=3)


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
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime | None = None
    items: list[PlanItemResponse] = Field(default_factory=list)
