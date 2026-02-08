"""
People (HR/HRIS) Models.

This module contains all models for the People/HR functionality including:
- HR Core: Employees, Departments, Designations, Employee Grades
- Payroll: Salary Components, Structures, Slips, Payroll Runs
- Leave: Leave Types, Allocations, Applications
- Attendance: Shifts, Attendance Records
- Recruitment: Jobs, Applicants, Interviews, Offers
- Training: Programs, Events, Results
- Performance: KPIs, Appraisals, Scorecards
- Expenses: Claims, Advances, Corporate Cards

All People models:
- Use UUID primary keys for consistency with Finance models
- Include organization_id for multi-tenancy
- Support RLS (Row Level Security) via organization_id
- Include audit fields (created_at, updated_at, created_by, updated_by)
- Support soft deletion where appropriate
"""

# People Assets
from app.models.people.assets import (
    AssetAssignment,
    AssetCondition,
    AssignmentStatus,
)

# Attendance Models
from app.models.people.attendance import (
    Attendance,
    AttendanceStatus,
    ShiftType,
)
from app.models.people.base import (
    AuditMixin,
    ERPNextSyncMixin,
    PeopleBaseMixin,
    SoftDeleteMixin,
    StatusTrackingMixin,
    VersionMixin,
)

# Discipline Models
from app.models.people.discipline import (
    ActionType,
    CaseAction,
    CaseDocument,
    CaseResponse,
    CaseStatus,
    CaseWitness,
    DisciplinaryCase,
    SeverityLevel,
    ViolationType,
)
from app.models.people.discipline import (
    DocumentType as DisciplineDocumentType,
)

# Expense Models
from app.models.people.exp import (
    CardTransaction,
    CardTransactionStatus,
    CashAdvance,
    CashAdvanceStatus,
    CorporateCard,
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)

# HR Core Models
from app.models.people.hr import (
    Department,
    Designation,
    Employee,
    EmployeeGrade,
    EmployeeStatus,
    EmploymentType,
    Gender,
)

# Leave Models
from app.models.people.leave import (
    Holiday,
    HolidayList,
    LeaveAllocation,
    LeaveApplication,
    LeaveApplicationStatus,
    LeaveType,
    LeaveTypePolicy,
)

# Payroll Models
from app.models.people.payroll import (
    PayrollEntry,
    PayrollEntryStatus,
    PayrollFrequency,
    SalaryComponent,
    SalaryComponentType,
    SalarySlip,
    SalarySlipDeduction,
    SalarySlipEarning,
    SalarySlipStatus,
    SalaryStructure,
    SalaryStructureAssignment,
    SalaryStructureDeduction,
    SalaryStructureEarning,
)

# Performance Models
from app.models.people.perf import (
    KPI,
    KRA,
    Appraisal,
    AppraisalCycle,
    AppraisalCycleStatus,
    AppraisalFeedback,
    AppraisalKRAScore,
    AppraisalStatus,
    AppraisalTemplate,
    AppraisalTemplateKRA,
    KPIStatus,
    Scorecard,
    ScorecardItem,
)

# Recruitment Models
from app.models.people.recruit import (
    ApplicantStatus,
    Interview,
    InterviewRound,
    InterviewStatus,
    JobApplicant,
    JobOffer,
    JobOpening,
    JobOpeningStatus,
    OfferStatus,
)

# Training Models
from app.models.people.training import (
    AttendeeStatus,
    TrainingAttendee,
    TrainingEvent,
    TrainingEventStatus,
    TrainingProgram,
    TrainingProgramStatus,
)

__all__ = [
    # Base mixins
    "PeopleBaseMixin",
    "AuditMixin",
    "SoftDeleteMixin",
    "StatusTrackingMixin",
    "ERPNextSyncMixin",
    "VersionMixin",
    # HR Core
    "Department",
    "Designation",
    "Employee",
    "EmployeeGrade",
    "EmployeeStatus",
    "EmploymentType",
    "Gender",
    # Payroll
    "PayrollEntry",
    "PayrollEntryStatus",
    "PayrollFrequency",
    "SalaryComponent",
    "SalaryComponentType",
    "SalarySlip",
    "SalarySlipDeduction",
    "SalarySlipEarning",
    "SalarySlipStatus",
    "SalaryStructure",
    "SalaryStructureAssignment",
    "SalaryStructureDeduction",
    "SalaryStructureEarning",
    # Leave
    "LeaveType",
    "LeaveTypePolicy",
    "HolidayList",
    "Holiday",
    "LeaveAllocation",
    "LeaveApplication",
    "LeaveApplicationStatus",
    # Attendance
    "ShiftType",
    "Attendance",
    "AttendanceStatus",
    # Recruitment
    "JobOpening",
    "JobOpeningStatus",
    "JobApplicant",
    "ApplicantStatus",
    "Interview",
    "InterviewRound",
    "InterviewStatus",
    "JobOffer",
    "OfferStatus",
    # Training
    "TrainingProgram",
    "TrainingProgramStatus",
    "TrainingEvent",
    "TrainingEventStatus",
    "TrainingAttendee",
    "AttendeeStatus",
    # Performance
    "AppraisalCycle",
    "AppraisalCycleStatus",
    "AppraisalTemplate",
    "AppraisalTemplateKRA",
    "KRA",
    "KPI",
    "KPIStatus",
    "Appraisal",
    "AppraisalStatus",
    "AppraisalKRAScore",
    "AppraisalFeedback",
    "Scorecard",
    "ScorecardItem",
    # Assets
    "AssetAssignment",
    "AssignmentStatus",
    "AssetCondition",
    # Expenses
    "ExpenseCategory",
    "ExpenseClaim",
    "ExpenseClaimStatus",
    "ExpenseClaimItem",
    "CashAdvance",
    "CashAdvanceStatus",
    "CorporateCard",
    "CardTransaction",
    "CardTransactionStatus",
    # Discipline
    "DisciplinaryCase",
    "CaseStatus",
    "ViolationType",
    "SeverityLevel",
    "CaseWitness",
    "CaseAction",
    "ActionType",
    "CaseDocument",
    "DisciplineDocumentType",
    "CaseResponse",
]
