"""Tests for the Careers Service."""

import uuid
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.models.finance.core_org.organization import Organization
from app.models.people.hr.department import Department
from app.models.people.recruit.job_applicant import ApplicantStatus, JobApplicant
from app.models.people.recruit.job_opening import JobOpening, JobOpeningStatus
from app.services.careers.careers_service import (
    ApplicationSubmissionError,
    CareersService,
    JobNotFoundError,
)


@pytest.fixture
def org_with_slug(db: Session) -> Organization:
    """Create an organization with a slug for testing."""
    org = Organization(
        organization_code="TESTORG",
        slug="test-company",
        legal_name="Test Company Ltd",
        trading_name="Test Company",
        functional_currency_code="NGN",
        presentation_currency_code="NGN",
        fiscal_year_end_month=12,
        fiscal_year_end_day=31,
        is_active=True,
    )
    db.add(org)
    db.flush()
    return org


@pytest.fixture
def department(db: Session, org_with_slug: Organization) -> Department:
    """Create a test department."""
    dept = Department(
        organization_id=org_with_slug.organization_id,
        department_code="ENG",
        department_name="Engineering",
        is_active=True,
    )
    db.add(dept)
    db.flush()
    return dept


@pytest.fixture
def open_job(db: Session, org_with_slug: Organization, department: Department) -> JobOpening:
    """Create an open job for testing."""
    job = JobOpening(
        organization_id=org_with_slug.organization_id,
        job_code="JOB-2024-001",
        job_title="Software Engineer",
        description="We're looking for a talented software engineer.",
        department_id=department.department_id,
        number_of_positions=2,
        positions_filled=0,
        employment_type="FULL_TIME",
        location="Lagos",
        is_remote=True,
        status=JobOpeningStatus.OPEN,
        posted_on=date.today(),
    )
    db.add(job)
    db.flush()
    return job


