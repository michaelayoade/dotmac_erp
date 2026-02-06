"""
ERPNext Sync Services.

Services for syncing data from ERPNext to DotMac ERP.
"""

from .attendance import (
    AttendanceSyncService,
    ShiftTypeSyncService,
)
from .base import BaseSyncService, SyncResult

# Expense Sync Services
from .expense import (
    ExpenseCategorySyncService,
    ExpenseClaimSyncService,
)

# HR Sync Services
from .hr import (
    DepartmentSyncService,
    DesignationSyncService,
    EmployeeGradeSyncService,
    EmployeeSyncService,
    EmploymentTypeSyncService,
)

# Leave & Attendance Sync Services
from .leave import (
    LeaveAllocationSyncService,
    LeaveApplicationSyncService,
    LeaveTypeSyncService,
)

# Material Request Sync Services
from .material_request import MaterialRequestSyncService
from .orchestrator import ERPNextSyncOrchestrator

# Project & Support Sync Services
from .projects import ProjectSyncService
from .support import TicketSyncService

__all__ = [
    # Base
    "BaseSyncService",
    "SyncResult",
    "ERPNextSyncOrchestrator",
    # HR
    "DepartmentSyncService",
    "DesignationSyncService",
    "EmploymentTypeSyncService",
    "EmployeeGradeSyncService",
    "EmployeeSyncService",
    # Leave & Attendance
    "LeaveTypeSyncService",
    "LeaveAllocationSyncService",
    "LeaveApplicationSyncService",
    "ShiftTypeSyncService",
    "AttendanceSyncService",
    # Expense
    "ExpenseCategorySyncService",
    "ExpenseClaimSyncService",
    # Project & Support
    "ProjectSyncService",
    "TicketSyncService",
    # Material Request
    "MaterialRequestSyncService",
]
