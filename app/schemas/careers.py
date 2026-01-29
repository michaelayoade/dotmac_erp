"""
Pydantic schemas for public careers portal.

These schemas define the request/response models for the public-facing
careers API. They intentionally omit internal IDs and sensitive information.
"""

from datetime import date
from typing import Optional
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ═══════════════════════════════════════════════════════════════════════════
# Job Listing Schemas (Public View)
# ═══════════════════════════════════════════════════════════════════════════


class PublicJobBrief(BaseModel):
    """Brief job listing for cards/lists."""

    model_config = ConfigDict(from_attributes=True)

    job_code: str = Field(..., description="Job reference code")
    job_title: str = Field(..., description="Position title")
    department_name: Optional[str] = Field(None, description="Department name")
    location: Optional[str] = Field(None, description="Job location")
    employment_type: str = Field(..., description="FULL_TIME, PART_TIME, etc.")
    is_remote: bool = Field(False, description="Remote work available")
    posted_on: Optional[date] = Field(None, description="Date posted")
    closes_on: Optional[date] = Field(None, description="Application deadline")


class PublicJobRead(BaseModel):
    """Full job details for detail page."""

    model_config = ConfigDict(from_attributes=True)

    job_code: str
    job_title: str
    description: Optional[str] = None
    department_name: Optional[str] = None
    location: Optional[str] = None
    employment_type: str
    is_remote: bool = False
    min_experience_years: Optional[int] = None
    required_skills: Optional[str] = None
    preferred_skills: Optional[str] = None
    education_requirements: Optional[str] = None
    posted_on: Optional[date] = None
    closes_on: Optional[date] = None
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
    phone: Optional[str] = Field(None, max_length=40)
    resume_file_id: Optional[str] = Field(None, description="Uploaded resume file ID")
    cover_letter: Optional[str] = Field(None, max_length=5000)
    current_employer: Optional[str] = Field(None, max_length=200)
    current_job_title: Optional[str] = Field(None, max_length=200)
    years_of_experience: Optional[int] = Field(None, ge=0, le=50)
    highest_qualification: Optional[str] = Field(None, max_length=100)
    skills: Optional[str] = Field(None, max_length=1000, description="Comma-separated skills")
    city: Optional[str] = Field(None, max_length=80)
    country_code: Optional[str] = Field(None, min_length=2, max_length=2)
    captcha_token: Optional[str] = Field(None, description="CAPTCHA response token")

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
    application_number: Optional[str] = Field(
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
    applied_on: Optional[str] = Field(None, description="Application date ISO format")
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
    logo_url: Optional[str] = None
    website_url: Optional[str] = None
