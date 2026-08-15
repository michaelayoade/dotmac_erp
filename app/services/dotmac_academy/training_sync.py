"""Record an academy course completion against an employee's HR record.

Idempotent: keyed on (organization, employee, credential_id) where credential_id
is the academy certificate reference, so a re-delivered webhook updates the same
EmployeeCertification instead of creating duplicates. A completion for an email
that is not an employee is a no-op (the academy pushes all completions; only
staff are recorded here).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.people.hr.employee import Employee
from app.models.people.hr.employee_extended import EmployeeCertification
from app.models.people.training.learning_assessment import (
    TrainingCourse,
    TrainingCourseAssignment,
    TrainingCourseProgress,
    TrainingProgressStatus,
)
from app.models.person import Person
from app.services.people.training import AcademyProgressService

logger = logging.getLogger(__name__)


def _issuing_authority() -> str:
    """Credential branding, read at call time so a rename is a config change.

    Was hardcoded to "Dotmac Fiber Academy" and stamped onto every certification
    — stale since the academy rebranded to Dotmac Academy.
    """
    from app.config import settings

    return settings.dotmac_academy_issuing_authority


def _parse_date(value: Any) -> date:
    """Parse an ISO date/datetime string; fall back to today on absence/garbage."""
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    return date.today()


def record_course_completion(
    db: Session, *, organization_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Record a `course_completed` event as an EmployeeCertification.

    Returns a status dict: recorded / updated / ignored (with a reason).
    """
    email = (payload.get("email") or "").strip().lower()
    if not email:
        return {"status": "ignored", "reason": "missing email"}
    if not payload.get("passed"):
        progress_result = record_course_progress(
            db,
            organization_id=organization_id,
            payload=payload,
        )
        if progress_result.get("status") in {"recorded", "updated"}:
            return progress_result
        return {"status": "ignored", "reason": "not passed"}

    course_title = (payload.get("course_title") or "Fiber Academy course").strip()
    certificate_ref = payload.get("certificate_ref") or None
    issue_date = _parse_date(payload.get("completed_on"))

    employee = (
        db.execute(
            select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .where(Employee.organization_id == organization_id)
            .where(func.lower(Person.email) == email)
        )
        .scalars()
        .first()
    )
    if employee is None:
        return {"status": "ignored", "reason": "no matching employee"}

    progress = None
    try:
        progress = AcademyProgressService(db).upsert_from_payload(
            organization_id,
            employee=employee,
            payload={
                **payload,
                "status": payload.get("status") or "completed",
                "progress_percentage": payload.get("progress_percentage") or "100",
            },
        )
    except Exception:
        logger.exception("Failed to record academy progress for %s", email)

    existing: EmployeeCertification | None = None
    if certificate_ref:
        existing = (
            db.execute(
                select(EmployeeCertification)
                .where(EmployeeCertification.organization_id == organization_id)
                .where(EmployeeCertification.employee_id == employee.employee_id)
                .where(EmployeeCertification.credential_id == certificate_ref)
            )
            .scalars()
            .first()
        )

    if existing is not None:
        existing.certification_name = course_title
        existing.issue_date = issue_date
        existing.is_verified = True
        if progress is not None:
            progress.certification_id = existing.certification_id
        db.flush()
        logger.info("Updated academy certification %s", existing.certification_id)
        return {"status": "updated", "certification_id": str(existing.certification_id)}

    cert = EmployeeCertification(
        organization_id=organization_id,
        employee_id=employee.employee_id,
        certification_name=course_title,
        issuing_authority=_issuing_authority(),
        credential_id=certificate_ref,
        issue_date=issue_date,
        is_verified=True,
    )
    db.add(cert)
    db.flush()
    if progress is not None:
        progress.certification_id = cert.certification_id
        db.flush()
    logger.info(
        "Recorded academy certification %s for employee %s",
        cert.certification_id,
        employee.employee_id,
    )
    return {"status": "recorded", "certification_id": str(cert.certification_id)}


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"missing {key}")
    return value


