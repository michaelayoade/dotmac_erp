"""
Careers web service - handles all logic for the public careers portal.

This service handles:
- Job listing and detail retrieval
- Application form submission
- Status verification requests
- Resume uploads
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.finance.core_org.organization import Organization
from app.models.people.recruit.job_opening import JobOpening
from app.services.careers.captcha import (
    get_captcha_site_key,
    is_captcha_enabled,
    verify_captcha,
)
from app.services.careers.careers_service import (
    ApplicationSubmissionError,
    CareersService,
    JobNotFoundError,
)
from app.services.careers.resume_service import (
    FileTooLargeError,
    InvalidFileTypeError,
    ResumeService,
)

logger = logging.getLogger(__name__)


@dataclass
class OrganizationContext:
    """Context data for a careers portal organization."""

    org: Organization
    org_id: uuid.UUID
    org_slug: str
    org_name: str
    org_logo: Optional[str]
    brand: dict


@dataclass
class JobListResult:
    """Result of job listing query."""

    jobs: list[JobOpening]
    total: int
    page: int
    page_size: int
    total_pages: int
    departments: list[tuple[uuid.UUID, str, int]]
    locations: list[str]


@dataclass
class ApplicationResult:
    """Result of application submission."""

    success: bool
    application_number: Optional[str] = None
    error: Optional[str] = None


class CareersWebService:
    """
    Web service for the public careers portal.

    Encapsulates all business logic for careers routes.
    """

    def __init__(self, db: Session):
        self.db = db
        self._careers_service = CareersService(db)
        self._resume_service = ResumeService()

    def get_organization_context(self, slug: str) -> Optional[OrganizationContext]:
        """
        Get organization context for template rendering.

        Returns None if organization not found.
        """
        org = self._careers_service.get_organization_by_slug(slug)
        if not org:
            return None

        org_name = org.trading_name or org.legal_name
        return OrganizationContext(
            org=org,
            org_id=org.organization_id,
            org_slug=slug,
            org_name=org_name,
            org_logo=org.logo_url,
            brand={
                "name": org_name,
                "tagline": "Careers",
                "logo_url": org.logo_url,
                "mark": (org_name or "C")[:2].upper(),
            },
        )

    def list_jobs(
        self,
        org_id: uuid.UUID,
        *,
        search: Optional[str] = None,
        department_id: Optional[list[uuid.UUID]] = None,
        location: Optional[str] = None,
        employment_type: Optional[str] = None,
        is_remote: Optional[bool] = None,
        page: int = 1,
        page_size: int = 12,
    ) -> JobListResult:
        """
        List open jobs with filters and pagination.
        """
        page = max(1, page)
        page_size = min(max(1, page_size), 50)
        offset = (page - 1) * page_size

        jobs, total = self._careers_service.list_open_jobs(
            org_id,
            search=search,
            department_id=department_id,
            location=location,
            employment_type=employment_type,
            is_remote=is_remote,
            limit=page_size,
            offset=offset,
        )

        total_pages = (total + page_size - 1) // page_size if total > 0 else 1
        departments = self._careers_service.get_departments_with_openings(org_id)
        locations = self._careers_service.get_locations_with_openings(org_id)

        return JobListResult(
            jobs=jobs,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            departments=departments,
            locations=locations,
        )

    def get_job_by_code(self, org_id: uuid.UUID, job_code: str) -> Optional[JobOpening]:
        """Get a job opening by its code."""
        return self._careers_service.get_job_by_code(org_id, job_code)

    def get_public_job(
        self, org_id: uuid.UUID, job_id: uuid.UUID
    ) -> Optional[JobOpening]:
        """Get a job opening by ID."""
        return self._careers_service.get_public_job(org_id, job_id)

    async def upload_resume(
        self,
        org_id: uuid.UUID,
        filename: str,
        content: bytes,
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Upload a resume file.

        Returns (file_id, error_message).
        """
        try:
            file_id, _ = self._resume_service.save_resume(org_id, filename, content)
            return file_id, None
        except InvalidFileTypeError as e:
            return None, str(e)
        except FileTooLargeError as e:
            return None, str(e)

    async def submit_application(
        self,
        org_id: uuid.UUID,
        job_code: str,
        *,
        first_name: str,
        last_name: str,
        email: str,
        phone: Optional[str] = None,
        resume_file_id: Optional[str] = None,
        cover_letter: Optional[str] = None,
        current_employer: Optional[str] = None,
        current_job_title: Optional[str] = None,
        years_of_experience: Optional[int] = None,
        highest_qualification: Optional[str] = None,
        skills: Optional[str] = None,
        city: Optional[str] = None,
        country_code: Optional[str] = None,
        captcha_token: Optional[str] = None,
        client_ip: Optional[str] = None,
    ) -> ApplicationResult:
        """
        Submit a job application with all validations.
        """
        # Get job by code
        job = self._careers_service.get_job_by_code(org_id, job_code)
        if not job:
            return ApplicationResult(
                success=False,
                error="Job not found or no longer accepting applications",
            )

        # Verify CAPTCHA if enabled
        if is_captcha_enabled():
            if not captcha_token:
                return ApplicationResult(
                    success=False,
                    error="Please complete the CAPTCHA verification.",
                )
            is_valid = await verify_captcha(captcha_token, client_ip)
            if not is_valid:
                return ApplicationResult(
                    success=False,
                    error="CAPTCHA verification failed. Please try again.",
                )

        # Submit application
        try:
            applicant = self._careers_service.submit_application(
                org_id,
                job.job_opening_id,
                first_name=first_name,
                last_name=last_name,
                email=email,
                phone=phone,
                resume_file_id=resume_file_id,
                cover_letter=cover_letter,
                current_employer=current_employer,
                current_job_title=current_job_title,
                years_of_experience=years_of_experience,
                highest_qualification=highest_qualification,
                skills=skills,
                city=city,
                country_code=country_code,
            )

            # Get org for confirmation email
            org = self.db.get(Organization, org_id)
            if org:
                try:
                    self._careers_service.send_application_confirmation(applicant, org)
                except Exception as e:
                    logger.warning("Failed to send confirmation email: %s", e)

            self.db.commit()

            return ApplicationResult(
                success=True,
                application_number=applicant.application_number,
            )

        except JobNotFoundError as e:
            return ApplicationResult(success=False, error=str(e))
        except ApplicationSubmissionError as e:
            return ApplicationResult(success=False, error=str(e))

    def request_status_check(
        self,
        org_id: uuid.UUID,
        email: str,
        application_number: Optional[str] = None,
    ) -> bool:
        """
        Request application status check via email.

        Always returns True to prevent email enumeration.
        """
        self._careers_service.request_status_check(
            org_id,
            email=email,
            application_number=application_number,
        )
        self.db.commit()
        return True

    def verify_status_token(self, org_id: uuid.UUID, token: str) -> Optional[dict]:
        """
        Verify status token and return application details.
        """
        return self._careers_service.verify_status_token(org_id, token)

    def get_captcha_config(self) -> dict:
        """Get CAPTCHA configuration for client-side rendering."""
        return {
            "enabled": is_captcha_enabled(),
            "site_key": get_captcha_site_key() if is_captcha_enabled() else None,
        }
