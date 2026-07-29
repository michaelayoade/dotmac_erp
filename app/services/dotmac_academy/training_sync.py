"""Record an academy course completion against an employee's HR record.

Idempotent: keyed on (organization, employee, credential_id) where credential_id
is the academy certificate reference, so a re-delivered webhook updates the same
EmployeeCertification instead of creating duplicates. A completion for an email
that is not an employee is a no-op (the academy pushes all completions; only
staff are recorded here).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.people.hr.employee import Employee
from app.models.people.hr.employee_extended import EmployeeCertification
from app.models.person import Person

logger = logging.getLogger(__name__)

ISSUING_AUTHORITY = "Dotmac Fiber Academy"


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
        db.flush()
        logger.info("Updated academy certification %s", existing.certification_id)
        return {"status": "updated", "certification_id": str(existing.certification_id)}

    cert = EmployeeCertification(
        organization_id=organization_id,
        employee_id=employee.employee_id,
        certification_name=course_title,
        issuing_authority=ISSUING_AUTHORITY,
        credential_id=certificate_ref,
        issue_date=issue_date,
        is_verified=True,
    )
    db.add(cert)
    db.flush()
    logger.info(
        "Recorded academy certification %s for employee %s",
        cert.certification_id,
        employee.employee_id,
    )
    return {"status": "recorded", "certification_id": str(cert.certification_id)}
