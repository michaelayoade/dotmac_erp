"""
Recruitment Management Pydantic Schemas.

Pydantic schemas for Recruitment APIs including:
- Job Opening
- Job Applicant
- Interview
- Job Offer
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.recruit import (
    ApplicantStatus,
    InterviewRound,
    InterviewStatus,
    JobOpeningStatus,
    OfferStatus,
)

# =============================================================================
# Job Opening Schemas
# =============================================================================


class JobOpeningBase(BaseModel):
    """Base job opening schema."""

    job_code: str = Field(max_length=30)
    job_title: str = Field(max_length=200)
    description: str | None = None
    department_id: UUID | None = None
    designation_id: UUID | None = None
    reports_to_id: UUID | None = None
    number_of_positions: int = 1
    posted_on: date | None = None
    closes_on: date | None = None
    employment_type: str = "FULL_TIME"
    location: str | None = Field(default=None, max_length=100)
    is_remote: bool = False
    min_salary: Decimal | None = None
    max_salary: Decimal | None = None
    currency_code: str = "NGN"
    min_experience_years: int | None = None
    required_skills: str | None = None
    preferred_skills: str | None = None
    education_requirements: str | None = None
    status: JobOpeningStatus = JobOpeningStatus.DRAFT


class JobOpeningCreate(JobOpeningBase):
    """Create job opening request."""

    pass


class JobOpeningUpdate(BaseModel):
    """Update job opening request."""

    job_code: str | None = Field(default=None, max_length=30)
    job_title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    department_id: UUID | None = None
    designation_id: UUID | None = None
    reports_to_id: UUID | None = None
    number_of_positions: int | None = None
    posted_on: date | None = None
    closes_on: date | None = None
    employment_type: str | None = None
    location: str | None = Field(default=None, max_length=100)
    is_remote: bool | None = None
    min_salary: Decimal | None = None
    max_salary: Decimal | None = None
    currency_code: str | None = None
    min_experience_years: int | None = None
    required_skills: str | None = None
    preferred_skills: str | None = None
    education_requirements: str | None = None
    status: JobOpeningStatus | None = None


class DepartmentBrief(BaseModel):
    """Brief department info."""

    model_config = ConfigDict(from_attributes=True)

    department_id: UUID
    department_code: str
    department_name: str


class DesignationBrief(BaseModel):
    """Brief designation info."""

    model_config = ConfigDict(from_attributes=True)

    designation_id: UUID
    designation_code: str
    designation_name: str


class JobOpeningRead(JobOpeningBase):
    """Job opening response."""

    model_config = ConfigDict(from_attributes=True)

    job_opening_id: UUID
    organization_id: UUID
    positions_filled: int
    created_at: datetime
    updated_at: datetime | None = None

    department: DepartmentBrief | None = None
    designation: DesignationBrief | None = None


class JobOpeningListResponse(BaseModel):
    """Paginated job opening list response."""

    items: list[JobOpeningRead]
    total: int
    offset: int
    limit: int


class JobOpeningBrief(BaseModel):
    """Brief job opening info."""

    model_config = ConfigDict(from_attributes=True)

    job_opening_id: UUID
    job_code: str
    job_title: str
    status: JobOpeningStatus


class JobOpeningStats(BaseModel):
    """Job opening statistics."""

    total: int
    open: int
    filled: int
    closed: int
    total_applicants: int


# =============================================================================
# Job Applicant Schemas
# =============================================================================


class JobApplicantBase(BaseModel):
    """Base job applicant schema."""

    job_opening_id: UUID
    first_name: str = Field(max_length=80)
    last_name: str = Field(max_length=80)
    email: str = Field(max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=80)
    country_code: str | None = Field(default=None, max_length=2)
    current_employer: str | None = Field(default=None, max_length=200)
    current_job_title: str | None = Field(default=None, max_length=200)
    years_of_experience: int | None = None
    highest_qualification: str | None = Field(default=None, max_length=100)
    skills: str | None = None
    source: str | None = Field(default=None, max_length=50)
    referral_employee_id: UUID | None = None
    cover_letter: str | None = None
    resume_url: str | None = Field(default=None, max_length=500)


class JobApplicantCreate(JobApplicantBase):
    """Create job applicant request."""

    pass


class JobApplicantUpdate(BaseModel):
    """Update job applicant request."""

    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=40)
    date_of_birth: date | None = None
    gender: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=80)
    country_code: str | None = Field(default=None, max_length=2)
    current_employer: str | None = Field(default=None, max_length=200)
    current_job_title: str | None = Field(default=None, max_length=200)
    years_of_experience: int | None = None
    highest_qualification: str | None = Field(default=None, max_length=100)
    skills: str | None = None
    source: str | None = Field(default=None, max_length=50)
    cover_letter: str | None = None
    resume_url: str | None = Field(default=None, max_length=500)
    status: ApplicantStatus | None = None
    overall_rating: int | None = None
    notes: str | None = None


class JobApplicantRead(JobApplicantBase):
    """Job applicant response."""

    model_config = ConfigDict(from_attributes=True)

    applicant_id: UUID
    organization_id: UUID
    application_number: str
    applied_on: date
    status: ApplicantStatus
    overall_rating: int | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    job_opening: JobOpeningBrief | None = None


class JobApplicantListResponse(BaseModel):
    """Paginated job applicant list response."""

    items: list[JobApplicantRead]
    total: int
    offset: int
    limit: int


class ApplicantStatusUpdateRequest(BaseModel):
    """Update applicant pipeline status."""

    status: ApplicantStatus
    notes: str | None = None


class ApplicantStats(BaseModel):
    """Applicant statistics for a job opening."""

    job_opening_id: UUID | None = None
    total: int
    new: int
    screening: int
    shortlisted: int
    interviewing: int
    selected: int
    rejected: int


# =============================================================================
# Interview Schemas
# =============================================================================


class InterviewBase(BaseModel):
    """Base interview schema."""

    applicant_id: UUID
    round: InterviewRound
    interview_type: str = "IN_PERSON"
    scheduled_from: datetime
    scheduled_to: datetime
    location: str | None = Field(default=None, max_length=200)
    meeting_link: str | None = Field(default=None, max_length=500)
    interviewer_id: UUID


class InterviewCreate(InterviewBase):
    """Create interview request."""

    pass


class InterviewUpdate(BaseModel):
    """Update interview request."""

    round: InterviewRound | None = None
    interview_type: str | None = None
    scheduled_from: datetime | None = None
    scheduled_to: datetime | None = None
    location: str | None = Field(default=None, max_length=200)
    meeting_link: str | None = Field(default=None, max_length=500)
    interviewer_id: UUID | None = None
    status: InterviewStatus | None = None


class EmployeeBrief(BaseModel):
    """Brief employee info."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: UUID
    employee_code: str


