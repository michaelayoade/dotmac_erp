"""Versioned, read-only ERP People projection consumed during replacement."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PeopleProjectionEntity(str, Enum):
    PARTY_PERSON = "party_person"
    DEPARTMENT = "department"
    DESIGNATION = "designation"
    EMPLOYMENT_TYPE = "employment_type"
    EMPLOYEE = "employee"
    POSITION = "position"
    POSITION_ASSIGNMENT = "position_assignment"


class _ProjectionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: UUID
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_updated_at: datetime | None = None


class PartyPersonProjection(_ProjectionRecord):
    entity: Literal["party_person"] = "party_person"
    display_name: str
    email: str | None = None
    is_active: bool
    first_name: str
    last_name: str


class DepartmentProjection(_ProjectionRecord):
    entity: Literal["department"] = "department"
    code: str
    name: str
    description: str | None = None
    parent_id: UUID | None = None
    is_active: bool


class DesignationProjection(_ProjectionRecord):
    entity: Literal["designation"] = "designation"
    code: str
    name: str
    description: str | None = None
    is_active: bool


class EmploymentTypeProjection(_ProjectionRecord):
    entity: Literal["employment_type"] = "employment_type"
    code: str
    name: str
    description: str | None = None
    is_active: bool


class EmployeeProjection(_ProjectionRecord):
    entity: Literal["employee"] = "employee"
    party_id: UUID
    employee_code: str
    department_id: UUID | None = None
    designation_id: UUID | None = None
    employment_type_id: UUID | None = None
    date_of_joining: date
    date_of_leaving: date | None = None
    probation_end_date: date | None = None
    confirmation_date: date | None = None
    status: str


class PositionProjection(_ProjectionRecord):
    entity: Literal["position"] = "position"
    code: str
    name: str
    department_id: UUID | None = None
    designation_id: UUID | None = None
    parent_id: UUID | None = None
    is_department_head: bool
    vacancy_routing_policy: str
    is_active: bool


class PositionAssignmentProjection(_ProjectionRecord):
    entity: Literal["position_assignment"] = "position_assignment"
    employee_id: UUID
    position_id: UUID
    assignment_type: str
    start_date: date
    end_date: date | None = None


PeopleProjectionRecord = Annotated[
    PartyPersonProjection
    | DepartmentProjection
    | DesignationProjection
    | EmploymentTypeProjection
    | EmployeeProjection
    | PositionProjection
    | PositionAssignmentProjection,
    Field(discriminator="entity"),
]


class PeopleProjectionPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["backoffice.people.projection.v1"] = (
        "backoffice.people.projection.v1"
    )
    entity: PeopleProjectionEntity
    items: list[PeopleProjectionRecord]
    next_after: UUID | None = None


__all__ = [
    "DepartmentProjection",
    "DesignationProjection",
    "EmployeeProjection",
    "EmploymentTypeProjection",
    "PartyPersonProjection",
    "PeopleProjectionEntity",
    "PeopleProjectionPage",
    "PeopleProjectionRecord",
    "PositionAssignmentProjection",
    "PositionProjection",
]
