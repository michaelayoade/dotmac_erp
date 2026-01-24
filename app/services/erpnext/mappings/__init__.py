"""
ERPNext to DotMac ERP Field Mappings.

Configuration for transforming ERPNext DocTypes to DotMac ERP models.
"""
from .accounts import AccountMapping, AccountCategoryMapping
from .items import ItemMapping, ItemCategoryMapping
from .assets import AssetMapping, AssetCategoryMapping
from .contacts import CustomerMapping, SupplierMapping
from .warehouses import WarehouseMapping

# HR Mappings
from .hr import (
    DepartmentMapping,
    DesignationMapping,
    EmploymentTypeMapping,
    EmployeeGradeMapping,
    EmployeeMapping,
)

# Leave Mappings
from .leave import (
    LeaveTypeMapping,
    LeaveAllocationMapping,
    LeaveApplicationMapping,
)

# Attendance Mappings
from .attendance import (
    ShiftTypeMapping,
    AttendanceMapping,
)

# Expense Mappings
from .expense import (
    ExpenseCategoryMapping,
    ExpenseClaimMapping,
    ExpenseClaimItemMapping,
)

# Project Mappings
from .projects import ProjectMapping

# Support/Ticket Mappings
from .support import TicketMapping, HDTicketMapping

# Material Request Mappings
from .material_request import (
    MaterialRequestMapping,
    MaterialRequestItemMapping,
)

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
