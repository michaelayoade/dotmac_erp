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
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.people.recruit import (
    JobOpeningStatus,
    ApplicantStatus,
    InterviewRound,
    InterviewStatus,
    OfferStatus,
)


# =============================================================================
# Job Opening Schemas
# =============================================================================


class JobOpeningBase(BaseModel):
    """Base job opening schema."""

    job_code: str = Field(max_length=30)
    job_title: str = Field(max_length=200)
    description: Optional[str] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    reports_to_id: Optional[UUID] = None
    number_of_positions: int = 1
    posted_on: Optional[date] = None
    closes_on: Optional[date] = None
    employment_type: str = "FULL_TIME"
    location: Optional[str] = Field(default=None, max_length=100)
    is_remote: bool = False
    min_salary: Optional[Decimal] = None
    max_salary: Optional[Decimal] = None
    currency_code: str = "NGN"
    min_experience_years: Optional[int] = None
    required_skills: Optional[str] = None
    preferred_skills: Optional[str] = None
    education_requirements: Optional[str] = None
    status: JobOpeningStatus = JobOpeningStatus.DRAFT


class JobOpeningCreate(JobOpeningBase):
    """Create job opening request."""

    pass


class JobOpeningUpdate(BaseModel):
    """Update job opening request."""

    job_code: Optional[str] = Field(default=None, max_length=30)
    job_title: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = None
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    reports_to_id: Optional[UUID] = None
    number_of_positions: Optional[int] = None
    posted_on: Optional[date] = None
    closes_on: Optional[date] = None
    employment_type: Optional[str] = None
    location: Optional[str] = Field(default=None, max_length=100)
    is_remote: Optional[bool] = None
    min_salary: Optional[Decimal] = None
    max_salary: Optional[Decimal] = None
    currency_code: Optional[str] = None
    min_experience_years: Optional[int] = None
    required_skills: Optional[str] = None
    preferred_skills: Optional[str] = None
    education_requirements: Optional[str] = None
    status: Optional[JobOpeningStatus] = None


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
    updated_at: Optional[datetime] = None

    department: Optional[DepartmentBrief] = None
    designation: Optional[DesignationBrief] = None


class JobOpeningListResponse(BaseModel):
    """Paginated job opening list response."""

    items: List[JobOpeningRead]
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
    phone: Optional[str] = Field(default=None, max_length=40)
    date_of_birth: Optional[date] = None
    gender: Optional[str] = Field(default=None, max_length=20)
    city: Optional[str] = Field(default=None, max_length=80)
    country_code: Optional[str] = Field(default=None, max_length=2)
    current_employer: Optional[str] = Field(default=None, max_length=200)
    current_job_title: Optional[str] = Field(default=None, max_length=200)
    years_of_experience: Optional[int] = None
    highest_qualification: Optional[str] = Field(default=None, max_length=100)
    skills: Optional[str] = None
    source: Optional[str] = Field(default=None, max_length=50)
    referral_employee_id: Optional[UUID] = None
    cover_letter: Optional[str] = None
    resume_url: Optional[str] = Field(default=None, max_length=500)


class JobApplicantCreate(JobApplicantBase):
    """Create job applicant request."""

    pass


class JobApplicantUpdate(BaseModel):
    """Update job applicant request."""

    first_name: Optional[str] = Field(default=None, max_length=80)
    last_name: Optional[str] = Field(default=None, max_length=80)
    email: Optional[str] = Field(default=None, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=40)
    date_of_birth: Optional[date] = None
    gender: Optional[str] = Field(default=None, max_length=20)
    city: Optional[str] = Field(default=None, max_length=80)
    country_code: Optional[str] = Field(default=None, max_length=2)
    current_employer: Optional[str] = Field(default=None, max_length=200)
    current_job_title: Optional[str] = Field(default=None, max_length=200)
    years_of_experience: Optional[int] = None
    highest_qualification: Optional[str] = Field(default=None, max_length=100)
    skills: Optional[str] = None
    source: Optional[str] = Field(default=None, max_length=50)
    cover_letter: Optional[str] = None
    resume_url: Optional[str] = Field(default=None, max_length=500)
    status: Optional[ApplicantStatus] = None
    overall_rating: Optional[int] = None
    notes: Optional[str] = None


class JobApplicantRead(JobApplicantBase):
    """Job applicant response."""

    model_config = ConfigDict(from_attributes=True)

    applicant_id: UUID
    organization_id: UUID
    application_number: str
    applied_on: date
    status: ApplicantStatus
    overall_rating: Optional[int] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    job_opening: Optional[JobOpeningBrief] = None


