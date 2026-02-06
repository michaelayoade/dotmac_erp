"""
Careers service for public job portal operations.

Provides methods for:
- Listing open job positions
- Submitting applications
- Status verification via email tokens
"""

import logging
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.finance.core_org.organization import Organization
from app.models.people.hr.department import Department
from app.models.people.recruit.job_applicant import ApplicantStatus, JobApplicant
from app.models.people.recruit.job_opening import JobOpening, JobOpeningStatus
from app.services.careers.candidate_notifications import CandidateNotificationService
from app.services.careers.resume_service import ResumeService

logger = logging.getLogger(__name__)

# Token expiration time
STATUS_TOKEN_EXPIRY_HOURS = 24


class CareersServiceError(Exception):
    """Base exception for careers service errors."""

    pass


class OrganizationNotFoundError(CareersServiceError):
    """Organization not found by slug."""

    pass


class JobNotFoundError(CareersServiceError):
    """Job opening not found or not available."""

    pass


class ApplicationSubmissionError(CareersServiceError):
    """Error during application submission."""

    pass


class CareersService:
    """
    Service for public careers portal operations.

    Provides read-only access to job listings and handles
    application submissions with proper validation.
    """

    def __init__(self, db: Session):
        self.db = db
        self.resume_service = ResumeService()
        self.notification_service = CandidateNotificationService()

    def get_organization_by_slug(self, slug: str) -> Optional[Organization]:
        """
        Get organization by its URL slug.

        Args:
            slug: Organization slug

        Returns:
            Organization if found, None otherwise
        """
        stmt = select(Organization).where(
            Organization.slug == slug,
            Organization.is_active == True,
        )
        org = self.db.scalar(stmt)
        if org:
            return org

        # Allow UUIDs in the public URL to resolve by organization_id.
        try:
            org_id = uuid.UUID(str(slug))
        except (ValueError, TypeError):
            return None

        stmt = select(Organization).where(
            Organization.organization_id == org_id,
            Organization.is_active == True,
        )
        return self.db.scalar(stmt)

    def list_open_jobs(
        self,
        org_id: uuid.UUID,
        *,
        search: Optional[str] = None,
        department_id: Optional[uuid.UUID] = None,
        location: Optional[str] = None,
        employment_type: Optional[str] = None,
        is_remote: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[JobOpening], int]:
        """
        List open job positions for an organization.

        Args:
            org_id: Organization UUID
            search: Optional search term for job title/description
            department_id: Filter by department
            location: Filter by location (partial match)
            employment_type: Filter by employment type
            is_remote: Filter remote jobs
            limit: Maximum results to return
            offset: Results offset for pagination

        Returns:
            Tuple of (jobs, total_count)
        """
        # Base query for open jobs
        base_conditions = [
            JobOpening.organization_id == org_id,
            JobOpening.status == JobOpeningStatus.OPEN,
            # Only show jobs with remaining positions
            JobOpening.positions_filled < JobOpening.number_of_positions,
        ]

        # Apply filters
        if search:
            search_term = f"%{search}%"
            base_conditions.append(
                or_(
                    JobOpening.job_title.ilike(search_term),
                    JobOpening.description.ilike(search_term),
                    JobOpening.job_code.ilike(search_term),
                )
            )

        if department_id:
            base_conditions.append(JobOpening.department_id == department_id)

        if location:
            base_conditions.append(JobOpening.location.ilike(f"%{location}%"))

        if employment_type:
            base_conditions.append(JobOpening.employment_type == employment_type)

        if is_remote is not None:
            base_conditions.append(JobOpening.is_remote == is_remote)

        # Get total count
        count_stmt = (
            select(func.count()).select_from(JobOpening).where(*base_conditions)
        )
        total = self.db.scalar(count_stmt) or 0

        # Get paginated results with eager loading
        stmt = (
            select(JobOpening)
            .options(joinedload(JobOpening.department))
            .where(*base_conditions)
            .order_by(JobOpening.posted_on.desc(), JobOpening.job_title)
            .limit(limit)
            .offset(offset)
        )

        jobs = list(self.db.scalars(stmt).unique().all())
        return jobs, total

    def get_public_job(
        self, org_id: uuid.UUID, job_id: uuid.UUID
    ) -> Optional[JobOpening]:
        """
        Get a single job opening for public view.

        Args:
            org_id: Organization UUID
            job_id: Job opening UUID

        Returns:
            JobOpening if found and open, None otherwise
        """
        stmt = (
            select(JobOpening)
            .options(joinedload(JobOpening.department))
            .where(
                JobOpening.organization_id == org_id,
                JobOpening.job_opening_id == job_id,
                JobOpening.status == JobOpeningStatus.OPEN,
            )
        )
        return self.db.scalar(stmt)

    def get_job_by_code(self, org_id: uuid.UUID, job_code: str) -> Optional[JobOpening]:
        """
        Get a job opening by its code (for public URLs).

        Args:
            org_id: Organization UUID
            job_code: Job code (e.g., "JOB-2024-001")

        Returns:
            JobOpening if found and open, None otherwise
        """
        stmt = (
            select(JobOpening)
            .options(joinedload(JobOpening.department))
            .where(
                JobOpening.organization_id == org_id,
                JobOpening.job_code == job_code,
                JobOpening.status == JobOpeningStatus.OPEN,
            )
        )
        return self.db.scalar(stmt)

    def get_departments_with_openings(
        self, org_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, str, int]]:
        """
        Get departments that have open positions.

        Args:
            org_id: Organization UUID

        Returns:
            List of (department_id, department_name, job_count) tuples
        """
        stmt = (
            select(
                Department.department_id,
                Department.department_name,
                func.count(JobOpening.job_opening_id).label("job_count"),
            )
            .select_from(JobOpening)
            .join(Department, JobOpening.department_id == Department.department_id)
            .where(
                JobOpening.organization_id == org_id,
                JobOpening.status == JobOpeningStatus.OPEN,
                JobOpening.positions_filled < JobOpening.number_of_positions,
            )
            .group_by(Department.department_id, Department.department_name)
            .order_by(Department.department_name)
        )

        results = self.db.execute(stmt).all()
        return [(r[0], r[1], r[2]) for r in results]

    def get_locations_with_openings(self, org_id: uuid.UUID) -> list[str]:
        """
        Get unique locations that have open positions.

        Args:
            org_id: Organization UUID

        Returns:
            List of location strings
        """
        stmt = (
            select(JobOpening.location)
            .where(
                JobOpening.organization_id == org_id,
                JobOpening.status == JobOpeningStatus.OPEN,
                JobOpening.positions_filled < JobOpening.number_of_positions,
                JobOpening.location.isnot(None),
                JobOpening.location != "",
            )
            .distinct()
            .order_by(JobOpening.location)
        )

        return [loc for loc in self.db.scalars(stmt).all() if loc]

    def _generate_application_number(self, org_id: uuid.UUID) -> str:
        """Generate a unique application number."""
        year = date.today().year

        # Count existing applications this year for this org
        stmt = (
            select(func.count())
            .select_from(JobApplicant)
            .where(
                JobApplicant.organization_id == org_id,
                JobApplicant.application_number.like(f"APP-{year}-%"),
            )
        )
        count = self.db.scalar(stmt) or 0

        return f"APP-{year}-{count + 1:05d}"

    def submit_application(
        self,
        org_id: uuid.UUID,
        job_id: uuid.UUID,
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
    ) -> JobApplicant:
        """
        Submit a job application.

        Args:
            org_id: Organization UUID
            job_id: Job opening UUID
            first_name: Applicant's first name
            last_name: Applicant's last name
            email: Applicant's email
            phone: Optional phone number
            resume_file_id: Optional uploaded resume file ID
            cover_letter: Optional cover letter text
            current_employer: Optional current employer
            current_job_title: Optional current job title
            years_of_experience: Optional years of experience
            highest_qualification: Optional education level
            skills: Optional comma-separated skills
            city: Optional city
            country_code: Optional 2-letter country code

        Returns:
            Created JobApplicant

        Raises:
            JobNotFoundError: If job doesn't exist or is closed
            ApplicationSubmissionError: If submission fails
        """
        # Verify job exists and is open
        job = self.get_public_job(org_id, job_id)
        if not job:
            raise JobNotFoundError(
                "Job position not found or no longer accepting applications"
            )

        # Check for duplicate application (same email + job)
        existing = self.db.scalar(
            select(JobApplicant).where(
                JobApplicant.organization_id == org_id,
                JobApplicant.job_opening_id == job_id,
                JobApplicant.email == email.lower(),
            )
        )
        if existing:
            raise ApplicationSubmissionError(
                "You have already applied for this position. "
                f"Your application number is {existing.application_number}."
            )

        # Build resume URL if file was uploaded
        resume_url = None
        if resume_file_id:
            resume_url = self.resume_service.get_resume_url(org_id, resume_file_id)

        # Create application
        application_number = self._generate_application_number(org_id)

        applicant = JobApplicant(
            organization_id=org_id,
            application_number=application_number,
            job_opening_id=job_id,
            first_name=first_name.strip(),
            last_name=last_name.strip(),
            email=email.lower().strip(),
            phone=phone.strip() if phone else None,
            resume_url=resume_url,
            cover_letter=cover_letter,
            current_employer=current_employer,
            current_job_title=current_job_title,
            years_of_experience=years_of_experience,
            highest_qualification=highest_qualification,
            skills=skills,
            city=city,
            country_code=country_code,
            applied_on=date.today(),
            source="WEBSITE",
            status=ApplicantStatus.NEW,
        )

        self.db.add(applicant)
        self.db.flush()

        logger.info(
            "Application submitted: %s for job %s (org %s)",
            application_number,
            job.job_code,
            org_id,
        )

        return applicant

    def request_status_check(
        self,
        org_id: uuid.UUID,
        email: str,
        application_number: Optional[str] = None,
    ) -> bool:
        """
        Request application status check via email verification.

        Sends a verification email with a token to access application status.

        Args:
            org_id: Organization UUID
            email: Applicant email
            application_number: Optional specific application number

        Returns:
            True if verification email was sent (or would be sent)
        """
        # Find applicant(s) by email
        conditions = [
            JobApplicant.organization_id == org_id,
            JobApplicant.email == email.lower(),
        ]
        if application_number:
            conditions.append(JobApplicant.application_number == application_number)

        stmt = select(JobApplicant).where(*conditions).limit(1)
        applicant = self.db.scalar(stmt)

        # Always return True to prevent email enumeration
        # But only actually send if found
        if not applicant:
            logger.debug("Status check requested for unknown email: %s", email)
            return True

        # Get organization for branding
        org = self.db.get(Organization, org_id)
        if not org:
            return True

        # Generate verification token
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + timedelta(
            hours=STATUS_TOKEN_EXPIRY_HOURS
        )

        # Update applicant with token
        applicant.verification_token = token
        applicant.verification_token_expires = expires
        self.db.flush()

        # Build verification URL
        verification_url = f"{settings.app_url}/careers/{self._public_org_identifier(org)}/status/{token}"

        # Send verification email
        self.notification_service.send_status_verification_email(
            db=self.db,
            applicant_email=applicant.email,
            applicant_name=applicant.first_name,
            verification_url=verification_url,
            org_name=org.legal_name or org.trading_name or "Our Company",
        )

        logger.info("Status verification email sent to %s", email)
        return True

    def verify_status_token(self, org_id: uuid.UUID, token: str) -> Optional[dict]:
        """
        Verify a status token and return application status.

        Args:
            org_id: Organization UUID
            token: Verification token

        Returns:
            Dict with application status details, or None if invalid
        """
        if not token:
            return None

        stmt = (
            select(JobApplicant)
            .options(joinedload(JobApplicant.job_opening))
            .where(
                JobApplicant.organization_id == org_id,
                JobApplicant.verification_token == token,
            )
        )
        applicant = self.db.scalar(stmt)

        if not applicant:
            return None

        # Check expiration
        if applicant.verification_token_expires:
            if datetime.now(timezone.utc) > applicant.verification_token_expires:
                logger.debug(
                    "Status token expired for %s", applicant.application_number
                )
                return None

        # Mark email as verified
        if not applicant.email_verified:
            applicant.email_verified = True
            self.db.flush()

        # Build status response (no sensitive data)
        return {
            "application_number": applicant.application_number,
            "job_title": applicant.job_opening.job_title
            if applicant.job_opening
            else "Unknown Position",
            "status": applicant.status.value,
            "status_display": self._format_status(applicant.status),
            "applied_on": applicant.applied_on.isoformat()
            if applicant.applied_on
            else None,
            "applicant_name": f"{applicant.first_name} {applicant.last_name}",
        }

    def _format_status(self, status: ApplicantStatus) -> str:
        """Format status enum for display."""
        status_labels = {
            ApplicantStatus.NEW: "Application Received",
            ApplicantStatus.SCREENING: "Under Review",
            ApplicantStatus.SHORTLISTED: "Shortlisted",
            ApplicantStatus.INTERVIEW_SCHEDULED: "Interview Scheduled",
            ApplicantStatus.INTERVIEW_COMPLETED: "Interview Completed",
            ApplicantStatus.SELECTED: "Selected",
            ApplicantStatus.OFFER_EXTENDED: "Offer Extended",
            ApplicantStatus.OFFER_ACCEPTED: "Offer Accepted",
            ApplicantStatus.OFFER_DECLINED: "Offer Declined",
            ApplicantStatus.HIRED: "Hired",
            ApplicantStatus.REJECTED: "Not Selected",
            ApplicantStatus.WITHDRAWN: "Withdrawn",
        }
        return status_labels.get(status, status.value.replace("_", " ").title())

    def _public_org_identifier(self, org: Organization) -> str:
        """Return slug if present, else fallback to organization_id for public URLs."""
        return org.slug or str(org.organization_id)

    def send_application_confirmation(
        self, applicant: JobApplicant, org: Organization
    ) -> bool:
        """
        Send application confirmation email.

        Args:
            applicant: The job applicant
            org: The organization

        Returns:
            True if email sent successfully
        """
        return self.notification_service.send_application_confirmation(
            db=self.db,
            applicant_email=applicant.email,
            applicant_name=applicant.first_name,
            job_title=applicant.job_opening.job_title
            if applicant.job_opening
            else "Position",
            application_number=applicant.application_number,
            org_name=org.legal_name or org.trading_name or "Our Company",
            org_slug=self._public_org_identifier(org),
        )
