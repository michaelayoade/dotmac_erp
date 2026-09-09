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

from typing import TYPE_CHECKING, Any

from app.models.domain_settings import SettingDomain
from app.services.setting_domain_declaration import ModuleSettingDomains

PEOPLE_SETTINGS_DOMAIN = SettingDomain("people")
REQUIRE_DOB_FOR_CHECKIN_SETTING = "require_dob_for_erp_checkin"
SETTING_DOMAINS = ModuleSettingDomains(setting_domains=(PEOPLE_SETTINGS_DOMAIN,))

if TYPE_CHECKING:  # pragma: no cover
    from .attendance import AttendanceService
    from .expense import ExpenseService
    from .hr import EmployeeService, OrganizationService
    from .leave import LeaveService
    from .perf import PerformanceService
    from .recruit import RecruitmentService
    from .training import TrainingService

__all__ = [
    "PEOPLE_SETTINGS_DOMAIN",
    "REQUIRE_DOB_FOR_CHECKIN_SETTING",
    "SETTING_DOMAINS",
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


def __getattr__(name: str) -> Any:  # pragma: no cover
    """Lazy attribute loading to avoid importing large submodules at package import."""
    if name == "AttendanceService":
        from .attendance import AttendanceService as _AttendanceService

        return _AttendanceService
    if name == "ExpenseService":
        from .expense import ExpenseService as _ExpenseService

        return _ExpenseService
    if name == "EmployeeService":
        from .hr import EmployeeService as _EmployeeService

        return _EmployeeService
    if name == "OrganizationService":
        from .hr import OrganizationService as _OrganizationService

        return _OrganizationService
    if name == "LeaveService":
        from .leave import LeaveService as _LeaveService

        return _LeaveService
    if name == "PerformanceService":
        from .perf import PerformanceService as _PerformanceService

        return _PerformanceService
    if name == "RecruitmentService":
        from .recruit import RecruitmentService as _RecruitmentService

        return _RecruitmentService
    if name == "TrainingService":
        from .training import TrainingService as _TrainingService

        return _TrainingService
    raise AttributeError(name)
