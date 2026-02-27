"""
HR Pydantic Schemas.

Pydantic schemas for HR Core APIs including:
- Department
- Designation
- Employment Type
- Employee Grade
- Employee
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.auth import AuthProvider
from app.models.people.hr import EmployeeStatus
from app.models.people.hr.employee import SalaryMode

# =============================================================================
# Department Schemas
# =============================================================================


class DepartmentBase(BaseModel):
    """Base department schema."""

    department_code: str = Field(max_length=20)
    department_name: str = Field(max_length=100)
    description: str | None = None
    parent_department_id: UUID | None = None
    cost_center_id: UUID | None = None
    is_active: bool = True


class DepartmentCreate(DepartmentBase):
    """Create department request."""

    pass


class DepartmentUpdate(BaseModel):
    """Update department request."""

    department_code: str | None = Field(default=None, max_length=20)
    department_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    parent_department_id: UUID | None = None
    cost_center_id: UUID | None = None
    is_active: bool | None = None


class DepartmentRead(DepartmentBase):
    """Department response."""

    model_config = ConfigDict(from_attributes=True)

    department_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None


class DepartmentListResponse(BaseModel):
    """Paginated department list response."""

    items: list[DepartmentRead]
    total: int
    offset: int
    limit: int


# =============================================================================
# Designation Schemas
# =============================================================================


class DesignationBase(BaseModel):
    """Base designation schema."""

    designation_code: str = Field(max_length=20)
    designation_name: str = Field(max_length=100)
    description: str | None = None
    is_active: bool = True


class DesignationCreate(DesignationBase):
    """Create designation request."""

    pass


class DesignationUpdate(BaseModel):
    """Update designation request."""

    designation_code: str | None = Field(default=None, max_length=20)
    designation_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    is_active: bool | None = None


class DesignationRead(DesignationBase):
    """Designation response."""

    model_config = ConfigDict(from_attributes=True)

    designation_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None


# =============================================================================
# Employment Type Schemas
# =============================================================================


class EmploymentTypeBase(BaseModel):
    """Base employment type schema."""

    type_code: str = Field(max_length=20)
    type_name: str = Field(max_length=100)
    description: str | None = None
    is_active: bool = True


class EmploymentTypeCreate(EmploymentTypeBase):
    """Create employment type request."""

    pass


class EmploymentTypeUpdate(BaseModel):
    """Update employment type request."""

    type_code: str | None = Field(default=None, max_length=20)
    type_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    is_active: bool | None = None


class EmploymentTypeRead(EmploymentTypeBase):
    """Employment type response."""

    model_config = ConfigDict(from_attributes=True)

    employment_type_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None


# =============================================================================
# Employee Grade Schemas
# =============================================================================


class EmployeeGradeBase(BaseModel):
    """Base employee grade schema."""

    grade_code: str = Field(max_length=20)
    grade_name: str = Field(max_length=100)
    description: str | None = None
    rank: int = 0
    min_salary: Decimal | None = None
    max_salary: Decimal | None = None
    is_active: bool = True


class EmployeeGradeCreate(EmployeeGradeBase):
    """Create employee grade request."""

    pass


class EmployeeGradeUpdate(BaseModel):
    """Update employee grade request."""

    grade_code: str | None = Field(default=None, max_length=20)
    grade_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    rank: int | None = None
    min_salary: Decimal | None = None
    max_salary: Decimal | None = None
    is_active: bool | None = None


class EmployeeGradeRead(EmployeeGradeBase):
    """Employee grade response."""

    model_config = ConfigDict(from_attributes=True)

    grade_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None


# =============================================================================
# Employee Schemas
# =============================================================================


class EmployeeBase(BaseModel):
    """Base employee schema."""

    employee_code: str | None = Field(default=None, max_length=30)
    department_id: UUID | None = None
    designation_id: UUID | None = None
    employment_type_id: UUID | None = None
    grade_id: UUID | None = None
    reports_to_id: UUID | None = None
    expense_approver_id: UUID | None = None
    assigned_location_id: UUID | None = None
    default_shift_type_id: UUID | None = None
    date_of_joining: date
    probation_end_date: date | None = None
    confirmation_date: date | None = None
    status: EmployeeStatus = EmployeeStatus.DRAFT
    cost_center_id: UUID | None = None
    ctc: Decimal | None = None
    salary_mode: SalaryMode | None = None
    bank_name: str | None = Field(default=None, max_length=100)
    bank_account_number: str | None = Field(default=None, max_length=30)
    bank_account_name: str | None = Field(default=None, max_length=100)
    bank_branch_code: str | None = Field(default=None, max_length=20)
    notes: str | None = None


class EmployeeCreate(EmployeeBase):
    """Create employee request.

    Requires person_id to link to an existing Person record.
    """

    person_id: UUID


class EmployeeUpdate(BaseModel):
    """Update employee request."""

    employee_code: str | None = Field(default=None, max_length=30)
    department_id: UUID | None = None
    designation_id: UUID | None = None
    employment_type_id: UUID | None = None
    grade_id: UUID | None = None
    reports_to_id: UUID | None = None
    expense_approver_id: UUID | None = None
    assigned_location_id: UUID | None = None
    default_shift_type_id: UUID | None = None
    date_of_joining: date | None = None
    date_of_leaving: date | None = None
    probation_end_date: date | None = None
    confirmation_date: date | None = None
    status: EmployeeStatus | None = None
    cost_center_id: UUID | None = None
    ctc: Decimal | None = None
    salary_mode: SalaryMode | None = None
    bank_name: str | None = Field(default=None, max_length=100)
    bank_account_number: str | None = Field(default=None, max_length=30)
    bank_account_name: str | None = Field(default=None, max_length=100)
    bank_branch_code: str | None = Field(default=None, max_length=20)
    notes: str | None = None


class PersonBrief(BaseModel):
    """Brief person info for employee responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: str | None
    last_name: str | None
    email: str | None