class JobApplicantListResponse(BaseModel):
    """Paginated job applicant list response."""

    items: List[JobApplicantRead]
    total: int
    offset: int
    limit: int


class ApplicantStatusUpdateRequest(BaseModel):
    """Update applicant pipeline status."""

    status: ApplicantStatus
    notes: Optional[str] = None


class ApplicantStats(BaseModel):
    """Applicant statistics for a job opening."""

    job_opening_id: Optional[UUID] = None
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
    location: Optional[str] = Field(default=None, max_length=200)
    meeting_link: Optional[str] = Field(default=None, max_length=500)
    interviewer_id: UUID


class InterviewCreate(InterviewBase):
    """Create interview request."""

    pass


class InterviewUpdate(BaseModel):
    """Update interview request."""

    round: Optional[InterviewRound] = None
    interview_type: Optional[str] = None
    scheduled_from: Optional[datetime] = None
    scheduled_to: Optional[datetime] = None
    location: Optional[str] = Field(default=None, max_length=200)
    meeting_link: Optional[str] = Field(default=None, max_length=500)
    interviewer_id: Optional[UUID] = None
    status: Optional[InterviewStatus] = None


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
    actual_start: Optional[datetime] = None
    actual_end: Optional[datetime] = None
    rating: Optional[int] = None
    recommendation: Optional[str] = None
    feedback: Optional[str] = None
    strengths: Optional[str] = None
    weaknesses: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    applicant: Optional[ApplicantBrief] = None
    interviewer: Optional[EmployeeBrief] = None


class InterviewListResponse(BaseModel):
    """Paginated interview list response."""

    items: List[InterviewRead]
    total: int
    offset: int
    limit: int


class InterviewFeedbackRequest(BaseModel):
    """Submit interview feedback."""

    rating: int = Field(ge=1, le=5)
    recommendation: str = Field(description="STRONG_YES, YES, MAYBE, NO, STRONG_NO")
    feedback: Optional[str] = None
    strengths: Optional[str] = None
    weaknesses: Optional[str] = None


class InterviewRescheduleRequest(BaseModel):
    """Reschedule interview request."""

    scheduled_from: datetime
    scheduled_to: datetime
    reason: Optional[str] = None


class InterviewCancelRequest(BaseModel):
    """Cancel interview request."""

    reason: Optional[str] = None


# =============================================================================
# Job Offer Schemas
# =============================================================================


class JobOfferBase(BaseModel):
    """Base job offer schema."""

    applicant_id: UUID
    job_opening_id: UUID
    designation_id: UUID
    department_id: Optional[UUID] = None
    offer_date: date
    valid_until: date
    expected_joining_date: date
    base_salary: Decimal
    currency_code: str = "NGN"
    pay_frequency: str = "MONTHLY"
    signing_bonus: Optional[Decimal] = None
    relocation_allowance: Optional[Decimal] = None
    other_benefits: Optional[str] = None
    employment_type: str = "FULL_TIME"
    probation_months: int = 3
    notice_period_days: int = 30
    terms_and_conditions: Optional[str] = None
    notes: Optional[str] = None


class JobOfferCreate(JobOfferBase):
    """Create job offer request."""

    pass


class JobOfferUpdate(BaseModel):
    """Update job offer request."""

    designation_id: Optional[UUID] = None
    department_id: Optional[UUID] = None
    offer_date: Optional[date] = None
    valid_until: Optional[date] = None
    expected_joining_date: Optional[date] = None
    base_salary: Optional[Decimal] = None
    currency_code: Optional[str] = None
    pay_frequency: Optional[str] = None
    signing_bonus: Optional[Decimal] = None
    relocation_allowance: Optional[Decimal] = None
    other_benefits: Optional[str] = None
    employment_type: Optional[str] = None
    probation_months: Optional[int] = None
    notice_period_days: Optional[int] = None
    terms_and_conditions: Optional[str] = None
    notes: Optional[str] = None


class JobOfferRead(JobOfferBase):
    """Job offer response."""

    model_config = ConfigDict(from_attributes=True)

    offer_id: UUID
    organization_id: UUID
    offer_number: str
    status: OfferStatus
    extended_on: Optional[date] = None
    responded_on: Optional[date] = None
    decline_reason: Optional[str] = None
    converted_to_employee_id: Optional[UUID] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    applicant: Optional[ApplicantBrief] = None
    job_opening: Optional[JobOpeningBrief] = None
    designation: Optional[DesignationBrief] = None
    department: Optional[DepartmentBrief] = None


class JobOfferListResponse(BaseModel):
    """Paginated job offer list response."""

    items: List[JobOfferRead]
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
    decline_reason: Optional[str] = None


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
