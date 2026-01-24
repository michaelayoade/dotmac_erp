"""
ERPNext Sync Services.

Services for syncing data from ERPNext to DotMac ERP.
"""
from .base import BaseSyncService, SyncResult
from .orchestrator import ERPNextSyncOrchestrator

# HR Sync Services
from .hr import (
    DepartmentSyncService,
    DesignationSyncService,
    EmploymentTypeSyncService,
    EmployeeGradeSyncService,
    EmployeeSyncService,
)

# Leave & Attendance Sync Services
from .leave import (
    LeaveTypeSyncService,
    LeaveAllocationSyncService,
    LeaveApplicationSyncService,
)
from .attendance import (
    ShiftTypeSyncService,
    AttendanceSyncService,
)

# Expense Sync Services
from .expense import (
    ExpenseCategorySyncService,
    ExpenseClaimSyncService,
)

# Project & Support Sync Services
from .projects import ProjectSyncService
from .support import TicketSyncService

# Material Request Sync Services
from .material_request import MaterialRequestSyncService

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
