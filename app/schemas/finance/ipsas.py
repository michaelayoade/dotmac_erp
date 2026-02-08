"""
IPSAS Pydantic Schemas.

Request/response models for Fund Accounting, Appropriations,
Commitments, Virements, CoA Segments, and IPSAS reporting.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
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
    description: str | None = None
    is_restricted: bool = False
    restriction_description: str | None = None
    donor_name: str | None = None
    donor_reference: str | None = None
    parent_fund_id: UUID | None = None


class FundUpdate(BaseModel):
    """Update an existing fund."""

    fund_name: str | None = None
    description: str | None = None
    status: str | None = None
    is_restricted: bool | None = None
    restriction_description: str | None = None
    donor_name: str | None = None
    donor_reference: str | None = None
    effective_to: date | None = None


class FundResponse(BaseModel):
    """Fund response."""

    model_config = ConfigDict(from_attributes=True)

    fund_id: UUID
    organization_id: UUID
    fund_code: str
    fund_name: str
    description: str | None = None
    fund_type: str
    status: str
    is_restricted: bool
    restriction_description: str | None = None
    donor_name: str | None = None
    donor_reference: str | None = None
    effective_from: date
    effective_to: date | None = None
    parent_fund_id: UUID | None = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime | None = None


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
    budget_id: UUID | None = None
    account_id: UUID | None = None
    cost_center_id: UUID | None = None
    business_unit_id: UUID | None = None
    appropriation_act_reference: str | None = None
    effective_to: date | None = None


class AppropriationResponse(BaseModel):
    """Appropriation response."""

    model_config = ConfigDict(from_attributes=True)

    appropriation_id: UUID
    organization_id: UUID
    fiscal_year_id: UUID
    fund_id: UUID
    budget_id: UUID | None = None
    appropriation_code: str
    appropriation_name: str
    appropriation_type: str
    status: str
    approved_amount: Decimal
    revised_amount: Decimal
    currency_code: str
    account_id: UUID | None = None
    cost_center_id: UUID | None = None
    business_unit_id: UUID | None = None
    appropriation_act_reference: str | None = None
    effective_from: date
    effective_to: date | None = None
    created_by_user_id: UUID
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


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
    cost_center_id: UUID | None = None
    business_unit_id: UUID | None = None


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
    cost_center_id: UUID | None = None
    business_unit_id: UUID | None = None
    status: str
    created_at: datetime
    updated_at: datetime | None = None


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
    appropriation_id: UUID | None = None
    allotment_id: UUID | None = None
    fund_id: UUID
    source_type: str
    source_id: UUID
    account_id: UUID
    cost_center_id: UUID | None = None
    business_unit_id: UUID | None = None
    project_id: UUID | None = None
    fiscal_year_id: UUID
    fiscal_period_id: UUID
    currency_code: str
    committed_amount: Decimal
    obligated_amount: Decimal
    expended_amount: Decimal
    cancelled_amount: Decimal
    commitment_date: date
    obligation_date: date | None = None
    expenditure_date: date | None = None
    created_by_user_id: UUID
    created_at: datetime
    updated_at: datetime | None = None


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
    from_account_id: UUID | None = None
    from_cost_center_id: UUID | None = None
    from_fund_id: UUID | None = None
    to_account_id: UUID | None = None
    to_cost_center_id: UUID | None = None
    to_fund_id: UUID | None = None
    approval_authority: str | None = None


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
    from_account_id: UUID | None = None
    from_cost_center_id: UUID | None = None
    from_fund_id: UUID | None = None
    to_appropriation_id: UUID
    to_account_id: UUID | None = None
    to_cost_center_id: UUID | None = None
    to_fund_id: UUID | None = None
    amount: Decimal
    currency_code: str
    justification: str
    approval_authority: str | None = None
    created_by_user_id: UUID
    approved_by_user_id: UUID | None = None
    approved_at: datetime | None = None
    applied_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


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
    parent_segment_value_id: UUID | None = None
    is_active: bool = True


class CoASegmentValueResponse(BaseModel):
    """CoA segment value response."""

    model_config = ConfigDict(from_attributes=True)

    segment_value_id: UUID
    segment_def_id: UUID
    organization_id: UUID
    segment_code: str
    segment_name: str
    parent_segment_value_id: UUID | None = None
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
    fund_code: str | None = None
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
    fund_id: UUID | None = None
    total_budget: Decimal
    total_committed: Decimal
    total_obligated: Decimal
    total_expended: Decimal
    total_available: Decimal
    lines: list[BudgetLineItem]


class AvailableBalanceResponse(BaseModel):
    """Available balance calculation result."""

    organization_id: UUID
    appropriation_id: UUID | None = None
    fund_id: UUID | None = None
    account_id: UUID | None = None
    total_appropriated: Decimal
    total_allotted: Decimal
    total_committed: Decimal
    total_obligated: Decimal
    total_expended: Decimal
    available_balance: Decimal
    currency_code: str
