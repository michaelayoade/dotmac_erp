"""Job Description and Competency Services.

Services for managing job descriptions and competency frameworks.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import and_, select
from sqlalchemy.orm import Session, selectinload

from app.models.people.hr import (
    Competency,
    CompetencyCategory,
    Department,
    Designation,
    JobDescription,
    JobDescriptionCompetency,
    JobDescriptionStatus,
)
from app.services.common import PaginatedResult, PaginationParams, paginate
from app.services.people.hr.errors import ValidationError

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.auth import Principal

__all__ = [
    "CompetencyService",
    "JobDescriptionService",
    "CompetencyNotFoundError",
    "JobDescriptionNotFoundError",
]


# =============================================================================
# Exceptions
# =============================================================================


class JobDescriptionServiceError(Exception):
    """Base exception for job description service errors."""

    pass


class CompetencyNotFoundError(JobDescriptionServiceError):
    """Competency not found."""

    pass


class JobDescriptionNotFoundError(JobDescriptionServiceError):
    """Job description not found."""

    pass


class DuplicateCodeError(JobDescriptionServiceError):
    """Code already exists."""

    pass


# =============================================================================
# CompetencyService
# =============================================================================


class CompetencyService:
    """Service for managing competencies."""

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def get_competency(self, competency_id: uuid.UUID) -> Competency | None:
        """Get a competency by ID."""
        stmt = select(Competency).where(
            and_(
                Competency.competency_id == competency_id,
                Competency.organization_id == self.organization_id,
                Competency.is_deleted == False,
            )
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def get_competency_by_code(self, code: str) -> Competency | None:
        """Get a competency by code."""
        stmt = select(Competency).where(
            and_(
                Competency.competency_code == code,
                Competency.organization_id == self.organization_id,
                Competency.is_deleted == False,
            )
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def list_competencies(
        self,
        *,
        category: CompetencyCategory | None = None,
        is_active: bool | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[Competency]:
        """List competencies with filtering."""
        stmt = select(Competency).where(
            and_(
                Competency.organization_id == self.organization_id,
                Competency.is_deleted == False,
            )
        )

        if category:
            stmt = stmt.where(Competency.category == category)

        if is_active is not None:
            stmt = stmt.where(Competency.is_active == is_active)

        if search:
            search_filter = f"%{search}%"
            stmt = stmt.where(
                Competency.competency_name.ilike(search_filter)
                | Competency.competency_code.ilike(search_filter)
            )

        stmt = stmt.order_by(Competency.category, Competency.competency_name)

        if pagination:
            return paginate(self.db, stmt, pagination)

        results = self.db.execute(stmt).scalars().all()
        return PaginatedResult(
            items=list(results),
            total=len(results),
            offset=0,
            limit=len(results),
        )

    def create_competency(
        self,
        *,
        competency_code: str,
        competency_name: str,
        category: CompetencyCategory,
        description: str | None = None,
        level_1_description: str | None = None,
        level_2_description: str | None = None,
        level_3_description: str | None = None,
        level_4_description: str | None = None,
        level_5_description: str | None = None,
        is_active: bool = True,
    ) -> Competency:
        """Create a new competency."""
        # Check for duplicate code
        existing = self.get_competency_by_code(competency_code)
        if existing:
            raise DuplicateCodeError(
                f"Competency with code '{competency_code}' already exists"
            )

        competency = Competency(
            organization_id=self.organization_id,
            competency_code=competency_code,
            competency_name=competency_name,
            category=category,
            description=description,
            level_1_description=level_1_description,
            level_2_description=level_2_description,
            level_3_description=level_3_description,
            level_4_description=level_4_description,
            level_5_description=level_5_description,
            is_active=is_active,
        )

        if self.principal:
            competency.created_by_id = self.principal.user_id

        self.db.add(competency)
        return competency

    def update_competency(
        self,
        competency_id: uuid.UUID,
        data: dict,
    ) -> Competency:
        """Update a competency."""
        competency = self.get_competency(competency_id)
        if not competency:
            raise CompetencyNotFoundError(f"Competency {competency_id} not found")

        # Check for code uniqueness if changing code
        if (
            "competency_code" in data
            and data["competency_code"] != competency.competency_code
        ):
            existing = self.get_competency_by_code(data["competency_code"])
            if existing:
                raise DuplicateCodeError(
                    f"Competency with code '{data['competency_code']}' already exists"
                )

        allowed_fields = {
            "competency_code",
            "competency_name",
            "category",
            "description",
            "level_1_description",
            "level_2_description",
            "level_3_description",
            "level_4_description",
            "level_5_description",
            "is_active",
        }
        for key, value in data.items():
            if key in allowed_fields:
                setattr(competency, key, value)

        if self.principal:
            competency.updated_by_id = self.principal.user_id

        return competency

    def delete_competency(self, competency_id: uuid.UUID) -> None:
        """Soft delete a competency."""
        competency = self.get_competency(competency_id)
        if not competency:
            raise CompetencyNotFoundError(f"Competency {competency_id} not found")

        competency.is_deleted = True
        competency.deleted_at = datetime.now(UTC)
        if self.principal:
            competency.deleted_by_id = self.principal.user_id


# =============================================================================
# JobDescriptionService
# =============================================================================


class JobDescriptionService:
    """Service for managing job descriptions."""

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        principal: Principal | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.principal = principal

    def _validate_org_reference(
        self,
        model: type,
        entity_id: uuid.UUID | None,
        label: str,
    ) -> None:
        if entity_id is None:
            return
        record = self.db.get(model, entity_id)
        if (
            not record
            or getattr(record, "organization_id", None) != self.organization_id
        ):
            raise ValidationError(f"{label} {entity_id} not found")
        if getattr(record, "is_deleted", False):
            raise ValidationError(f"{label} {entity_id} not found")

    def get_job_description(
        self, jd_id: uuid.UUID, *, load_competencies: bool = False
    ) -> JobDescription | None:
        """Get a job description by ID."""
        stmt = select(JobDescription).where(
            and_(
                JobDescription.job_description_id == jd_id,
                JobDescription.organization_id == self.organization_id,
                JobDescription.is_deleted == False,
            )
        )

        if load_competencies:
            stmt = stmt.options(
                selectinload(JobDescription.competencies).selectinload(
                    JobDescriptionCompetency.competency
                )
            )

        return self.db.execute(stmt).scalar_one_or_none()

    def get_job_description_by_code(self, code: str) -> JobDescription | None:
        """Get a job description by code."""
        stmt = select(JobDescription).where(
            and_(
                JobDescription.jd_code == code,
                JobDescription.organization_id == self.organization_id,
                JobDescription.is_deleted == False,
            )
        )
        return self.db.execute(stmt).scalar_one_or_none()

    def list_job_descriptions(
        self,
        *,
        designation_id: uuid.UUID | None = None,
        department_id: uuid.UUID | None = None,
        status: JobDescriptionStatus | None = None,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[JobDescription]:
        """List job descriptions with filtering."""
        stmt = (
            select(JobDescription)
            .where(
                and_(
                    JobDescription.organization_id == self.organization_id,
                    JobDescription.is_deleted == False,
                )
            )
            .options(
                selectinload(JobDescription.designation),
                selectinload(JobDescription.department),
            )
        )

        if designation_id:
            stmt = stmt.where(JobDescription.designation_id == designation_id)

        if department_id:
            stmt = stmt.where(JobDescription.department_id == department_id)

        if status:
            stmt = stmt.where(JobDescription.status == status)

        if search:
            search_filter = f"%{search}%"
            stmt = stmt.where(
                JobDescription.job_title.ilike(search_filter)
                | JobDescription.jd_code.ilike(search_filter)
            )

        stmt = stmt.order_by(JobDescription.job_title)

        if pagination:
            return paginate(self.db, stmt, pagination)

        results = self.db.execute(stmt).scalars().all()
        return PaginatedResult(
            items=list(results),
            total=len(results),
            offset=0,
            limit=len(results),
        )

    def create_job_description(
        self,
        *,
        jd_code: str,
        job_title: str,
        designation_id: uuid.UUID,
        department_id: uuid.UUID | None = None,
        summary: str | None = None,
        purpose: str | None = None,
        key_responsibilities: str | None = None,
        education_requirements: str | None = None,
        experience_requirements: str | None = None,
        min_years_experience: int | None = None,
        max_years_experience: int | None = None,
        technical_skills: str | None = None,
        certifications_required: str | None = None,
        certifications_preferred: str | None = None,
        work_location: str | None = None,
        travel_requirements: str | None = None,
        physical_requirements: str | None = None,
        reports_to: str | None = None,
        direct_reports: str | None = None,
        additional_notes: str | None = None,
        status: JobDescriptionStatus = JobDescriptionStatus.DRAFT,
        effective_from: date | None = None,
    ) -> JobDescription:
        """Create a new job description."""
        # Check for duplicate code
        existing = self.get_job_description_by_code(jd_code)
        if existing:
            raise DuplicateCodeError(
                f"Job description with code '{jd_code}' already exists"
            )

        self._validate_org_reference(Designation, designation_id, "Designation")
        self._validate_org_reference(Department, department_id, "Department")

        jd = JobDescription(
            organization_id=self.organization_id,
            jd_code=jd_code,
            job_title=job_title,
            designation_id=designation_id,
            department_id=department_id,
            summary=summary,
            purpose=purpose,
            key_responsibilities=key_responsibilities,
            education_requirements=education_requirements,
            experience_requirements=experience_requirements,
            min_years_experience=min_years_experience,
            max_years_experience=max_years_experience,
            technical_skills=technical_skills,
            certifications_required=certifications_required,
            certifications_preferred=certifications_preferred,
            work_location=work_location,
            travel_requirements=travel_requirements,
            physical_requirements=physical_requirements,
            reports_to=reports_to,
            direct_reports=direct_reports,
            additional_notes=additional_notes,
            status=status,
            effective_from=effective_from,
        )

        if self.principal:
            jd.created_by_id = self.principal.user_id

        self.db.add(jd)
        return jd

    def update_job_description(
        self,
        jd_id: uuid.UUID,
        data: dict,
    ) -> JobDescription:
        """Update a job description."""
        jd = self.get_job_description(jd_id)
        if not jd:
            raise JobDescriptionNotFoundError(f"Job description {jd_id} not found")

        # Check for code uniqueness if changing code
        if "jd_code" in data and data["jd_code"] != jd.jd_code:
            existing = self.get_job_description_by_code(data["jd_code"])
            if existing:
                raise DuplicateCodeError(
                    f"Job description with code '{data['jd_code']}' already exists"
                )

        if "designation_id" in data:
            self._validate_org_reference(
                Designation, data["designation_id"], "Designation"
            )
        if "department_id" in data:
            self._validate_org_reference(
                Department, data["department_id"], "Department"
            )

        allowed_fields = {
            "jd_code",
            "job_title",
            "designation_id",
            "department_id",
            "summary",
            "purpose",
            "key_responsibilities",
            "education_requirements",
            "experience_requirements",
            "min_years_experience",
            "max_years_experience",
            "technical_skills",
            "certifications_required",
            "certifications_preferred",
            "work_location",
            "travel_requirements",
            "physical_requirements",
            "reports_to",
            "direct_reports",
            "additional_notes",
            "status",
            "effective_from",
            "effective_to",
        }
        for key, value in data.items():
            if key in allowed_fields:
                setattr(jd, key, value)

        if self.principal:
            jd.updated_by_id = self.principal.user_id

        return jd

    def delete_job_description(self, jd_id: uuid.UUID) -> None:
        """Soft delete a job description."""
        jd = self.get_job_description(jd_id)
        if not jd:
            raise JobDescriptionNotFoundError(f"Job description {jd_id} not found")

        jd.is_deleted = True
        jd.deleted_at = datetime.now(UTC)
        if self.principal:
            jd.deleted_by_id = self.principal.user_id

    def activate_job_description(self, jd_id: uuid.UUID) -> JobDescription:
        """Activate a job description."""
        jd = self.get_job_description(jd_id)
        if not jd:
            raise JobDescriptionNotFoundError(f"Job description {jd_id} not found")

        jd.status = JobDescriptionStatus.ACTIVE
        if not jd.effective_from:
            jd.effective_from = date.today()

        if self.principal:
            jd.updated_by_id = self.principal.user_id

        return jd

    def archive_job_description(self, jd_id: uuid.UUID) -> JobDescription:
        """Archive a job description."""
        jd = self.get_job_description(jd_id)
        if not jd:
            raise JobDescriptionNotFoundError(f"Job description {jd_id} not found")

        jd.status = JobDescriptionStatus.ARCHIVED
        jd.effective_to = date.today()

        if self.principal:
            jd.updated_by_id = self.principal.user_id

        return jd

    # -------------------------------------------------------------------------
    # Competency Management for Job Descriptions
    # -------------------------------------------------------------------------

    def add_competency(
        self,
        jd_id: uuid.UUID,
        competency_id: uuid.UUID,
        *,
        required_level: int = 3,
        weight: float | None = None,
        is_mandatory: bool = True,
        notes: str | None = None,
    ) -> JobDescriptionCompetency:
        """Add a competency requirement to a job description."""
        jd = self.get_job_description(jd_id)
        if not jd:
            raise JobDescriptionNotFoundError(f"Job description {jd_id} not found")
        self._validate_org_reference(Competency, competency_id, "Competency")

        # Check if competency already exists for this JD
        stmt = select(JobDescriptionCompetency).where(
            and_(
                JobDescriptionCompetency.job_description_id == jd_id,
                JobDescriptionCompetency.competency_id == competency_id,
            )
        )
        existing = self.db.execute(stmt).scalar_one_or_none()
        if existing:
            # Update existing
            existing.required_level = required_level
            if weight is not None:
                existing.weight = Decimal(str(weight))
            existing.is_mandatory = is_mandatory
            existing.notes = notes
            return existing

        jd_competency = JobDescriptionCompetency(
            job_description_id=jd_id,
            competency_id=competency_id,
            required_level=required_level,
            weight=Decimal(str(weight)) if weight is not None else None,
            is_mandatory=is_mandatory,
            notes=notes,
        )

        if self.principal:
            jd_competency.created_by_id = self.principal.user_id

        self.db.add(jd_competency)
        return jd_competency

    def remove_competency(
        self,
        jd_id: uuid.UUID,
        competency_id: uuid.UUID,
    ) -> None:
        """Remove a competency from a job description."""
        stmt = select(JobDescriptionCompetency).where(
            and_(
                JobDescriptionCompetency.job_description_id == jd_id,
                JobDescriptionCompetency.competency_id == competency_id,
            )
        )
        jd_competency = self.db.execute(stmt).scalar_one_or_none()
        if jd_competency:
            self.db.delete(jd_competency)

    def list_competencies_for_jd(
        self, jd_id: uuid.UUID
    ) -> list[JobDescriptionCompetency]:
        """List all competencies for a job description."""
        stmt = (
            select(JobDescriptionCompetency)
            .where(JobDescriptionCompetency.job_description_id == jd_id)
            .options(selectinload(JobDescriptionCompetency.competency))
            .order_by(JobDescriptionCompetency.is_mandatory.desc())
        )
        return list(self.db.execute(stmt).scalars().all())
