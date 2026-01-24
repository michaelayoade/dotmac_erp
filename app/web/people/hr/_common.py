"""Common imports and utilities for HR web routes."""

from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.person import Person
from app.models.finance.core_org.location import Location, LocationType
from app.models.people.hr import Employee
from app.services.people.hr.web import hr_web_service
from app.services.people.hr import (
    EmployeeService,
    OrganizationService,
    EmployeeCreateData,
    EmployeeUpdateData,
    TerminationData,
    DepartmentFilters,
    DepartmentCreateData,
    DepartmentUpdateData,
    DesignationCreateData,
    DesignationUpdateData,
    EmploymentTypeCreateData,
    EmploymentTypeUpdateData,
    EmployeeGradeCreateData,
    EmployeeGradeUpdateData,
    BulkUpdateData,
    # Extended data services
    EmployeeDocumentService,
    EmployeeQualificationService,
    EmployeeCertificationService,
    EmployeeDependentService,
    SkillService,
    EmployeeSkillService,
    # Job description services
    CompetencyService,
    JobDescriptionService,
)
from app.models.people.hr import (
    DocumentType,
    QualificationType,
    RelationshipType,
    SkillCategory,
    CompetencyCategory,
    JobDescriptionStatus,
)
from app.services.common import PaginationParams, ValidationError, coerce_uuid
from app.services.people.hr.web.employee_web import DEFAULT_PAGE_SIZE, DROPDOWN_LIMIT
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext


def _parse_bool(value: Optional[str], default: bool = False) -> bool:
    """Parse a string value to boolean."""
    if value is None:
        return default
    return str(value).lower() in {"1", "true", "on", "yes"}


def _parse_location_type(value: Optional[str]) -> Optional[LocationType]:
    """Parse a string value to LocationType enum."""
    if not value:
        return None
    try:
        return LocationType(value)
    except ValueError:
        return None
