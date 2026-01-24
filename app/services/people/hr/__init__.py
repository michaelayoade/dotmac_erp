"""HR Services - Employee, Organization, and related business logic.

This package provides services for HR Core operations:
- EmployeeService: Employee CRUD, status management, org chart
- OrganizationService: Departments, Designations, Employment Types, Grades

Usage:
    from app.services.people.hr import EmployeeService, OrganizationService

    # Create service with organization context
    employee_svc = EmployeeService(db, organization_id, principal)
    org_svc = OrganizationService(db, organization_id, principal)

    # List employees
    result = employee_svc.list_employees(filters, pagination)

    # Create department
    dept = org_svc.create_department(DepartmentCreateData(...))
"""
from .employees import EmployeeService
from .employee_types import (
    BulkResult,
    BulkUpdateData,
    EmployeeCreateData,
    EmployeeFilters,
    EmployeeSummary,
    EmployeeUpdateData,
    OrgChartNode,
    TerminationData,
)
from .errors import (
    CircularDepartmentError,
    DepartmentNotFoundError,
    DesignationNotFoundError,
    EmployeeAlreadyExistsError,
    EmployeeGradeNotFoundError,
    EmployeeNotFoundError,
    EmployeeStatusError,
    EmploymentTypeNotFoundError,
    InvalidManagerError,
    ValidationError,
)
from .organization import OrganizationService
from .lifecycle import LifecycleService
from .organization_types import (
    DepartmentCreateData,
    DepartmentFilters,
    DepartmentHeadcount,
    DepartmentNode,
    DepartmentUpdateData,
    DesignationCreateData,
    DesignationFilters,
    DesignationHeadcount,
    DesignationUpdateData,
    EmployeeGradeCreateData,
    EmployeeGradeFilters,
    EmployeeGradeUpdateData,
    EmploymentTypeCreateData,
    EmploymentTypeFilters,
    EmploymentTypeUpdateData,
)

__all__ = [
    # Services
    "EmployeeService",
    "OrganizationService",
    "LifecycleService",
    # Employee Types
    "EmployeeFilters",
    "EmployeeCreateData",
    "EmployeeUpdateData",
    "EmployeeSummary",
    "OrgChartNode",
    "TerminationData",
    "BulkUpdateData",
    "BulkResult",
    # Organization Types
    "DepartmentFilters",
    "DepartmentCreateData",
    "DepartmentUpdateData",
    "DepartmentNode",
    "DepartmentHeadcount",
    "DesignationFilters",
    "DesignationCreateData",
    "DesignationUpdateData",
    "DesignationHeadcount",
    "EmploymentTypeFilters",
    "EmploymentTypeCreateData",
    "EmploymentTypeUpdateData",
    "EmployeeGradeFilters",
    "EmployeeGradeCreateData",
    "EmployeeGradeUpdateData",
    # Errors
    "EmployeeNotFoundError",
    "EmployeeAlreadyExistsError",
    "EmployeeStatusError",
    "InvalidManagerError",
    "DepartmentNotFoundError",
    "DesignationNotFoundError",
    "EmploymentTypeNotFoundError",
    "EmployeeGradeNotFoundError",
    "CircularDepartmentError",
    "ValidationError",
]
