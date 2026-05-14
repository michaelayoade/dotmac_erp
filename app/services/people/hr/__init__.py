"""HR Services - Employee, Organization, and related business logic.

Keep package `__init__` import-light: many modules import selected services/types
from this package, and eager imports can pull in a large graph during test
collection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .employee_extended import (  # noqa: F401
        CertificationNotFoundError,
        DependentNotFoundError,
        DocumentNotFoundError,
        EmployeeCertificationService,
        EmployeeDependentService,
        EmployeeDocumentService,
        EmployeeQualificationService,
        EmployeeSkillNotFoundError,
        EmployeeSkillService,
        QualificationNotFoundError,
        SkillNotFoundError,
        SkillService,
    )
    from .employee_types import (  # noqa: F401
        BulkResult,
        BulkUpdateData,
        EmployeeCreateData,
        EmployeeFilters,
        EmployeeSummary,
        EmployeeUpdateData,
        TerminationData,
    )
    from .employees import EmployeeService  # noqa: F401
    from .errors import (  # noqa: F401
        ActivityNotFoundError,
        ChecklistTemplateNotFoundError,
        CircularDepartmentError,
        DepartmentNotFoundError,
        DesignationNotFoundError,
        EmployeeAlreadyExistsError,
        EmployeeGradeNotFoundError,
        EmployeeNotFoundError,
        EmployeeStatusError,
        EmploymentTypeNotFoundError,
        InvalidManagerError,
        InvalidSelfServiceTokenError,
        LocationNotFoundError,
        ValidationError,
    )
    from .job_description import (  # noqa: F401
        CompetencyNotFoundError,
        CompetencyService,
        JobDescriptionNotFoundError,
        JobDescriptionService,
    )
    from .lifecycle import LifecycleService  # noqa: F401
    from .onboarding import OnboardingService  # noqa: F401
    from .org_resolver import OrgResolver  # noqa: F401
    from .organization import OrganizationService  # noqa: F401
    from .positions import (  # noqa: F401
        PositionAssignmentCreateData,
        PositionCreateData,
        PositionRoleSummary,
        PositionService,
        PositionSummary,
        PositionUpdateData,
    )
    from .organization_types import (  # noqa: F401
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
    "OnboardingService",
    "OrgResolver",
    "PositionService",
    "PositionAssignmentCreateData",
    "PositionCreateData",
    "PositionUpdateData",
    "PositionSummary",
    "EmployeeDocumentService",
    "EmployeeQualificationService",
    "EmployeeCertificationService",
    "EmployeeDependentService",
    "SkillService",
    "EmployeeSkillService",
    "CompetencyService",
    "JobDescriptionService",
    # Employee Types
    "EmployeeFilters",
    "EmployeeCreateData",
    "EmployeeUpdateData",
    "EmployeeSummary",
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
    "LocationNotFoundError",
    "DesignationNotFoundError",
    "EmploymentTypeNotFoundError",
    "EmployeeGradeNotFoundError",
    "CircularDepartmentError",
    "ValidationError",
    # Extended Data Errors
    "DocumentNotFoundError",
    "QualificationNotFoundError",
    "CertificationNotFoundError",
    "DependentNotFoundError",
    "SkillNotFoundError",
    "EmployeeSkillNotFoundError",
    # Job Description Errors
    "CompetencyNotFoundError",
    "JobDescriptionNotFoundError",
    # Onboarding Errors
    "ActivityNotFoundError",
    "ChecklistTemplateNotFoundError",
    "InvalidSelfServiceTokenError",
]


_NAME_TO_MODULE = {
    # employees
    "EmployeeService": "employees",
    # organization
    "OrganizationService": "organization",
    # lifecycle / onboarding
    "LifecycleService": "lifecycle",
    "OnboardingService": "onboarding",
    "OrgResolver": "org_resolver",
    "PositionService": "positions",
    "PositionAssignmentCreateData": "positions",
    "PositionCreateData": "positions",
    "PositionUpdateData": "positions",
    "PositionRoleSummary": "positions",
    "PositionSummary": "positions",
    # employee_extended
    "EmployeeDocumentService": "employee_extended",
    "EmployeeQualificationService": "employee_extended",
    "EmployeeCertificationService": "employee_extended",
    "EmployeeDependentService": "employee_extended",
    "SkillService": "employee_extended",
    "EmployeeSkillService": "employee_extended",
    "DocumentNotFoundError": "employee_extended",
    "QualificationNotFoundError": "employee_extended",
    "CertificationNotFoundError": "employee_extended",
    "DependentNotFoundError": "employee_extended",
    "SkillNotFoundError": "employee_extended",
    "EmployeeSkillNotFoundError": "employee_extended",
    # employee_types
    "EmployeeFilters": "employee_types",
    "EmployeeCreateData": "employee_types",
    "EmployeeUpdateData": "employee_types",
    "EmployeeSummary": "employee_types",
    "TerminationData": "employee_types",
    "BulkUpdateData": "employee_types",
    "BulkResult": "employee_types",
    # organization_types
    "DepartmentFilters": "organization_types",
    "DepartmentCreateData": "organization_types",
    "DepartmentUpdateData": "organization_types",
    "DepartmentNode": "organization_types",
    "DepartmentHeadcount": "organization_types",
    "DesignationFilters": "organization_types",
    "DesignationCreateData": "organization_types",
    "DesignationUpdateData": "organization_types",
    "DesignationHeadcount": "organization_types",
    "EmploymentTypeFilters": "organization_types",
    "EmploymentTypeCreateData": "organization_types",
    "EmploymentTypeUpdateData": "organization_types",
    "EmployeeGradeFilters": "organization_types",
    "EmployeeGradeCreateData": "organization_types",
    "EmployeeGradeUpdateData": "organization_types",
    # errors
    "EmployeeNotFoundError": "errors",
    "EmployeeAlreadyExistsError": "errors",
    "EmployeeStatusError": "errors",
    "InvalidManagerError": "errors",
    "DepartmentNotFoundError": "errors",
    "LocationNotFoundError": "errors",
    "DesignationNotFoundError": "errors",
    "EmploymentTypeNotFoundError": "errors",
    "EmployeeGradeNotFoundError": "errors",
    "CircularDepartmentError": "errors",
    "ValidationError": "errors",
    "ActivityNotFoundError": "errors",
    "ChecklistTemplateNotFoundError": "errors",
    "InvalidSelfServiceTokenError": "errors",
    # job_description
    "CompetencyService": "job_description",
    "JobDescriptionService": "job_description",
    "CompetencyNotFoundError": "job_description",
    "JobDescriptionNotFoundError": "job_description",
}


def __getattr__(name: str) -> Any:  # pragma: no cover
    module_name = _NAME_TO_MODULE.get(name)
    if not module_name:
        raise AttributeError(name)
    module = __import__(f"{__name__}.{module_name}", fromlist=[name])
    return getattr(module, name)