class DepartmentBrief(BaseModel):
    """Brief department info for employee responses."""

    model_config = ConfigDict(from_attributes=True)

    department_id: UUID
    department_code: str
    department_name: str


class DesignationBrief(BaseModel):
    """Brief designation info for employee responses."""

    model_config = ConfigDict(from_attributes=True)

    designation_id: UUID
    designation_code: str
    designation_name: str


class EmployeeRead(BaseModel):
    """Employee response with nested person and org info."""

    model_config = ConfigDict(from_attributes=True)

    employee_id: UUID
    organization_id: UUID
    person_id: UUID
    employee_code: str
    department_id: UUID | None = None
    designation_id: UUID | None = None
    employment_type_id: UUID | None = None
    grade_id: UUID | None = None
    reports_to_id: UUID | None = None
    expense_approver_id: UUID | None = None
    assigned_location_id: UUID | None = None
    default_shift_type_id: UUID | None = None
    date_of_joining: date
    date_of_leaving: date | None = None
    probation_end_date: date | None = None
    confirmation_date: date | None = None
    status: EmployeeStatus
    cost_center_id: UUID | None = None
    ctc: Decimal | None = None
    salary_mode: SalaryMode | None = None
    bank_name: str | None = None
    bank_account_number: str | None = None
    bank_account_name: str | None = None
    bank_branch_code: str | None = None
    notes: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime | None = None

    # Nested relationships (populated when eager loaded)
    person: PersonBrief | None = None
    department: DepartmentBrief | None = None
    designation: DesignationBrief | None = None


class EmployeeSummaryRead(BaseModel):
    """Summary employee info for lists/search."""

    employee_id: UUID
    employee_code: str
    person_name: str
    email: str | None = None
    department_name: str | None = None
    designation_name: str | None = None
    status: EmployeeStatus


class EmployeeListResponse(BaseModel):
    """Paginated employee list response."""

    items: list[EmployeeRead]
    total: int
    offset: int
    limit: int


class EmployeeStatsRead(BaseModel):
    """Employee statistics."""

    total: int
    active: int
    on_leave: int
    inactive: int


class EmployeeUserCredentialCreate(BaseModel):
    """Create user credentials for an employee."""

    provider: AuthProvider = AuthProvider.local
    username: str | None = Field(default=None, max_length=150)
    password: str | None = Field(default=None, max_length=255)
    must_change_password: bool = True


class EmployeeUserLink(BaseModel):
    """Link an employee to an existing user (Person)."""

    person_id: UUID


# =============================================================================
# Office Location Schemas
# =============================================================================


class LocationBase(BaseModel):
    """Base location schema."""

    location_code: str = Field(max_length=20)
    location_name: str = Field(max_length=100)
    location_type: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state_province: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    geofence_radius_m: int = 500
    geofence_enabled: bool = True
    geofence_polygon: dict | None = (
        None  # GeoJSON Polygon/MultiPolygon (if set, overrides circle)
    )
    is_active: bool = True


class LocationCreate(LocationBase):
    """Create location request."""

    pass


class LocationUpdate(BaseModel):
    """Update location request."""

    location_code: str | None = Field(default=None, max_length=20)
    location_name: str | None = Field(default=None, max_length=100)
    location_type: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state_province: str | None = None
    postal_code: str | None = None
    country_code: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    geofence_radius_m: int | None = None
    geofence_enabled: bool | None = None
    geofence_polygon: dict | None = (
        None  # GeoJSON Polygon/MultiPolygon (if set, overrides circle)
    )
    is_active: bool | None = None


class LocationRead(LocationBase):
    """Location response."""

    model_config = ConfigDict(from_attributes=True)

    location_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime | None = None


class LocationListResponse(BaseModel):
    """Paginated location list response."""

    items: list[LocationRead]
    total: int
    offset: int
    limit: int


class TerminationRequest(BaseModel):
    """Employee termination request."""

    date_of_leaving: date
    reason: str | None = None
    exit_interview_notes: str | None = None


class ResignationRequest(BaseModel):
    """Employee resignation request."""

    date_of_leaving: date


class RehireRequest(BaseModel):
    """Employee rehire request."""

    date_of_rejoining: date
    notes: str | None = None


class BulkUpdateRequest(BaseModel):
    """Bulk employee update request."""

    ids: list[UUID]
    department_id: UUID | None = None
    designation_id: UUID | None = None
    status: EmployeeStatus | None = None
    reports_to_id: UUID | None = None


class BulkDeleteRequest(BaseModel):
    """Bulk employee delete request."""

    ids: list[UUID]


class BulkOperationResponse(BaseModel):
    """Bulk operation response."""

    updated_count: int = 0
    deleted_count: int = 0
    failed_ids: list[UUID] = []
    errors: list[str] = []
