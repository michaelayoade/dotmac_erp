"""Dotmac Academy requirement and progress services."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.designation import Designation
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.employee_extended import EmployeeCertification
from app.models.people.training import (
    AcademyLearningProgress,
    AcademyLearningRequirement,
    AcademyProgressStatus,
)
from app.services.common import (
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = _clean(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(value: Any, default: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return default
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default


def _bounded_percentage(value: Any) -> Decimal:
    parsed = _decimal(value, Decimal("0.00")) or Decimal("0.00")
    return max(Decimal("0.00"), min(Decimal("100.00"), parsed))


def _is_complete(progress: AcademyLearningProgress | None) -> bool:
    if progress is None:
        return False
    if progress.passed is True:
        return True
    if progress.passed is False:
        return False
    return progress.status in {
        AcademyProgressStatus.PASSED,
        AcademyProgressStatus.COMPLETED,
        AcademyProgressStatus.CERTIFICATE_ISSUED,
    }


def _person_name(employee: Employee) -> str:
    person = employee.person
    if person is None:
        return employee.employee_code
    name = getattr(person, "name", None)
    if name:
        return str(name)
    return (
        " ".join(
            part
            for part in [
                getattr(person, "first_name", None),
                getattr(person, "last_name", None),
            ]
            if part
        )
        or employee.employee_code
    )


def _status_from_payload(payload: dict[str, Any]) -> AcademyProgressStatus:
    raw = _clean(payload.get("status") or payload.get("progress_status"))
    event_type = _clean(
        payload.get("event_type") or payload.get("event") or payload.get("type")
    )
    candidate = (raw or event_type or "").lower().replace("-", "_")
    aliases = {
        "course_assigned": AcademyProgressStatus.ASSIGNED,
        "course_started": AcademyProgressStatus.STARTED,
        "progress_updated": AcademyProgressStatus.IN_PROGRESS,
        "course_progress": AcademyProgressStatus.IN_PROGRESS,
        "assessment_started": AcademyProgressStatus.STARTED,
        "assessment_completed": AcademyProgressStatus.ASSESSMENT_TAKEN,
        "assessment_taken": AcademyProgressStatus.ASSESSMENT_TAKEN,
        "course_completed": AcademyProgressStatus.COMPLETED,
        "certificate_issued": AcademyProgressStatus.CERTIFICATE_ISSUED,
    }
    if candidate in aliases:
        return aliases[candidate]
    try:
        return AcademyProgressStatus(candidate)
    except ValueError:
        pass
    if payload.get("passed") is True:
        return AcademyProgressStatus.PASSED
    if payload.get("passed") is False:
        return AcademyProgressStatus.FAILED
    return AcademyProgressStatus.IN_PROGRESS


def _course_id_from_payload(payload: dict[str, Any]) -> str | None:
    return _clean(
        payload.get("academy_course_id")
        or payload.get("course_id")
        or payload.get("course_slug")
        or payload.get("course_ref")
        or payload.get("course_title")
    )


def _assessment_id_from_payload(payload: dict[str, Any]) -> str | None:
    return _clean(
        payload.get("academy_assessment_id")
        or payload.get("assessment_id")
        or payload.get("assessment_ref")
    )


class AcademyRequirementService:
    """Manage Academy requirements linked to HR designations."""

    def __init__(self, db: Session):
        self.db = db

    def list_requirements(
        self,
        org_id: UUID,
        *,
        designation_id: UUID | None = None,
        academy_course_id: str | None = None,
        include_inactive: bool = False,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AcademyLearningRequirement]:
        stmt = (
            select(AcademyLearningRequirement)
            .options(joinedload(AcademyLearningRequirement.designation))
            .where(AcademyLearningRequirement.organization_id == org_id)
            .order_by(
                AcademyLearningRequirement.created_at.desc(),
                AcademyLearningRequirement.academy_course_title.asc(),
            )
        )
        if designation_id:
            stmt = stmt.where(
                AcademyLearningRequirement.designation_id == designation_id
            )
        if academy_course_id:
            stmt = stmt.where(
                AcademyLearningRequirement.academy_course_id == academy_course_id
            )
        if not include_inactive:
            stmt = stmt.where(AcademyLearningRequirement.is_active.is_(True))
        return paginate(self.db, stmt, pagination)

    def create_requirement(
        self,
        org_id: UUID,
        *,
        designation_id: UUID,
        academy_course_id: str,
        academy_course_title: str,
        academy_assessment_id: str | None = None,
        academy_assessment_title: str | None = None,
        is_required: bool = True,
        notes: str | None = None,
        created_by: UUID | None = None,
    ) -> AcademyLearningRequirement:
        self._get_designation(org_id, designation_id)
        course_id = _clean(academy_course_id)
        course_title = _clean(academy_course_title)
        assessment_id = _clean(academy_assessment_id)
        if not course_id:
            raise ValidationError("Academy course ID is required")
        if not course_title:
            raise ValidationError("Academy course title is required")
        existing = self._find_requirement(
            org_id, designation_id, course_id, assessment_id
        )
        if existing:
            existing.academy_course_title = course_title
            existing.academy_assessment_title = _clean(academy_assessment_title)
            existing.is_required = is_required
            existing.is_active = True
            existing.notes = _clean(notes)
            self.db.flush()
            return existing
        requirement = AcademyLearningRequirement(
            organization_id=org_id,
            designation_id=designation_id,
            academy_course_id=course_id,
            academy_course_title=course_title,
            academy_assessment_id=assessment_id,
            academy_assessment_title=_clean(academy_assessment_title),
            is_required=is_required,
            notes=_clean(notes),
            created_by=created_by,
        )
        self.db.add(requirement)
        self.db.flush()
        return requirement

    def deactivate_requirement(
        self,
        org_id: UUID,
        requirement_id: UUID,
    ) -> AcademyLearningRequirement:
        requirement = self.get_requirement(org_id, requirement_id)
        requirement.is_active = False
        self.db.flush()
        return requirement

    def get_requirement(
        self,
        org_id: UUID,
        requirement_id: UUID,
    ) -> AcademyLearningRequirement:
        requirement = self.db.scalar(
            select(AcademyLearningRequirement).where(
                AcademyLearningRequirement.organization_id == org_id,
                AcademyLearningRequirement.id == requirement_id,
            )
        )
        if not requirement:
            raise NotFoundError("Academy requirement not found")
        return requirement

    def _get_designation(self, org_id: UUID, designation_id: UUID) -> Designation:
        designation = self.db.scalar(
            select(Designation).where(
                Designation.organization_id == org_id,
                Designation.designation_id == designation_id,
                Designation.is_active.is_(True),
            )
        )
        if not designation:
            raise NotFoundError("Designation not found")
        return designation

    def _find_requirement(
        self,
        org_id: UUID,
        designation_id: UUID,
        academy_course_id: str,
        academy_assessment_id: str | None,
    ) -> AcademyLearningRequirement | None:
        assessment_filter = (
            AcademyLearningRequirement.academy_assessment_id.is_(None)
            if academy_assessment_id is None
            else AcademyLearningRequirement.academy_assessment_id
            == academy_assessment_id
        )
        return self.db.scalar(
            select(AcademyLearningRequirement).where(
                AcademyLearningRequirement.organization_id == org_id,
                AcademyLearningRequirement.designation_id == designation_id,
                AcademyLearningRequirement.academy_course_id == academy_course_id,
                assessment_filter,
            )
        )


class AcademyProgressService:
    """Read and upsert Academy learner progress."""

    def __init__(self, db: Session):
        self.db = db

    def list_progress(
        self,
        org_id: UUID,
        *,
        employee_id: UUID | None = None,
        designation_id: UUID | None = None,
        academy_course_id: str | None = None,
        status: AcademyProgressStatus | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[AcademyLearningProgress]:
        stmt = (
            select(AcademyLearningProgress)
            .join(Employee, Employee.employee_id == AcademyLearningProgress.employee_id)
            .options(
                joinedload(AcademyLearningProgress.employee).joinedload(
                    Employee.person
                ),
                joinedload(AcademyLearningProgress.employee).joinedload(
                    Employee.designation
                ),
                joinedload(AcademyLearningProgress.requirement).joinedload(
                    AcademyLearningRequirement.designation
                ),
            )
            .where(AcademyLearningProgress.organization_id == org_id)
            .order_by(AcademyLearningProgress.last_synced_at.desc())
        )
        if employee_id:
            stmt = stmt.where(AcademyLearningProgress.employee_id == employee_id)
        if designation_id:
            stmt = stmt.where(Employee.designation_id == designation_id)
        if academy_course_id:
            stmt = stmt.where(
                AcademyLearningProgress.academy_course_id == academy_course_id
            )
        if status:
            stmt = stmt.where(AcademyLearningProgress.status == status)
        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=AcademyLearningProgress.id,
        )

    def upsert_from_payload(
        self,
        org_id: UUID,
        *,
        employee: Employee,
        payload: dict[str, Any],
        certification_id: UUID | None = None,
    ) -> AcademyLearningProgress:
        course_id = _course_id_from_payload(payload)
        if not course_id:
            raise ValidationError("Academy course ID or title is required")
        assessment_id = _assessment_id_from_payload(payload)
        requirement = self._match_requirement(
            org_id,
            employee.designation_id,
            course_id,
            assessment_id,
        )
        progress = self._find_progress(
            org_id, employee.employee_id, course_id, assessment_id
        )
        status = _status_from_payload(payload)
        now = _now()
        if progress is None:
            progress = AcademyLearningProgress(
                organization_id=org_id,
                employee_id=employee.employee_id,
                academy_course_id=course_id,
                academy_assessment_id=assessment_id,
                status=status,
            )
            self.db.add(progress)
        progress.requirement_id = requirement.id if requirement else None
        progress.academy_course_title = _clean(
            payload.get("academy_course_title") or payload.get("course_title")
        )
        progress.academy_assessment_title = _clean(
            payload.get("academy_assessment_title") or payload.get("assessment_title")
        )
        progress.status = status
        progress.progress_percentage = self._progress_percentage(payload, status)
        progress.score = _decimal(
            payload.get("score") or payload.get("assessment_score")
        )
        progress.passed = (
            payload.get("passed")
            if payload.get("passed") is not None
            else progress.passed
        )
        progress.started_at = (
            _parse_datetime(payload.get("started_at") or payload.get("started_on"))
            or progress.started_at
        )
        progress.completed_at = (
            _parse_datetime(payload.get("completed_at") or payload.get("completed_on"))
            or progress.completed_at
        )
        progress.last_activity_at = (
            _parse_datetime(
                payload.get("last_activity_at")
                or payload.get("activity_at")
                or payload.get("updated_at")
            )
            or progress.completed_at
            or progress.started_at
            or now
        )
        progress.last_synced_at = now
        progress.certificate_ref = _clean(
            payload.get("certificate_ref") or payload.get("certificate_id")
        )
        if certification_id:
            progress.certification_id = certification_id
        progress.raw_payload = payload
        self.db.flush()
        return progress

    def _progress_percentage(
        self,
        payload: dict[str, Any],
        status: AcademyProgressStatus,
    ) -> Decimal:
        explicit = (
            payload.get("progress_percentage")
            or payload.get("percent_complete")
            or payload.get("completion_percentage")
        )
        if explicit is not None:
            return _bounded_percentage(explicit)
        if status in {
            AcademyProgressStatus.PASSED,
            AcademyProgressStatus.COMPLETED,
            AcademyProgressStatus.CERTIFICATE_ISSUED,
        }:
            return Decimal("100.00")
        if status == AcademyProgressStatus.ASSIGNED:
            return Decimal("0.00")
        return Decimal("1.00")

    def _find_progress(
        self,
        org_id: UUID,
        employee_id: UUID,
        academy_course_id: str,
        academy_assessment_id: str | None,
    ) -> AcademyLearningProgress | None:
        assessment_filter = (
            AcademyLearningProgress.academy_assessment_id.is_(None)
            if academy_assessment_id is None
            else AcademyLearningProgress.academy_assessment_id == academy_assessment_id
        )
        return self.db.scalar(
            select(AcademyLearningProgress).where(
                AcademyLearningProgress.organization_id == org_id,
                AcademyLearningProgress.employee_id == employee_id,
                AcademyLearningProgress.academy_course_id == academy_course_id,
                assessment_filter,
            )
        )

    def _match_requirement(
        self,
        org_id: UUID,
        designation_id: UUID | None,
        academy_course_id: str,
        academy_assessment_id: str | None,
    ) -> AcademyLearningRequirement | None:
        if designation_id is None:
            return None
        assessment_match = (
            AcademyLearningRequirement.academy_assessment_id == academy_assessment_id
            if academy_assessment_id
            else AcademyLearningRequirement.academy_assessment_id.is_(None)
        )
        return self.db.scalar(
            select(AcademyLearningRequirement)
            .where(
                AcademyLearningRequirement.organization_id == org_id,
                AcademyLearningRequirement.designation_id == designation_id,
                AcademyLearningRequirement.academy_course_id == academy_course_id,
                AcademyLearningRequirement.is_active.is_(True),
                or_(
                    assessment_match,
                    AcademyLearningRequirement.academy_assessment_id.is_(None),
                ),
            )
            .order_by(
                and_(
                    AcademyLearningRequirement.academy_assessment_id.is_not(None),
                    AcademyLearningRequirement.academy_assessment_id
                    == academy_assessment_id,
                ).desc(),
                AcademyLearningRequirement.created_at.desc(),
            )
        )


class AcademyReportService:
    """Report on Academy requirement compliance and certification coverage."""

    def __init__(self, db: Session):
        self.db = db

    def compliance_by_designation(
        self,
        org_id: UUID,
        *,
        designation_id: UUID | None = None,
        academy_course_id: str | None = None,
    ) -> dict[str, Any]:
        requirements = self._requirements(
            org_id,
            designation_id=designation_id,
            academy_course_id=academy_course_id,
        )
        employees = self._employees_by_designation(org_id, designation_id)
        progress_by_key = self._progress_by_requirement_employee(org_id)
        rows: list[dict[str, Any]] = []
        total_expected = 0
        total_completed = 0

        for requirement in requirements:
            assigned_employees = employees.get(requirement.designation_id, [])
            expected = len(assigned_employees)
            completed = sum(
                1
                for employee in assigned_employees
                if _is_complete(
                    progress_by_key.get((requirement.id, employee.employee_id))
                )
            )
            total_expected += expected
            total_completed += completed
            rows.append(
                {
                    "designation": requirement.designation.designation_name,
                    "academy_course": requirement.academy_course_title,
                    "academy_course_id": requirement.academy_course_id,
                    "academy_assessment": requirement.academy_assessment_title
                    or requirement.academy_assessment_id
                    or "-",
                    "required_staff": expected,
                    "completed": completed,
                    "outstanding": expected - completed,
                    "compliance_rate": self._percentage(completed, expected),
                }
            )

        return {
            "summary": {
                "requirements": len(requirements),
                "expected_completions": total_expected,
                "completed": total_completed,
                "outstanding": total_expected - total_completed,
                "compliance_rate": self._percentage(total_completed, total_expected),
            },
            "rows": rows,
        }

    def missing_required_courses(
        self,
        org_id: UUID,
        *,
        designation_id: UUID | None = None,
        academy_course_id: str | None = None,
    ) -> dict[str, Any]:
        requirements = self._requirements(
            org_id,
            designation_id=designation_id,
            academy_course_id=academy_course_id,
        )
        employees = self._employees_by_designation(org_id, designation_id)
        progress_by_key = self._progress_by_requirement_employee(org_id)
        rows: list[dict[str, Any]] = []

        for requirement in requirements:
            for employee in employees.get(requirement.designation_id, []):
                progress = progress_by_key.get((requirement.id, employee.employee_id))
                if _is_complete(progress):
                    continue
                rows.append(
                    {
                        "employee": _person_name(employee),
                        "employee_code": employee.employee_code,
                        "designation": requirement.designation.designation_name,
                        "academy_course": requirement.academy_course_title,
                        "academy_course_id": requirement.academy_course_id,
                        "academy_assessment": requirement.academy_assessment_title
                        or requirement.academy_assessment_id
                        or "-",
                        "status": progress.status.value if progress else "not_started",
                        "progress_percentage": float(progress.progress_percentage)
                        if progress
                        else 0,
                        "last_synced_at": progress.last_synced_at.isoformat()
                        if progress and progress.last_synced_at
                        else None,
                    }
                )

        return {
            "summary": {
                "missing_count": len(rows),
                "requirements": len(requirements),
            },
            "rows": rows,
        }

    def certification_gaps(
        self,
        org_id: UUID,
        *,
        designation_id: UUID | None = None,
        academy_course_id: str | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        today = as_of or date.today()
        requirements = self._requirements(
            org_id,
            designation_id=designation_id,
            academy_course_id=academy_course_id,
        )
        employees = self._employees_by_designation(org_id, designation_id)
        progress_by_key = self._progress_by_requirement_employee(org_id)
        rows: list[dict[str, Any]] = []

        for requirement in requirements:
            for employee in employees.get(requirement.designation_id, []):
                progress = progress_by_key.get((requirement.id, employee.employee_id))
                if not _is_complete(progress):
                    rows.append(
                        self._certification_gap_row(
                            employee,
                            requirement,
                            progress,
                            "missing_completion",
                        )
                    )
                    continue
                certification = progress.certification if progress else None
                if certification is None:
                    rows.append(
                        self._certification_gap_row(
                            employee,
                            requirement,
                            progress,
                            "missing_certification",
                        )
                    )
                    continue
                if (
                    not certification.does_not_expire
                    and certification.expiry_date is not None
                    and certification.expiry_date < today
                ):
                    rows.append(
                        self._certification_gap_row(
                            employee,
                            requirement,
                            progress,
                            "expired_certification",
                            certification,
                        )
                    )

        return {
            "summary": {
                "gap_count": len(rows),
                "requirements": len(requirements),
            },
            "rows": rows,
        }

    def _certification_gap_row(
        self,
        employee: Employee,
        requirement: AcademyLearningRequirement,
        progress: AcademyLearningProgress | None,
        gap_type: str,
        certification: EmployeeCertification | None = None,
    ) -> dict[str, Any]:
        certification = certification or (progress.certification if progress else None)
        return {
            "employee": _person_name(employee),
            "employee_code": employee.employee_code,
            "designation": requirement.designation.designation_name,
            "academy_course": requirement.academy_course_title,
            "academy_assessment": requirement.academy_assessment_title
            or requirement.academy_assessment_id
            or "-",
            "gap_type": gap_type,
            "progress_status": progress.status.value if progress else "not_started",
            "certificate_ref": progress.certificate_ref if progress else None,
            "certification": certification.certification_name
            if certification
            else None,
            "expiry_date": certification.expiry_date.isoformat()
            if certification and certification.expiry_date
            else None,
        }

    def _requirements(
        self,
        org_id: UUID,
        *,
        designation_id: UUID | None = None,
        academy_course_id: str | None = None,
    ) -> list[AcademyLearningRequirement]:
        stmt = (
            select(AcademyLearningRequirement)
            .options(joinedload(AcademyLearningRequirement.designation))
            .where(
                AcademyLearningRequirement.organization_id == org_id,
                AcademyLearningRequirement.is_active.is_(True),
                AcademyLearningRequirement.is_required.is_(True),
            )
            .order_by(
                AcademyLearningRequirement.academy_course_title,
                AcademyLearningRequirement.created_at,
            )
        )
        if designation_id:
            stmt = stmt.where(
                AcademyLearningRequirement.designation_id == designation_id
            )
        if academy_course_id:
            stmt = stmt.where(
                AcademyLearningRequirement.academy_course_id == academy_course_id
            )
        return list(self.db.scalars(stmt).all())

    def _employees_by_designation(
        self,
        org_id: UUID,
        designation_id: UUID | None = None,
    ) -> dict[UUID, list[Employee]]:
        stmt = (
            select(Employee)
            .options(joinedload(Employee.person), joinedload(Employee.designation))
            .where(
                Employee.organization_id == org_id,
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.designation_id.is_not(None),
            )
            .order_by(Employee.employee_code)
        )
        if designation_id:
            stmt = stmt.where(Employee.designation_id == designation_id)
        employees: dict[UUID, list[Employee]] = {}
        for employee in self.db.scalars(stmt).all():
            if employee.designation_id:
                employees.setdefault(employee.designation_id, []).append(employee)
        return employees

    def _progress_by_requirement_employee(
        self,
        org_id: UUID,
    ) -> dict[tuple[UUID, UUID], AcademyLearningProgress]:
        stmt = (
            select(AcademyLearningProgress)
            .options(joinedload(AcademyLearningProgress.certification))
            .where(
                AcademyLearningProgress.organization_id == org_id,
                AcademyLearningProgress.requirement_id.is_not(None),
            )
            .order_by(AcademyLearningProgress.last_synced_at.desc())
        )
        rows: dict[tuple[UUID, UUID], AcademyLearningProgress] = {}
        for progress in self.db.scalars(stmt).all():
            if progress.requirement_id:
                rows.setdefault(
                    (progress.requirement_id, progress.employee_id),
                    progress,
                )
        return rows

    @staticmethod
    def _percentage(numerator: int, denominator: int) -> float:
        if denominator == 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)
