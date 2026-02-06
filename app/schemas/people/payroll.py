"""
Payroll Pydantic Schemas.

Pydantic schemas for Payroll APIs including:
- Salary Component
- Salary Structure
- Salary Slip
- Payroll Entry
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.payroll.salary_component import SalaryComponentType
from app.models.people.payroll.salary_slip import SalarySlipStatus
from app.models.people.payroll.salary_structure import PayrollFrequency


# =============================================================================
# Salary Component Schemas
# =============================================================================


class SalaryComponentBase(BaseModel):
    """Base salary component schema."""

    component_code: str = Field(max_length=30)
    component_name: str = Field(max_length=100)
    abbr: Optional[str] = Field(default=None, max_length=20)
    component_type: SalaryComponentType
    description: Optional[str] = None
    expense_account_id: Optional[UUID] = None
    liability_account_id: Optional[UUID] = None
    is_tax_applicable: bool = False
    is_statutory: bool = False
    depends_on_payment_days: bool = True


class SalaryComponentCreate(SalaryComponentBase):
    """Create salary component request."""

    organization_id: UUID


class SalaryComponentUpdate(BaseModel):
    """Update salary component request."""

    component_code: Optional[str] = Field(default=None, max_length=30)
    component_name: Optional[str] = Field(default=None, max_length=100)
    abbr: Optional[str] = Field(default=None, max_length=20)
    description: Optional[str] = None
    expense_account_id: Optional[UUID] = None
    liability_account_id: Optional[UUID] = None
    is_tax_applicable: Optional[bool] = None
    is_statutory: Optional[bool] = None
    depends_on_payment_days: Optional[bool] = None
    is_active: Optional[bool] = None


class SalaryComponentRead(SalaryComponentBase):
    """Salary component response."""

    model_config = ConfigDict(from_attributes=True)

    component_id: UUID
    organization_id: UUID
    is_active: bool
    display_order: int
    created_at: datetime
    updated_at: Optional[datetime] = None


class SalaryComponentListResponse(BaseModel):
    """Paginated salary component list response."""

    items: List[SalaryComponentRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Salary Structure Schemas
# =============================================================================


class SalaryStructureBase(BaseModel):
    """Base salary structure schema."""

    structure_code: str = Field(max_length=30)
    structure_name: str = Field(max_length=100)
    description: Optional[str] = None
    payroll_frequency: PayrollFrequency = PayrollFrequency.MONTHLY
    currency_code: str = Field(default="NGN", max_length=3)


class SalaryStructureComponentBase(BaseModel):
    """Base salary structure line schema."""

    component_id: UUID
    amount: Decimal = Decimal("0")
    amount_based_on_formula: bool = False
    formula: Optional[str] = None
    condition: Optional[str] = None
    display_order: int = 0


class SalaryStructureComponentCreate(SalaryStructureComponentBase):
    """Create salary structure line."""

    pass


class SalaryStructureComponentRead(SalaryStructureComponentBase):
    """Salary structure line response."""

    model_config = ConfigDict(from_attributes=True)

    component_name: Optional[str] = None
    abbr: Optional[str] = None


class SalaryStructureCreate(SalaryStructureBase):
    """Create salary structure request."""

    organization_id: UUID
    earnings: List[SalaryStructureComponentCreate] = []
    deductions: List[SalaryStructureComponentCreate] = []


class SalaryStructureUpdate(BaseModel):
    """Update salary structure request."""

    structure_code: Optional[str] = Field(default=None, max_length=30)
    structure_name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    payroll_frequency: Optional[PayrollFrequency] = None
    currency_code: Optional[str] = Field(default=None, max_length=3)
    is_active: Optional[bool] = None
    earnings: Optional[List[SalaryStructureComponentCreate]] = None
    deductions: Optional[List[SalaryStructureComponentCreate]] = None


class SalaryStructureRead(SalaryStructureBase):
    """Salary structure response."""

    model_config = ConfigDict(from_attributes=True)

    structure_id: UUID
    organization_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    earnings: List[SalaryStructureComponentRead] = []
    deductions: List[SalaryStructureComponentRead] = []


class SalaryStructureListResponse(BaseModel):
    """Paginated salary structure list response."""

    items: List[SalaryStructureRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Salary Slip Schemas
# =============================================================================


class SalarySlipEarningRead(BaseModel):
    """Salary slip earning line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    component_id: UUID
    component_name: str
    abbr: Optional[str] = None
    amount: Decimal
    default_amount: Decimal
    additional_amount: Decimal
    year_to_date: Decimal
    statistical_component: bool
    do_not_include_in_total: bool


class SalarySlipDeductionRead(BaseModel):
    """Salary slip deduction line response."""

    model_config = ConfigDict(from_attributes=True)

    line_id: UUID
    component_id: UUID
    component_name: str
    abbr: Optional[str] = None
    amount: Decimal
    default_amount: Decimal
    additional_amount: Decimal
    year_to_date: Decimal
    statistical_component: bool
    do_not_include_in_total: bool


