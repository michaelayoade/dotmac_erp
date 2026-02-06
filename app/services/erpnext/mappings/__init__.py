"""
ERPNext to DotMac ERP Field Mappings.

Configuration for transforming ERPNext DocTypes to DotMac ERP models.
"""

from .accounts import AccountCategoryMapping, AccountMapping
from .assets import AssetCategoryMapping, AssetMapping

# Attendance Mappings
from .attendance import (
    AttendanceMapping,
    ShiftTypeMapping,
)
from .contacts import CustomerMapping, SupplierMapping

# Expense Mappings
from .expense import (
    ExpenseCategoryMapping,
    ExpenseClaimItemMapping,
    ExpenseClaimMapping,
)

# HR Mappings
from .hr import (
    DepartmentMapping,
    DesignationMapping,
    EmployeeGradeMapping,
    EmployeeMapping,
    EmploymentTypeMapping,
)
from .items import ItemCategoryMapping, ItemMapping

# Leave Mappings
from .leave import (
    LeaveAllocationMapping,
    LeaveApplicationMapping,
    LeaveTypeMapping,
)

# Material Request Mappings
from .material_request import (
    MaterialRequestItemMapping,
    MaterialRequestMapping,
)

# Project Mappings
from .projects import ProjectMapping

# Support/Ticket Mappings
from .support import HDTicketMapping, TicketMapping
from .warehouses import WarehouseMapping

__all__ = [
    # Finance
    "AccountMapping",
    "AccountCategoryMapping",
    "ItemMapping",
    "ItemCategoryMapping",
    "AssetMapping",
    "AssetCategoryMapping",
    "CustomerMapping",
    "SupplierMapping",
    "WarehouseMapping",
    # HR
    "DepartmentMapping",
    "DesignationMapping",
    "EmploymentTypeMapping",
    "EmployeeGradeMapping",
    "EmployeeMapping",
    # Leave
    "LeaveTypeMapping",
    "LeaveAllocationMapping",
    "LeaveApplicationMapping",
    # Attendance
    "ShiftTypeMapping",
    "AttendanceMapping",
    # Expense
    "ExpenseCategoryMapping",
    "ExpenseClaimMapping",
    "ExpenseClaimItemMapping",
    # Projects
    "ProjectMapping",
    # Support
    "TicketMapping",
    "HDTicketMapping",
    # Material Request
    "MaterialRequestMapping",
    "MaterialRequestItemMapping",
]
