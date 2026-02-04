"""
IPSAS Pydantic Schemas.

Request/response models for Fund Accounting, Appropriations,
Commitments, Virements, CoA Segments, and IPSAS reporting.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


# =============================================================================
# Fund Schemas
# =============================================================================


class FundCreate(BaseModel):
    """Create a new fund."""

    fund_code: str
    fund_name: str
    fund_type: str
    effective_from: date
    description: Optional[str] = None
    is_restricted: bool = False
    restriction_description: Optional[str] = None
    donor_name: Optional[str] = None
    donor_reference: Optional[str] = None
    parent_fund_id: Optional[UUID] = None


class FundUpdate(BaseModel):
    """Update an existing fund."""

    fund_name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    is_restricted: Optional[bool] = None
    restriction_description: Optional[str] = None
    donor_name: Optional[str] = None
    donor_reference: Optional[str] = None
    effective_to: Optional[date] = None


class FundResponse(BaseModel):
    """Fund response."""

    model_config = ConfigDict(from_attributes=True)

    fund_id: UUID
    organization_id: UUID
    fund_code: str
    fund_name: str
    description: Optional[str] = None
    fund_type: str
    status: str
    is_restricted: bool
    restriction_description: Optional[str] = None
    donor_name: Optional[str] = None
    donor_reference: Optional[str] = None
    effective_from: date
    effective_to: Optional[date] = None
    parent_fund_id: Optional[UUID] = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


# =============================================================================
# Appropriation Schemas
# =============================================================================


class AppropriationCreate(BaseModel):
    """Create an appropriation."""

    fiscal_year_id: UUID
    fund_id: UUID
    appropriation_code: str
    appropriation_name: str
    appropriation_type: str
    approved_amount: Decimal
    currency_code: str
    effective_from: date
    budget_id: Optional[UUID] = None
    account_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    business_unit_id: Optional[UUID] = None
    appropriation_act_reference: Optional[str] = None
    effective_to: Optional[date] = None


class AppropriationResponse(BaseModel):
    """Appropriation response."""

    model_config = ConfigDict(from_attributes=True)

    appropriation_id: UUID
    organization_id: UUID
    fiscal_year_id: UUID
    fund_id: UUID
    budget_id: Optional[UUID] = None
    appropriation_code: str
    appropriation_name: str
    appropriation_type: str
    status: str
    approved_amount: Decimal
    revised_amount: Decimal
    currency_code: str
    account_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    business_unit_id: Optional[UUID] = None
    appropriation_act_reference: Optional[str] = None
    effective_from: date
    effective_to: Optional[date] = None
    created_by_user_id: UUID
    approved_by_user_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


# =============================================================================
# Allotment Schemas
# =============================================================================


class AllotmentCreate(BaseModel):
    """Create an allotment."""

    appropriation_id: UUID
    allotment_code: str
    allotment_name: str
    allotted_amount: Decimal
    period_from: date
    period_to: date
    cost_center_id: Optional[UUID] = None
    business_unit_id: Optional[UUID] = None


class AllotmentResponse(BaseModel):
    """Allotment response."""

    model_config = ConfigDict(from_attributes=True)

    allotment_id: UUID
    appropriation_id: UUID
    organization_id: UUID
    allotment_code: str
    allotment_name: str
    allotted_amount: Decimal
    period_from: date
    period_to: date
    cost_center_id: Optional[UUID] = None
    business_unit_id: Optional[UUID] = None
    status: str
    created_at: datetime
    updated_at: Optional[datetime] = None


# =============================================================================
# Commitment Schemas
# =============================================================================


class CommitmentResponse(BaseModel):
    """Commitment response."""

    model_config = ConfigDict(from_attributes=True)

    commitment_id: UUID
    organization_id: UUID
    commitment_number: str
    commitment_type: str
    status: str
    appropriation_id: Optional[UUID] = None
    allotment_id: Optional[UUID] = None
    fund_id: UUID
    source_type: str
    source_id: UUID
    account_id: UUID
    cost_center_id: Optional[UUID] = None
    business_unit_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    fiscal_year_id: UUID
    fiscal_period_id: UUID
    currency_code: str
    committed_amount: Decimal
    obligated_amount: Decimal
    expended_amount: Decimal
    cancelled_amount: Decimal
    commitment_date: date
    obligation_date: Optional[date] = None
    expenditure_date: Optional[date] = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


# =============================================================================
# Virement Schemas
# =============================================================================


class VirementCreate(BaseModel):
    """Create a virement."""

    fiscal_year_id: UUID
    description: str
    from_appropriation_id: UUID
    to_appropriation_id: UUID
    amount: Decimal
    currency_code: str
    justification: str
    from_account_id: Optional[UUID] = None
    from_cost_center_id: Optional[UUID] = None
    from_fund_id: Optional[UUID] = None
    to_account_id: Optional[UUID] = None
    to_cost_center_id: Optional[UUID] = None
    to_fund_id: Optional[UUID] = None
    approval_authority: Optional[str] = None


class VirementResponse(BaseModel):
    """Virement response."""

    model_config = ConfigDict(from_attributes=True)

    virement_id: UUID
    organization_id: UUID
    fiscal_year_id: UUID
    virement_number: str
    description: str
    status: str
    from_appropriation_id: UUID
    from_account_id: Optional[UUID] = None
    from_cost_center_id: Optional[UUID] = None
    from_fund_id: Optional[UUID] = None
    to_appropriation_id: UUID
    to_account_id: Optional[UUID] = None
    to_cost_center_id: Optional[UUID] = None
    to_fund_id: Optional[UUID] = None
    amount: Decimal
    currency_code: str
    justification: str
    approval_authority: Optional[str] = None
    created_by_user_id: UUID
    approved_by_user_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None


# =============================================================================
# CoA Segment Schemas
# =============================================================================


class CoASegmentDefinitionCreate(BaseModel):
    """Create a CoA segment definition."""

    segment_type: str
    segment_name: str
    code_position_start: int
    code_length: int
    separator: str = "-"
    is_required: bool = True
    display_order: int = 0


class CoASegmentDefinitionResponse(BaseModel):
    """CoA segment definition response."""

    model_config = ConfigDict(from_attributes=True)

    segment_def_id: UUID
    organization_id: UUID
    segment_type: str
    segment_name: str
    code_position_start: int
    code_length: int
    separator: str
    is_required: bool
    display_order: int
    created_at: datetime


class CoASegmentValueCreate(BaseModel):
    """Create a CoA segment value."""

    segment_code: str
    segment_name: str
    parent_segment_value_id: Optional[UUID] = None
    is_active: bool = True


class CoASegmentValueResponse(BaseModel):
    """CoA segment value response."""

    model_config = ConfigDict(from_attributes=True)

    segment_value_id: UUID
    segment_def_id: UUID
    organization_id: UUID
    segment_code: str
    segment_name: str
    parent_segment_value_id: Optional[UUID] = None
    is_active: bool
    created_at: datetime


# =============================================================================
# Report Schemas
# =============================================================================


class BudgetLineItem(BaseModel):
    """Single line in a budget comparison report."""

    appropriation_id: UUID
    appropriation_code: str
    appropriation_name: str
    fund_code: Optional[str] = None
    original_budget: Decimal
    revised_budget: Decimal
    committed: Decimal
    obligated: Decimal
    expended: Decimal
    available: Decimal
    utilization_pct: Decimal


class BudgetComparisonResponse(BaseModel):
    """IPSAS 24 Budget vs Actual comparison report."""

    organization_id: UUID
    fiscal_year_id: UUID
    fund_id: Optional[UUID] = None
    total_budget: Decimal
    total_committed: Decimal
    total_obligated: Decimal
    total_expended: Decimal
    total_available: Decimal
    lines: list[BudgetLineItem]


class AvailableBalanceResponse(BaseModel):
    """Available balance calculation result."""

    organization_id: UUID
    appropriation_id: Optional[UUID] = None
    fund_id: Optional[UUID] = None
    account_id: Optional[UUID] = None
    total_appropriated: Decimal
    total_allotted: Decimal
    total_committed: Decimal
    total_obligated: Decimal
    total_expended: Decimal
    available_balance: Decimal
    currency_code: str
