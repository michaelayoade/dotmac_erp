"""
Document Template Context Schemas.

Pydantic models defining the expected context variables for each document type.
Used for validation before rendering templates.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class OfferLetterContext(BaseModel):
    """
    Context for OFFER_LETTER template.

    Contains all variables available when rendering an offer letter.
    """

    model_config = ConfigDict(from_attributes=True)

    # Candidate information
    candidate_name: str
    candidate_first_name: str
    candidate_last_name: str
    candidate_email: str | None = None
    candidate_phone: str | None = None
    candidate_address: str | None = None

    # Position details
    job_title: str
    designation_name: str
    department_name: str | None = None
    reporting_to: str | None = None
    location: str | None = None
    employment_type: str = "FULL_TIME"  # FULL_TIME, PART_TIME, CONTRACT

    # Compensation
    base_salary: Decimal
    currency_code: str = "NGN"
    pay_frequency: str = "MONTHLY"  # MONTHLY, BI_WEEKLY, WEEKLY, ANNUAL
    annual_salary: Decimal | None = None  # Calculated from base_salary if monthly

    # Additional compensation
    signing_bonus: Decimal | None = None
    relocation_allowance: Decimal | None = None
    benefits: list[str] | None = None
    other_benefits: str | None = None

    # Dates
    offer_date: date
    offer_expiry_date: date
    proposed_start_date: date
    document_date: date | None = None

    # Terms
    probation_months: int = 3
    notice_period_days: int = 30
    work_hours_per_week: int = 40
    work_days: str | None = None  # e.g., "Monday to Friday"

    # Organization
    organization_name: str
    organization_legal_name: str | None = None
    organization_address: str | None = None
    organization_phone: str | None = None
    organization_email: str | None = None
    organization_website: str | None = None
    organization_logo_url: str | None = None

    # Signatory
    signatory_name: str
    signatory_title: str
    signatory_department: str | None = None

    # Additional terms
    terms_and_conditions: str | None = None
    special_conditions: str | None = None

    # Reference
    offer_number: str

    @field_validator("annual_salary", mode="before")
    @classmethod
    def calculate_annual_salary(cls, v: Any, info: Any) -> Decimal | None:
        """Calculate annual salary from base salary if not provided."""
        if v is not None:
            if isinstance(v, Decimal):
                return v
            return Decimal(str(v))
        # This validator runs before other fields are validated, so we can't
        # access base_salary here. The calculation should be done in the service.
        return None


class EmploymentContractContext(BaseModel):
    """Context for EMPLOYMENT_CONTRACT template."""

    model_config = ConfigDict(from_attributes=True)

    # Employee information
    employee_name: str
    employee_address: str
    employee_id_type: str | None = None  # e.g., "National ID", "Passport"
    employee_id_number: str | None = None

    # Position
    job_title: str
    designation_name: str
    department_name: str | None = None
    reporting_to: str | None = None
    location: str | None = None

    # Employment details
    start_date: date
    employment_type: str  # FULL_TIME, PART_TIME, CONTRACT
    contract_end_date: date | None = None  # For fixed-term contracts
    probation_period_months: int

    # Compensation
    base_salary: Decimal
    currency_code: str = "NGN"
    payment_frequency: str = "MONTHLY"
    payment_method: str = "BANK_TRANSFER"  # BANK_TRANSFER, CHEQUE, CASH

    # Allowances
    allowances: list[dict[str, Any]] | None = None  # [{name, amount, taxable}]

    # Working hours
    work_hours_per_week: int
    work_days: list[str]  # ['Monday', 'Tuesday', ...]
    work_start_time: str | None = None
    work_end_time: str | None = None

    # Leave entitlement
    annual_leave_days: int
    sick_leave_days: int
    other_leave: list[dict[str, Any]] | None = None  # [{type, days}]

    # Termination
    notice_period_days: int

    # Legal
    governing_law: str = "Laws of Nigeria"
    arbitration_location: str | None = None

    # Confidentiality & Non-compete
    has_nda: bool = True
    has_non_compete: bool = False
    non_compete_duration_months: int | None = None
    non_compete_territory: str | None = None

    # Organization
    organization_name: str
    organization_legal_name: str
    organization_registration_number: str | None = None
    organization_address: str

    # Signatory
    signatory_name: str
    signatory_title: str

    # Reference
    contract_number: str | None = None


class ConfirmationLetterContext(BaseModel):
    """Context for CONFIRMATION_LETTER (post-probation) template."""

    model_config = ConfigDict(from_attributes=True)

    # Employee
    employee_name: str
    employee_code: str
    employee_address: str | None = None

    # Position
    job_title: str
    department_name: str | None = None

    # Dates
    start_date: date
    probation_end_date: date
    confirmation_date: date

    # Salary (may be revised post-probation)
    current_salary: Decimal
    new_salary: Decimal | None = None
    currency_code: str = "NGN"
    salary_effective_date: date | None = None

    # Organization
    organization_name: str

    # Signatory
    signatory_name: str
    signatory_title: str


class TerminationLetterContext(BaseModel):
    """Context for TERMINATION_LETTER template."""

    model_config = ConfigDict(from_attributes=True)

    # Employee
    employee_name: str
    employee_code: str
    employee_address: str | None = None

    # Position
    job_title: str
    department_name: str | None = None

    # Dates
    termination_date: date
    last_working_date: date
    letter_date: date

    # Termination details
    termination_reason: str  # e.g., "Redundancy", "Performance", "Resignation"
    termination_type: str  # "VOLUNTARY", "INVOLUNTARY", "MUTUAL"

    # Settlement
    notice_period_served: bool
    notice_period_days: int
    payment_in_lieu_of_notice: Decimal | None = None
    severance_amount: Decimal | None = None
    accrued_leave_days: int
    leave_encashment_amount: Decimal | None = None
    total_settlement: Decimal
    currency_code: str = "NGN"

    # Exit checklist
    company_property_returned: bool = False
    exit_interview_completed: bool = False
    clearance_completed: bool = False

    # Organization
    organization_name: str

    # Signatory
    signatory_name: str
    signatory_title: str


class SalaryRevisionLetterContext(BaseModel):
    """Context for SALARY_REVISION_LETTER template."""

    model_config = ConfigDict(from_attributes=True)

    # Employee
    employee_name: str
    employee_code: str

    # Position
    job_title: str
    department_name: str | None = None

    # Salary change
    current_salary: Decimal
    new_salary: Decimal
    currency_code: str = "NGN"
    increment_amount: Decimal
    increment_percentage: Decimal
    effective_date: date

    # Reason
    revision_reason: str  # e.g., "Annual Review", "Promotion", "Market Adjustment"

    # Organization
    organization_name: str

    # Signatory
    signatory_name: str
    signatory_title: str

    # Reference
    letter_date: date


# =============================================================================
# Discipline Document Contexts
# =============================================================================


class QueryLetterContext(BaseModel):
    """
    Context for SHOW_CAUSE_NOTICE template (discipline query letter).

    Contains variables for rendering a formal disciplinary query to an employee.
    """

    model_config = ConfigDict(from_attributes=True)

    # Case information
    case_number: str
    case_date: date  # Date the case was created/reported

    # Employee information
    employee_name: str
    employee_code: str
    employee_address: str | None = None
    job_title: str | None = None
    department_name: str | None = None

    # Violation details
    violation_type: str  # e.g., "MISCONDUCT", "ATTENDANCE", etc.
    violation_severity: str  # e.g., "MINOR", "MAJOR", "CRITICAL"
    incident_date: date
    incident_description: str
    policy_violated: str | None = None  # Specific policy reference

    # Query details
    query_text: str  # The formal query/allegations
    response_due_date: date
    response_instructions: str | None = None

    # Organization
    organization_name: str
    organization_address: str | None = None
    organization_logo_url: str | None = None

    # Signatory
    signatory_name: str
    signatory_title: str

    # Reference
    letter_date: date


class WarningLetterContext(BaseModel):
    """
    Context for WARNING_LETTER template (disciplinary warning).

    Contains variables for rendering a formal written warning.
    """

    model_config = ConfigDict(from_attributes=True)

    # Case information
    case_number: str

    # Employee information
    employee_name: str
    employee_code: str
    employee_address: str | None = None
    job_title: str | None = None
    department_name: str | None = None

    # Warning details
    warning_type: str  # "VERBAL_WARNING", "WRITTEN_WARNING", "FINAL_WARNING"
    warning_description: str
    violation_type: str
    incident_date: date
    incident_summary: str

    # Previous warnings (if any)
    previous_warnings: list[dict[str, Any]] | None = None  # [{type, date, summary}]
    total_warnings_count: int = 0

    # Expected improvement
    expected_improvement: str
    improvement_deadline: date | None = None
    consequences_if_repeated: str

    # Appeal rights
    appeal_period_days: int = 14
    appeal_deadline: date | None = None
    appeal_instructions: str | None = None

    # Effective dates
    effective_date: date
    warning_expiry_date: date | None = None  # When warning expires from record

    # Organization
    organization_name: str
    organization_address: str | None = None
    organization_logo_url: str | None = None

    # Signatory
    signatory_name: str
    signatory_title: str

    # Reference
    letter_date: date


class DecisionLetterContext(BaseModel):
    """
    Context for decision notification letter (discipline outcome).

    Contains variables for rendering the formal decision after investigation/hearing.
    """

    model_config = ConfigDict(from_attributes=True)

    # Case information
    case_number: str

    # Employee information
    employee_name: str
    employee_code: str
    employee_address: str | None = None
    job_title: str | None = None
    department_name: str | None = None

    # Investigation summary
    investigation_summary: str
    hearing_date: date | None = None
    hearing_outcome: str | None = None

    # Decision
    decision_summary: str
    decision_date: date

    # Actions taken
    actions: list[dict[str, Any]]  # [{type, description, effective_date, end_date}]

    # Appeal rights
    appeal_period_days: int = 14
    appeal_deadline: date
    appeal_instructions: str | None = None

    # Organization
    organization_name: str
    organization_address: str | None = None
    organization_logo_url: str | None = None

    # Signatory
    signatory_name: str
    signatory_title: str

    # Reference
    letter_date: date


class DisciplineTerminationLetterContext(BaseModel):
    """
    Context for discipline-related TERMINATION_LETTER.

    Extends standard termination with disciplinary case references.
    """

    model_config = ConfigDict(from_attributes=True)

    # Case information
    case_number: str

    # Employee information
    employee_name: str
    employee_code: str
    employee_address: str | None = None
    job_title: str | None = None
    department_name: str | None = None

    # Termination details
    termination_date: date
    last_working_day: date
    termination_reason: str  # Summary of the reason
    violation_type: str
    incident_date: date

    # Case history summary
    case_summary: str  # Brief history of the case
    previous_actions: list[dict[str, Any]] | None = (
        None  # Previous disciplinary actions
    )

    # Final settlement
    final_settlement_items: list[dict[str, Any]] | None = None  # [{item, amount}]
    total_settlement: Decimal | None = None
    currency_code: str = "NGN"

    # Appeal rights
    appeal_period_days: int = 14
    appeal_deadline: date | None = None
    appeal_instructions: str | None = None

    # Return of company property
    items_to_return: list[str] | None = None
    return_deadline: date | None = None

    # Organization
    organization_name: str
    organization_address: str | None = None
    organization_logo_url: str | None = None

    # Signatory
    signatory_name: str
    signatory_title: str

    # Reference
    letter_date: date
