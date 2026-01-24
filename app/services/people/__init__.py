"""
People (HR/HRIS) Services.

This module contains all business logic services for the People/HR functionality.

Service modules:
- hr: Employee, Department, Designation management
- payroll: Salary calculation, slip generation, payroll runs
- leave: Leave allocation, application, balance tracking
- attendance: Shift management, attendance marking
- recruit: Job posting, applicant tracking, interviews
- training: Program and event management
- perf: KPI and appraisal management

Integration services:
- integrations.payroll_gl_adapter: Posts payroll entries to GL
- integrations.expense_ap_adapter: Creates AP invoices from expense claims
"""
from .hr import (
    EmployeeService,
    OrganizationService,
)
from .leave import LeaveService
from .attendance import AttendanceService
from .recruit import RecruitmentService
from .training import TrainingService
from .perf import PerformanceService
from .expense import ExpenseService

__all__ = [
    # HR Core
    "EmployeeService",
    "OrganizationService",
    # Leave Management
    "LeaveService",
    # Attendance
    "AttendanceService",
    # Recruitment
    "RecruitmentService",
    # Training
    "TrainingService",
    # Performance
    "PerformanceService",
    # Expenses
    "ExpenseService",
]
