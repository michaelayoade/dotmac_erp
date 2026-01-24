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
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.auth import AuthProvider
from app.models.people.hr import EmployeeStatus


# =============================================================================
# Department Schemas
# =============================================================================


class DepartmentBase(BaseModel):
    """Base department schema."""

    department_code: str = Field(max_length=20)
    department_name: str = Field(max_length=100)
    description: Optional[str] = None
    parent_department_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    is_active: bool = True


class DepartmentCreate(DepartmentBase):
    """Create department request."""

    pass


class DepartmentUpdate(BaseModel):
    """Update department request."""

    department_code: Optional[str] = Field(default=None, max_length=20)
    department_name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    parent_department_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    is_active: Optional[bool] = None


class DepartmentRead(DepartmentBase):
    """Department response."""

    model_config = ConfigDict(from_attributes=True)

    department_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


class DepartmentListResponse(BaseModel):
    """Paginated department list response."""

    items: List[DepartmentRead]
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
    description: Optional[str] = None
    is_active: bool = True


class DesignationCreate(DesignationBase):
    """Create designation request."""

    pass


class DesignationUpdate(BaseModel):
    """Update designation request."""

    designation_code: Optional[str] = Field(default=None, max_length=20)
    designation_name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DesignationRead(DesignationBase):
    """Designation response."""

    model_config = ConfigDict(from_attributes=True)

    designation_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


# =============================================================================
# Employment Type Schemas
# =============================================================================


class EmploymentTypeBase(BaseModel):
    """Base employment type schema."""

    type_code: str = Field(max_length=20)
    type_name: str = Field(max_length=100)
    description: Optional[str] = None
    is_active: bool = True


class EmploymentTypeCreate(EmploymentTypeBase):
    """Create employment type request."""

    pass


class EmploymentTypeUpdate(BaseModel):
    """Update employment type request."""

    type_code: Optional[str] = Field(default=None, max_length=20)
    type_name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class EmploymentTypeRead(EmploymentTypeBase):
    """Employment type response."""

    model_config = ConfigDict(from_attributes=True)

    employment_type_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


# =============================================================================
# Employee Grade Schemas
# =============================================================================


class EmployeeGradeBase(BaseModel):
    """Base employee grade schema."""

    grade_code: str = Field(max_length=20)
    grade_name: str = Field(max_length=100)
    description: Optional[str] = None
    rank: int = 0
    min_salary: Optional[Decimal] = None
    max_salary: Optional[Decimal] = None
    is_active: bool = True


class EmployeeGradeCreate(EmployeeGradeBase):
    """Create employee grade request."""

    pass


class EmployeeGradeUpdate(BaseModel):
    """Update employee grade request."""

    grade_code: Optional[str] = Field(default=None, max_length=20)
    grade_name: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = None
    rank: Optional[int] = None
    min_salary: Optional[Decimal] = None
    max_salary: Optional[Decimal] = None
    is_active: Optional[bool] = None


class EmployeeGradeRead(EmployeeGradeBase):
    """Employee grade response."""

    model_config = ConfigDict(from_attributes=True)

    grade_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


# =============================================================================
# Employee Schemas
# =============================================================================


class EmployeeBase(BaseModel):
    """Base employee schema."""

    employee_code: Optional[str] = Field(default=None, max_length=30)
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    employment_type_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    reports_to_id: Optional[UUID] = None
    assigned_location_id: Optional[UUID] = None
    default_shift_type_id: Optional[UUID] = None
    date_of_joining: date
    probation_end_date: Optional[date] = None
    confirmation_date: Optional[date] = None
    status: EmployeeStatus = EmployeeStatus.DRAFT
    cost_center_id: Optional[UUID] = None
    bank_name: Optional[str] = Field(default=None, max_length=100)
    bank_account_number: Optional[str] = Field(default=None, max_length=30)
    bank_account_name: Optional[str] = Field(default=None, max_length=100)
    bank_branch_code: Optional[str] = Field(default=None, max_length=20)
    notes: Optional[str] = None


class EmployeeCreate(EmployeeBase):
    """Create employee request.

    Requires person_id to link to an existing Person record.
    """

    person_id: UUID


