"""
HR Core Models.

This module contains all models for the HR Core functionality:
- Department: Organizational units
- Designation: Job titles/positions
- EmploymentType: Types of employment (full-time, part-time, etc.)
- EmployeeGrade: Salary grades/bands
- Employee: Central employee entity linking Person to HR
"""

from app.models.people.hr.department import Department
from app.models.people.hr.designation import Designation
from app.models.people.hr.employee import Employee, EmployeeStatus, Gender
from app.models.people.hr.employee_grade import EmployeeGrade
from app.models.people.hr.employment_type import EmploymentType
from app.models.people.hr.lifecycle import (
    BoardingStatus,
    SeparationType,
    EmployeeOnboarding,
    EmployeeOnboardingActivity,
    EmployeeSeparation,
    EmployeeSeparationActivity,
    EmployeePromotion,
    EmployeePromotionDetail,
    EmployeeTransfer,
    EmployeeTransferDetail,
)
from app.models.people.hr.checklist_template import (
    ChecklistTemplate,
    ChecklistTemplateItem,
    ChecklistTemplateType,
)

__all__ = [
    "Department",
    "Designation",
    "Employee",
    "EmployeeGrade",
    "EmployeeStatus",
    "EmploymentType",
    "Gender",
    "BoardingStatus",
    "SeparationType",
    "EmployeeOnboarding",
    "EmployeeOnboardingActivity",
    "EmployeeSeparation",
    "EmployeeSeparationActivity",
    "EmployeePromotion",
    "EmployeePromotionDetail",
    "EmployeeTransfer",
    "EmployeeTransferDetail",
    "ChecklistTemplate",
    "ChecklistTemplateItem",
    "ChecklistTemplateType",
]
