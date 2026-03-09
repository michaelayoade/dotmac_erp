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
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
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
    abbr: str | None = Field(default=None, max_length=20)
    component_type: SalaryComponentType
    description: str | None = None
    expense_account_id: UUID | None = None
    liability_account_id: UUID | None = None
    is_tax_applicable: bool = False
    is_statutory: bool = False
    depends_on_payment_days: bool = True


class SalaryComponentCreate(SalaryComponentBase):
    """Create salary component request."""

    organization_id: UUID


class SalaryComponentUpdate(BaseModel):
    """Update salary component request."""

    component_code: str | None = Field(default=None, max_length=30)
    component_name: str | None = Field(default=None, max_length=100)
    abbr: str | None = Field(default=None, max_length=20)
    description: str | None = None
    expense_account_id: UUID | None = None
    liability_account_id: UUID | None = None
    is_tax_applicable: bool | None = None
    is_statutory: bool | None = None
    depends_on_payment_days: bool | None = None
    is_active: bool | None = None


class SalaryComponentRead(SalaryComponentBase):
    """Salary component response."""

    model_config = ConfigDict(from_attributes=True)

    component_id: UUID
    organization_id: UUID
    is_active: bool
    display_order: int
    created_at: datetime
    updated_at: datetime | None = None


class SalaryComponentListResponse(BaseModel):
    """Paginated salary component list response."""

    items: list[SalaryComponentRead]
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
    description: str | None = None
    payroll_frequency: PayrollFrequency = PayrollFrequency.MONTHLY
    currency_code: str = Field(
        default=settings.default_functional_currency_code, max_length=3
    )


class SalaryStructureComponentBase(BaseModel):
    """Base salary structure line schema."""

    component_id: UUID
    amount: Decimal = Decimal("0")
    amount_based_on_formula: bool = False
    formula: str | None = None
    condition: str | None = None
    display_order: int = 0


class SalaryStructureComponentCreate(SalaryStructureComponentBase):
    """Create salary structure line."""

    pass


class SalaryStructureComponentRead(SalaryStructureComponentBase):
    """Salary structure line response."""

    model_config = ConfigDict(from_attributes=True)

    component_name: str | None = None
    abbr: str | None = None


class SalaryStructureCreate(SalaryStructureBase):
    """Create salary structure request."""

    organization_id: UUID
    earnings: list[SalaryStructureComponentCreate] = []
    deductions: list[SalaryStructureComponentCreate] = []


class SalaryStructureUpdate(BaseModel):
    """Update salary structure request."""

    structure_code: str | None = Field(default=None, max_length=30)
    structure_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    payroll_frequency: PayrollFrequency | None = None
    currency_code: str | None = Field(default=None, max_length=3)
    is_active: bool | None = None
    earnings: list[SalaryStructureComponentCreate] | None = None
    deductions: list[SalaryStructureComponentCreate] | None = None


class SalaryStructureRead(SalaryStructureBase):
    """Salary structure response."""

    model_config = ConfigDict(from_attributes=True)

    structure_id: UUID
    organization_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None
    earnings: list[SalaryStructureComponentRead] = []
    deductions: list[SalaryStructureComponentRead] = []


class SalaryStructureListResponse(BaseModel):
    """Paginated salary structure list response."""

    items: list[SalaryStructureRead]
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
    abbr: str | None = None
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
    abbr: str | None = None
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
    posting_date: date | None = None
    total_working_days: Decimal | None = None
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
    employee_name: str | None = None
    structure_id: UUID | None = None
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
    cost_center_id: UUID | None = None
    journal_entry_id: UUID | None = None
    posted_at: datetime | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    bank_account_name: str | None = None
    bank_branch_code: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    # Include earnings and deductions
    earnings: list[SalarySlipEarningRead] = []
    deductions: list[SalarySlipDeductionRead] = []


class SalarySlipListResponse(BaseModel):
    """Paginated salary slip list response."""

    items: list[SalarySlipRead]
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
    journal_entry_id: UUID | None = None


class SalarySlipExportRequest(BaseModel):
    """Export salary slips request."""

    employee_id: UUID | None = None
    status: SalarySlipStatus | None = None
    from_date: date | None = None
    to_date: date | None = None


# =============================================================================
# Payroll Entry Schemas
# =============================================================================


class PayrollEntryBase(BaseModel):
    """Base payroll entry schema."""

    posting_date: date
    start_date: date
    end_date: date
    payroll_frequency: PayrollFrequency = PayrollFrequency.MONTHLY
    currency_code: str = Field(
        default=settings.default_functional_currency_code, max_length=3
    )
    department_id: UUID | None = None
    designation_id: UUID | None = None
    employment_type_id: UUID | None = None
    bank_account_id: UUID | None = None
    notes: str | None = None


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
    updated_at: datetime | None = None


class PayrollEntryUpdate(BaseModel):
    """Update payroll entry request."""

    posting_date: date | None = None
    start_date: date | None = None
    end_date: date | None = None
    payroll_frequency: PayrollFrequency | None = None
    currency_code: str | None = Field(default=None, max_length=3)
    department_id: UUID | None = None
    designation_id: UUID | None = None
    employment_type_id: UUID | None = None
    bank_account_id: UUID | None = None
    notes: str | None = None


class PayrollEntryListResponse(BaseModel):
    """Paginated payroll entry list response."""

    items: list[PayrollEntryRead]
    total: int
    offset: int
    limit: int


class PayrollSlipGenerationResult(BaseModel):
    """Result of salary slip generation."""

    created_count: int
    skipped_count: int
    errors: list[dict] = []


class PayrollPayoutRequest(BaseModel):
    """Payroll payout request."""

    slip_ids: list[UUID] | None = None
    payment_reference: str | None = None


class PayrollPayoutResult(BaseModel):
    """Payroll payout result."""

    updated: int
    requested: int
    errors: list[dict] = []


class SalaryStructureAssignmentBase(BaseModel):
    """Base salary structure assignment schema."""

    employee_id: UUID
    structure_id: UUID
    from_date: date
    to_date: date | None = None
    base: Decimal = Decimal("0")
    variable: Decimal = Decimal("0")
    income_tax_slab: str | None = None


class SalaryStructureAssignmentCreate(SalaryStructureAssignmentBase):
    """Create salary structure assignment request."""

    pass


class SalaryStructureAssignmentUpdate(BaseModel):
    """Update salary structure assignment request."""

    structure_id: UUID | None = None
    from_date: date | None = None
    to_date: date | None = None
    base: Decimal | None = None
    variable: Decimal | None = None
    income_tax_slab: str | None = None


class SalaryStructureAssignmentRead(SalaryStructureAssignmentBase):
    """Salary structure assignment response."""

    model_config = ConfigDict(from_attributes=True)

    assignment_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None


class SalaryStructureAssignmentListResponse(BaseModel):
    """Paginated salary structure assignment list response."""

    items: list[SalaryStructureAssignmentRead]
    total: int
    offset: int
    limit: int