class ApplicantBrief(BaseModel):
    """Brief applicant info."""

    model_config = ConfigDict(from_attributes=True)

    applicant_id: UUID
    application_number: str
    first_name: str
    last_name: str
    email: str


class InterviewRead(InterviewBase):
    """Interview response."""

    model_config = ConfigDict(from_attributes=True)

    interview_id: UUID
    organization_id: UUID
    status: InterviewStatus
    actual_start: datetime | None = None
    actual_end: datetime | None = None
    rating: int | None = None
    recommendation: str | None = None
    feedback: str | None = None
    strengths: str | None = None
    weaknesses: str | None = None
    created_at: datetime
    updated_at: datetime | None = None

    applicant: ApplicantBrief | None = None
    interviewer: EmployeeBrief | None = None


class InterviewListResponse(BaseModel):
    """Paginated interview list response."""

    items: list[InterviewRead]
    total: int
    offset: int
    limit: int


class InterviewFeedbackRequest(BaseModel):
    """Submit interview feedback."""

    rating: int = Field(ge=1, le=5)
    recommendation: str = Field(description="STRONG_YES, YES, MAYBE, NO, STRONG_NO")
    feedback: str | None = None
    strengths: str | None = None
    weaknesses: str | None = None


class InterviewRescheduleRequest(BaseModel):
    """Reschedule interview request."""

    scheduled_from: datetime
    scheduled_to: datetime
    reason: str | None = None


class InterviewCancelRequest(BaseModel):
    """Cancel interview request."""

    reason: str | None = None


# =============================================================================
# Job Offer Schemas
# =============================================================================


class JobOfferBase(BaseModel):
    """Base job offer schema."""

    applicant_id: UUID
    job_opening_id: UUID
    designation_id: UUID
    department_id: UUID | None = None
    offer_date: date
    valid_until: date
    expected_joining_date: date
    base_salary: Decimal
    currency_code: str = "NGN"
    pay_frequency: str = "MONTHLY"
    signing_bonus: Decimal | None = None
    relocation_allowance: Decimal | None = None
    other_benefits: str | None = None
    employment_type: str = "FULL_TIME"
    probation_months: int = 3
    notice_period_days: int = 30
    terms_and_conditions: str | None = None
    notes: str | None = None


class JobOfferCreate(JobOfferBase):
    """Create job offer request."""

    pass


class JobOfferUpdate(BaseModel):
    """Update job offer request."""

    designation_id: UUID | None = None
    department_id: UUID | None = None
    offer_date: date | None = None
    valid_until: date | None = None
    expected_joining_date: date | None = None
    base_salary: Decimal | None = None
    currency_code: str | None = None
    pay_frequency: str | None = None
    signing_bonus: Decimal | None = None
    relocation_allowance: Decimal | None = None
    other_benefits: str | None = None
    employment_type: str | None = None
    probation_months: int | None = None
    notice_period_days: int | None = None
    terms_and_conditions: str | None = None
    notes: str | None = None


class JobOfferRead(JobOfferBase):
    """Job offer response."""

    model_config = ConfigDict(from_attributes=True)

    offer_id: UUID
    organization_id: UUID
    offer_number: str
    status: OfferStatus
    extended_on: date | None = None
    responded_on: date | None = None
    decline_reason: str | None = None
    converted_to_employee_id: UUID | None = None
    created_at: datetime
    updated_at: datetime | None = None

    applicant: ApplicantBrief | None = None
    job_opening: JobOpeningBrief | None = None
    designation: DesignationBrief | None = None
    department: DepartmentBrief | None = None


class JobOfferListResponse(BaseModel):
    """Paginated job offer list response."""

    items: list[JobOfferRead]
    total: int
    offset: int
    limit: int


class OfferApprovalRequest(BaseModel):
    """Approve offer request."""

    pass


class OfferExtendRequest(BaseModel):
    """Extend offer to candidate."""

    pass


class OfferResponseRequest(BaseModel):
    """Record candidate response to offer."""

    action: str = Field(description="ACCEPT or DECLINE")
    decline_reason: str | None = None


class OfferConvertRequest(BaseModel):
    """Convert accepted offer to employee."""

    date_of_joining: date


class RecruitmentStats(BaseModel):
    """Overall recruitment statistics."""

    open_positions: int
    total_applicants: int
    interviews_scheduled: int
    offers_pending: int
    offers_accepted: int
    recent_hires: int