def record_training_projection(
    db: Session, *, organization_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Project one v2 Academy event onto assignment and progress records."""
    try:
        employee_ref = _required_text(payload, "employee_ref")
        course_ref = _required_text(payload, "academy_course_ref")
        enrollment_id = UUID(_required_text(payload, "academy_enrollment_ref"))
        progress_pct = Decimal(str(payload.get("progress_pct", 0)))
        occurred_at = datetime.fromisoformat(
            _required_text(payload, "occurred_at").replace("Z", "+00:00")
        )
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    except (ValueError, InvalidOperation) as exc:
        return {"status": "ignored", "reason": str(exc)}
    if progress_pct < 0 or progress_pct > 100:
        return {"status": "ignored", "reason": "progress_pct must be between 0 and 100"}

    employee = db.scalar(
        select(Employee).where(
            Employee.organization_id == organization_id,
            Employee.employee_code == employee_ref,
        )
    )
    if employee is None:
        return {"status": "ignored", "reason": "no matching employee_ref"}
    course = db.scalar(
        select(TrainingCourse).where(
            TrainingCourse.organization_id == organization_id,
            TrainingCourse.academy_course_ref == course_ref,
        )
    )
    if course is None:
        return {"status": "ignored", "reason": "no course mapped to academy_course_ref"}

    assignment = db.scalar(
        select(TrainingCourseAssignment).where(
            TrainingCourseAssignment.organization_id == organization_id,
            TrainingCourseAssignment.course_id == course.id,
            TrainingCourseAssignment.employee_id == employee.employee_id,
        )
    )
    if assignment is None:
        assignment = TrainingCourseAssignment(
            organization_id=organization_id,
            course_id=course.id,
            employee_id=employee.employee_id,
            assignment_source="academy",
            assignment_source_id=enrollment_id,
            is_mandatory=course.is_mandatory,
            course_version_number=course.version_number,
        )
        db.add(assignment)

    event_type = str(payload.get("event") or "")
    status = (
        TrainingProgressStatus.COMPLETED
        if event_type == "course_completed" or progress_pct == 100
        else TrainingProgressStatus.IN_PROGRESS
        if progress_pct > 0
        else TrainingProgressStatus.NOT_STARTED
    )
    progress = db.scalar(
        select(TrainingCourseProgress).where(
            TrainingCourseProgress.organization_id == organization_id,
            TrainingCourseProgress.course_id == course.id,
            TrainingCourseProgress.employee_id == employee.employee_id,
        )
    )
    if progress is None:
        progress = TrainingCourseProgress(
            organization_id=organization_id,
            course_id=course.id,
            employee_id=employee.employee_id,
            course_version_number=course.version_number,
        )
        db.add(progress)
    elif progress.academy_updated_at:
        projected_at = progress.academy_updated_at
        if projected_at.tzinfo is None:
            projected_at = projected_at.replace(tzinfo=timezone.utc)
        if occurred_at <= projected_at:
            return {"status": "duplicate", "assignment_id": str(assignment.id)}
    progress.completion_percentage = progress_pct
    progress.status = status
    progress.academy_updated_at = occurred_at

    certification_id: str | None = None
    if event_type == "course_completed":
        certificate_ref = str(payload.get("certificate_ref") or "").strip()
        if not certificate_ref:
            return {"status": "ignored", "reason": "missing certificate_ref"}
        cert = db.scalar(
            select(EmployeeCertification).where(
                EmployeeCertification.organization_id == organization_id,
                EmployeeCertification.employee_id == employee.employee_id,
                EmployeeCertification.credential_id == certificate_ref,
            )
        )
        if cert is None:
            cert = EmployeeCertification(
                organization_id=organization_id,
                employee_id=employee.employee_id,
                certification_name=str(payload.get("course_title") or course.title),
                issuing_authority=_issuing_authority(),
                credential_id=certificate_ref,
                issue_date=_parse_date(payload.get("completed_on")),
                is_verified=True,
            )
            db.add(cert)
        else:
            cert.certification_name = str(payload.get("course_title") or course.title)
            cert.issue_date = _parse_date(payload.get("completed_on"))
            cert.is_verified = True
        db.flush()
        certification_id = str(cert.certification_id)

    db.flush()
    return {
        "status": "recorded",
        "assignment_id": str(assignment.id),
        "certification_id": certification_id,
    }


def record_course_progress(
    db: Session, *, organization_id: UUID, payload: dict[str, Any]
) -> dict[str, Any]:
    """Record an Academy progress event without requiring final certification."""
    email = (payload.get("email") or "").strip().lower()
    if not email:
        return {"status": "ignored", "reason": "missing email"}
    employee = (
        db.execute(
            select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .where(Employee.organization_id == organization_id)
            .where(func.lower(Person.email) == email)
        )
        .scalars()
        .first()
    )
    if employee is None:
        return {"status": "ignored", "reason": "no matching employee"}

    progress = AcademyProgressService(db).upsert_from_payload(
        organization_id,
        employee=employee,
        payload=payload,
    )
    return {"status": "updated", "academy_progress_id": str(progress.id)}
