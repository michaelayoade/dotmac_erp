"""
Pydantic schemas for public careers portal.

These schemas define the request/response models for the public-facing
careers API. They intentionally omit internal IDs and sensitive information.
"""

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ═══════════════════════════════════════════════════════════════════════════
# Job Listing Schemas (Public View)
# ═══════════════════════════════════════════════════════════════════════════


class PublicJobBrief(BaseModel):
    """Brief job listing for cards/lists."""

    model_config = ConfigDict(from_attributes=True)

    job_code: str = Field(..., description="Job reference code")
    job_title: str = Field(..., description="Position title")
    department_name: str | None = Field(None, description="Department name")
    location: str | None = Field(None, description="Job location")
    employment_type: str = Field(..., description="FULL_TIME, PART_TIME, etc.")
    is_remote: bool = Field(False, description="Remote work available")
    posted_on: date | None = Field(None, description="Date posted")
    closes_on: date | None = Field(None, description="Application deadline")


class PublicJobRead(BaseModel):
    """Full job details for detail page."""

    model_config = ConfigDict(from_attributes=True)

    job_code: str
    job_title: str
    description: str | None = None
    department_name: str | None = None
    location: str | None = None
    employment_type: str
    is_remote: bool = False
    min_experience_years: int | None = None
    required_skills: str | None = None
    preferred_skills: str | None = None
    education_requirements: str | None = None
    posted_on: date | None = None
    closes_on: date | None = None
    positions_remaining: int = 1


class PublicJobListResponse(BaseModel):
    """Paginated job list response."""

    jobs: list[PublicJobBrief]
    total: int
    page: int
    page_size: int
    total_pages: int


class DepartmentWithCount(BaseModel):
    """Department with open job count."""

    department_id: uuid.UUID
    department_name: str
    job_count: int


# ═══════════════════════════════════════════════════════════════════════════
# Application Schemas
# ═══════════════════════════════════════════════════════════════════════════


class ApplicationSubmitRequest(BaseModel):
    """Request to submit a job application."""

    first_name: str = Field(..., min_length=1, max_length=80)
    last_name: str = Field(..., min_length=1, max_length=80)
    email: EmailStr
    phone: str | None = Field(None, max_length=40)
    resume_file_id: str | None = Field(None, description="Uploaded resume file ID")
    cover_letter: str | None = Field(None, max_length=5000)
    current_employer: str | None = Field(None, max_length=200)
    current_job_title: str | None = Field(None, max_length=200)
    years_of_experience: int | None = Field(None, ge=0, le=50)
    highest_qualification: str | None = Field(None, max_length=100)
    skills: str | None = Field(
        None, max_length=1000, description="Comma-separated skills"
    )
    city: str | None = Field(None, max_length=80)
    country_code: str | None = Field(None, min_length=2, max_length=2)
    captcha_token: str | None = Field(None, description="CAPTCHA response token")

    @field_validator("first_name", "last_name")
    @classmethod
    def strip_whitespace(cls, v: str) -> str:
        return v.strip()

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower().strip()


class ApplicationSubmitResponse(BaseModel):
    """Response after successful application submission."""

    application_number: str = Field(..., description="Reference number for tracking")
    message: str = Field(..., description="Confirmation message")


# ═══════════════════════════════════════════════════════════════════════════
# Status Check Schemas
# ═══════════════════════════════════════════════════════════════════════════


class StatusCheckRequest(BaseModel):
    """Request to check application status."""

    email: EmailStr
    application_number: str | None = Field(
        None,
        description="Optional: specific application number",
    )


class StatusCheckResponse(BaseModel):
    """Response after status check request."""

    message: str = Field(
        ...,
        description="Always returns success message (prevents email enumeration)",
    )


class ApplicationStatusResponse(BaseModel):
    """Application status details (after verification)."""

    application_number: str
    job_title: str
    status: str = Field(..., description="Status code")
    status_display: str = Field(..., description="Human-readable status")
    applied_on: str | None = Field(None, description="Application date ISO format")
    applicant_name: str


# ═══════════════════════════════════════════════════════════════════════════
# Resume Upload Schemas
# ═══════════════════════════════════════════════════════════════════════════


class ResumeUploadResponse(BaseModel):
    """Response after successful resume upload."""

    file_id: str = Field(..., description="File ID for application submission")
    filename: str = Field(..., description="Original filename")
    message: str = Field(default="Resume uploaded successfully")


# ═══════════════════════════════════════════════════════════════════════════
# Organization Info Schemas
# ═══════════════════════════════════════════════════════════════════════════


class PublicOrganizationInfo(BaseModel):
    """Limited organization info for public careers page."""

    name: str = Field(..., description="Organization display name")
    logo_url: str | None = None
    website_url: str | None = None