class EmployeeUpdate(BaseModel):
    """Update employee request."""

    employee_code: Optional[str] = Field(default=None, max_length=30)
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    employment_type_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    reports_to_id: Optional[UUID] = None
    assigned_location_id: Optional[UUID] = None
    default_shift_type_id: Optional[UUID] = None
    date_of_joining: Optional[date] = None
    date_of_leaving: Optional[date] = None
    probation_end_date: Optional[date] = None
    confirmation_date: Optional[date] = None
    status: Optional[EmployeeStatus] = None
    cost_center_id: Optional[UUID] = None
    bank_name: Optional[str] = Field(default=None, max_length=100)
    bank_account_number: Optional[str] = Field(default=None, max_length=30)
    bank_account_name: Optional[str] = Field(default=None, max_length=100)
    bank_branch_code: Optional[str] = Field(default=None, max_length=20)
    notes: Optional[str] = None


class PersonBrief(BaseModel):
    """Brief person info for employee responses."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    first_name: Optional[str]
    last_name: Optional[str]
    email: Optional[str]


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
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    employment_type_id: Optional[UUID] = None
    grade_id: Optional[UUID] = None
    reports_to_id: Optional[UUID] = None
    assigned_location_id: Optional[UUID] = None
    default_shift_type_id: Optional[UUID] = None
    date_of_joining: date
    date_of_leaving: Optional[date] = None
    probation_end_date: Optional[date] = None
    confirmation_date: Optional[date] = None
    status: EmployeeStatus
    cost_center_id: Optional[UUID] = None
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_branch_code: Optional[str] = None
    notes: Optional[str] = None
    version: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    # Nested relationships (populated when eager loaded)
    person: Optional[PersonBrief] = None
    department: Optional[DepartmentBrief] = None
    designation: Optional[DesignationBrief] = None


class EmployeeSummaryRead(BaseModel):
    """Summary employee info for lists/search."""

    employee_id: UUID
    employee_code: str
    person_name: str
    email: Optional[str] = None
    department_name: Optional[str] = None
    designation_name: Optional[str] = None
    status: EmployeeStatus


class EmployeeListResponse(BaseModel):
    """Paginated employee list response."""

    items: List[EmployeeRead]
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
    username: Optional[str] = Field(default=None, max_length=150)
    password: Optional[str] = Field(default=None, max_length=255)
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
    location_type: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state_province: Optional[str] = None
    postal_code: Optional[str] = None
    country_code: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    geofence_radius_m: int = 500
    geofence_enabled: bool = True
    is_active: bool = True


class LocationCreate(LocationBase):
    """Create location request."""

    pass


class LocationUpdate(BaseModel):
    """Update location request."""

    location_code: Optional[str] = Field(default=None, max_length=20)
    location_name: Optional[str] = Field(default=None, max_length=100)
    location_type: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    state_province: Optional[str] = None
    postal_code: Optional[str] = None
    country_code: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    geofence_radius_m: Optional[int] = None
    geofence_enabled: Optional[bool] = None
    is_active: Optional[bool] = None


class LocationRead(LocationBase):
    """Location response."""

    model_config = ConfigDict(from_attributes=True)

    location_id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: Optional[datetime] = None


class LocationListResponse(BaseModel):
    """Paginated location list response."""

    items: List[LocationRead]
    total: int
    offset: int
    limit: int


class TerminationRequest(BaseModel):
    """Employee termination request."""

    date_of_leaving: date
    reason: Optional[str] = None
    exit_interview_notes: Optional[str] = None


class ResignationRequest(BaseModel):
    """Employee resignation request."""

    date_of_leaving: date


class BulkUpdateRequest(BaseModel):
    """Bulk employee update request."""

    ids: List[UUID]
    department_id: Optional[UUID] = None
    designation_id: Optional[UUID] = None
    status: Optional[EmployeeStatus] = None
    reports_to_id: Optional[UUID] = None


class BulkDeleteRequest(BaseModel):
    """Bulk employee delete request."""

    ids: List[UUID]


class BulkOperationResponse(BaseModel):
    """Bulk operation response."""

    updated_count: int = 0
    deleted_count: int = 0
    failed_ids: List[UUID] = []
    errors: List[str] = []