class TestCareersService:
    """Test cases for CareersService."""

    def test_get_organization_by_slug(self, db: Session, org_with_slug: Organization):
        """Test finding organization by slug."""
        service = CareersService(db)

        # Found
        org = service.get_organization_by_slug("test-company")
        assert org is not None
        assert org.organization_id == org_with_slug.organization_id

        # Not found
        org = service.get_organization_by_slug("nonexistent")
        assert org is None

    def test_list_open_jobs(
        self, db: Session, org_with_slug: Organization, open_job: JobOpening
    ):
        """Test listing open jobs."""
        service = CareersService(db)

        jobs, total = service.list_open_jobs(org_with_slug.organization_id)
        assert total == 1
        assert len(jobs) == 1
        assert jobs[0].job_code == "JOB-2024-001"

    def test_list_open_jobs_filters(
        self,
        db: Session,
        org_with_slug: Organization,
        department: Department,
        open_job: JobOpening,
    ):
        """Test job listing with filters."""
        service = CareersService(db)

        # Search filter
        jobs, total = service.list_open_jobs(
            org_with_slug.organization_id, search="software"
        )
        assert total == 1

        jobs, total = service.list_open_jobs(
            org_with_slug.organization_id, search="manager"
        )
        assert total == 0

        # Department filter
        jobs, total = service.list_open_jobs(
            org_with_slug.organization_id, department_id=department.department_id
        )
        assert total == 1

        # Location filter
        jobs, total = service.list_open_jobs(
            org_with_slug.organization_id, location="Lagos"
        )
        assert total == 1

        # Remote filter
        jobs, total = service.list_open_jobs(
            org_with_slug.organization_id, is_remote=True
        )
        assert total == 1

        jobs, total = service.list_open_jobs(
            org_with_slug.organization_id, is_remote=False
        )
        assert total == 0

    def test_get_public_job(
        self, db: Session, org_with_slug: Organization, open_job: JobOpening
    ):
        """Test getting a single job for public view."""
        service = CareersService(db)

        job = service.get_public_job(
            org_with_slug.organization_id, open_job.job_opening_id
        )
        assert job is not None
        assert job.job_title == "Software Engineer"

        # Wrong org
        job = service.get_public_job(uuid.uuid4(), open_job.job_opening_id)
        assert job is None

    def test_get_job_by_code(
        self, db: Session, org_with_slug: Organization, open_job: JobOpening
    ):
        """Test getting job by code."""
        service = CareersService(db)

        job = service.get_job_by_code(org_with_slug.organization_id, "JOB-2024-001")
        assert job is not None
        assert job.job_opening_id == open_job.job_opening_id

        # Not found
        job = service.get_job_by_code(org_with_slug.organization_id, "INVALID")
        assert job is None

    def test_submit_application(
        self, db: Session, org_with_slug: Organization, open_job: JobOpening
    ):
        """Test submitting a job application."""
        service = CareersService(db)

        applicant = service.submit_application(
            org_with_slug.organization_id,
            open_job.job_opening_id,
            first_name="John",
            last_name="Doe",
            email="john.doe@example.com",
            phone="+234 800 123 4567",
            years_of_experience=5,
        )

        assert applicant.applicant_id is not None
        assert applicant.application_number.startswith("APP-")
        assert applicant.first_name == "John"
        assert applicant.last_name == "Doe"
        assert applicant.email == "john.doe@example.com"
        assert applicant.status == ApplicantStatus.NEW
        assert applicant.source == "WEBSITE"

    def test_submit_application_duplicate(
        self, db: Session, org_with_slug: Organization, open_job: JobOpening
    ):
        """Test that duplicate applications are rejected."""
        service = CareersService(db)

        # First application succeeds
        service.submit_application(
            org_with_slug.organization_id,
            open_job.job_opening_id,
            first_name="John",
            last_name="Doe",
            email="john.doe@example.com",
        )

        # Second application with same email fails
        with pytest.raises(ApplicationSubmissionError) as exc_info:
            service.submit_application(
                org_with_slug.organization_id,
                open_job.job_opening_id,
                first_name="Johnny",
                last_name="Doe",
                email="john.doe@example.com",  # Same email
            )

        assert "already applied" in str(exc_info.value)

    def test_submit_application_job_not_found(
        self, db: Session, org_with_slug: Organization
    ):
        """Test application to non-existent job fails."""
        service = CareersService(db)

        with pytest.raises(JobNotFoundError):
            service.submit_application(
                org_with_slug.organization_id,
                uuid.uuid4(),  # Non-existent job
                first_name="John",
                last_name="Doe",
                email="john.doe@example.com",
            )

    def test_request_status_check(
        self, db: Session, org_with_slug: Organization, open_job: JobOpening
    ):
        """Test requesting status check email."""
        service = CareersService(db)

        # Create an applicant
        applicant = service.submit_application(
            org_with_slug.organization_id,
            open_job.job_opening_id,
            first_name="Jane",
            last_name="Smith",
            email="jane.smith@example.com",
        )
        db.flush()

        # Request status check
        result = service.request_status_check(
            org_with_slug.organization_id,
            email="jane.smith@example.com",
        )
        assert result is True

        # Refresh applicant
        db.refresh(applicant)
        assert applicant.verification_token is not None
        assert applicant.verification_token_expires is not None

    def test_verify_status_token(
        self, db: Session, org_with_slug: Organization, open_job: JobOpening
    ):
        """Test verifying status token."""
        service = CareersService(db)

        # Create applicant and get token
        applicant = service.submit_application(
            org_with_slug.organization_id,
            open_job.job_opening_id,
            first_name="Jane",
            last_name="Smith",
            email="jane.smith@example.com",
        )
        db.flush()

        service.request_status_check(
            org_with_slug.organization_id,
            email="jane.smith@example.com",
        )
        db.flush()
        db.refresh(applicant)

        token = applicant.verification_token
        assert token is not None

        # Verify token
        status = service.verify_status_token(org_with_slug.organization_id, token)
        assert status is not None
        assert status["application_number"] == applicant.application_number
        assert status["job_title"] == "Software Engineer"
        assert status["status"] == "NEW"

        # Invalid token
        status = service.verify_status_token(org_with_slug.organization_id, "invalid")
        assert status is None

    def test_get_departments_with_openings(
        self,
        db: Session,
        org_with_slug: Organization,
        department: Department,
        open_job: JobOpening,
    ):
        """Test getting departments with openings."""
        service = CareersService(db)

        departments = service.get_departments_with_openings(org_with_slug.organization_id)
        assert len(departments) == 1
        assert departments[0][1] == "Engineering"
        assert departments[0][2] == 1  # count

    def test_get_locations_with_openings(
        self, db: Session, org_with_slug: Organization, open_job: JobOpening
    ):
        """Test getting locations with openings."""
        service = CareersService(db)

        locations = service.get_locations_with_openings(org_with_slug.organization_id)
        assert "Lagos" in locations