class SalarySlipBase(BaseModel):
    """Base salary slip schema."""

    employee_id: UUID
    start_date: date
    end_date: date
    posting_date: Optional[date] = None
    total_working_days: Optional[Decimal] = None
    absent_days: Decimal = Decimal("0")
    leave_without_pay: Decimal = Decimal("0")


class SalarySlipCreate(SalarySlipBase):
    """Create salary slip request."""

    organization_id: UUID


class SalarySlipRead(BaseModel):
    """Salary slip response."""

    model_config = ConfigDict(from_attributes=True)

    slip_id: UUID
    organization_id: UUID
    slip_number: str
    employee_id: UUID
    employee_name: Optional[str] = None
    structure_id: Optional[UUID] = None
    posting_date: date
    start_date: date
    end_date: date
    currency_code: str
    exchange_rate: Decimal
    total_working_days: Decimal
    absent_days: Decimal
    payment_days: Decimal
    leave_without_pay: Decimal
    gross_pay: Decimal
    total_deduction: Decimal
    net_pay: Decimal
    status: SalarySlipStatus
    cost_center_id: Optional[UUID] = None
    journal_entry_id: Optional[UUID] = None
    posted_at: Optional[datetime] = None
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_branch_code: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    # Include earnings and deductions
    earnings: List[SalarySlipEarningRead] = []
    deductions: List[SalarySlipDeductionRead] = []


class SalarySlipListResponse(BaseModel):
    """Paginated salary slip list response."""

    items: List[SalarySlipRead]
    total: int
    offset: int
    limit: int


class SalarySlipPostRequest(BaseModel):
    """Request to post a salary slip to GL."""

    organization_id: UUID
    posting_date: date
    user_id: UUID


class SalarySlipPostResponse(BaseModel):
    """Response from posting a salary slip."""

    success: bool
    message: str
    journal_entry_id: Optional[UUID] = None


class SalarySlipExportRequest(BaseModel):
    """Export salary slips request."""

    employee_id: Optional[UUID] = None
    status: Optional[SalarySlipStatus] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None


# =============================================================================
# Payroll Entry Schemas
# =============================================================================


class PayrollEntryBase(BaseModel):
    """Base payroll entry schema."""

    posting_date: date
    start_date: date
    end_date: date
    payroll_frequency: PayrollFrequency = PayrollFrequency.MONTHLY
    currency_code: str = Field(default="NGN", max_length=3)
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    bank_account_id: Optional[UUID] = None
    notes: Optional[str] = None


class PayrollEntryCreate(PayrollEntryBase):
    """Create payroll entry request."""

    organization_id: UUID


class PayrollEntryRead(PayrollEntryBase):
    """Payroll entry response."""

    model_config = ConfigDict(from_attributes=True)

    entry_id: UUID
    organization_id: UUID
    entry_number: str
    total_gross_pay: Decimal
    total_deductions: Decimal
    total_net_pay: Decimal
    employee_count: int
    status: str
    salary_slips_created: bool
    salary_slips_submitted: bool
    created_at: datetime
    updated_at: Optional[datetime] = None


class PayrollEntryUpdate(BaseModel):
    """Update payroll entry request."""

    posting_date: Optional[date] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    payroll_frequency: Optional[PayrollFrequency] = None
    currency_code: Optional[str] = Field(default=None, max_length=3)
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    bank_account_id: Optional[UUID] = None
    notes: Optional[str] = None


class PayrollEntryListResponse(BaseModel):
    """Paginated payroll entry list response."""

    items: List[PayrollEntryRead]
    total: int
    offset: int
    limit: int


class PayrollSlipGenerationResult(BaseModel):
    """Result of salary slip generation."""

    created_count: int
    skipped_count: int
    errors: List[dict] = []


class PayrollPayoutRequest(BaseModel):
    """Payroll payout request."""

    slip_ids: Optional[List[UUID]] = None
    payment_reference: Optional[str] = None


class PayrollPayoutResult(BaseModel):
    """Payroll payout result."""

    updated: int
    requested: int
    errors: List[dict] = []


class SalaryStructureAssignmentBase(BaseModel):
    """Base salary structure assignment schema."""

    employee_id: UUID
    structure_id: UUID
    from_date: date
    to_date: Optional[date] = None
    base: Decimal = Decimal("0")
    variable: Decimal = Decimal("0")
    income_tax_slab: Optional[str] = None


class SalaryStructureAssignmentCreate(SalaryStructureAssignmentBase):
    """Create salary structure assignment request."""

    pass


class SalaryStructureAssignmentUpdate(BaseModel):
    """Update salary structure assignment request."""

    structure_id: Optional[UUID] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None
    base: Optional[Decimal] = None
    variable: Optional[Decimal] = None
    income_tax_slab: Optional[str] = None


class SalaryStructureAssignmentRead(SalaryStructureAssignmentBase):
    """Salary structure assignment response."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


class SalaryStructureAssignmentListResponse(BaseModel):
    """Paginated salary structure assignment list response."""

    items: List[SalaryStructureAssignmentRead]
    total: int
    offset: int
    limit: int
