"""NCC year-end staff head-count (Section G).

Aggregates active employees into NCC's head-count matrix: staff category
(Managerial / Senior-Technical / Junior-Technical / Other) x Nigerian vs
Expatriate x Male / Female. Category comes from the employee's designation
(`ncc_staff_category`, defaulting to OTHER); nationality from
`Employee.nationality` (Nigerian vs anything else; blank = unknown).
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr.employee import Employee, EmployeeStatus, Gender

logger = logging.getLogger(__name__)

_CATEGORIES = ("MANAGERIAL", "SENIOR_TECHNICAL", "JUNIOR_TECHNICAL", "OTHER")
_NATIONALITIES = ("nigerian", "expatriate", "unknown")
_GENDERS = ("male", "female", "other")
_NIGERIAN = {"nigerian", "nigeria", "ng"}


def _nationality_bucket(nationality: str | None) -> str:
    if not nationality or not nationality.strip():
        return "unknown"
    return "nigerian" if nationality.strip().lower() in _NIGERIAN else "expatriate"


def _gender_bucket(gender: Gender | None) -> str:
    if gender == Gender.MALE:
        return "male"
    if gender == Gender.FEMALE:
        return "female"
    return "other"


class NccStaffReportService:
    """Builds the NCC Section G staff head-count matrix for an organization."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def build(self, organization_id: UUID) -> dict:
        stmt = (
            select(Employee)
            .options(joinedload(Employee.designation))
            .where(
                Employee.organization_id == organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
        )
        employees = self.db.scalars(stmt).all()

        matrix: dict[str, dict[str, dict[str, int]]] = {
            cat: {nat: {g: 0 for g in _GENDERS} for nat in _NATIONALITIES} for cat in _CATEGORIES
        }
        total = 0
        for emp in employees:
            total += 1
            category = "OTHER"
            designation = emp.designation
            if designation is not None and designation.ncc_staff_category is not None:
                value = designation.ncc_staff_category.value
                if value in _CATEGORIES:
                    category = value
            nationality = _nationality_bucket(emp.nationality)
            gender = _gender_bucket(emp.gender)
            matrix[category][nationality][gender] += 1

        return {"total_active": total, "by_category": matrix}
