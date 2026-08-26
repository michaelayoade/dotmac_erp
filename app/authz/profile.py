"""ERP-owned authorization bundles over module permission declarations.

Modules declare permission questions. This product-level catalogue declares
the baseline ERP roles and the grants they receive. Persistence remains owned
by migrations and the RBAC service; importing this module performs no I/O.
"""

from __future__ import annotations

from app.authz.expense import EXPENSE_PERMISSION_DEFINITIONS
from app.authz.payment_execution import EXPENSE_PAYOUT_PERMISSION_DEFINITIONS

EXPENSE_BASELINE_ROLES: dict[str, str] = {
    "admin": "Full system administrator",
    "auditor": "Read-only audit access",
    "expense_admin": "Expense module administrator",
    "expense_approver": "Expense approval authority",
    "expense_processor": "Expense processing and reimbursement",
    "expense_reviewer": "Expense reviewer with manual weekly reset authority",
    "expense_reimburser": "Reimbursement-only processing",
    "department_manager": "Department head with team approvals",
    "employee": "Standard employee self-service",
}

_ALL_EXPENSE_PERMISSION_CODES = tuple(
    code for code, _description in EXPENSE_PERMISSION_DEFINITIONS
)

EXPENSE_ROLE_GRANTS: dict[str, tuple[str, ...]] = {
    "admin": _ALL_EXPENSE_PERMISSION_CODES,
    "auditor": ("expense:access", "expense:claims:read"),
    "expense_admin": (
        "expense:access",
        "expense:dashboard",
        "expense:claims:read",
        "expense:claims:create",
        "expense:claims:update",
        "expense:claims:delete",
        "expense:claims:submit",
        "expense:claims:approve:tier1",
        "expense:claims:approve:tier2",
        "expense:claims:approve:tier3",
        "expense:claims:reject",
        "expense:claims:reimburse",
        "expense:claims:post",
        "expense:categories:read",
        "expense:categories:manage",
        "expense:policies:read",
        "expense:policies:manage",
        "expense:advances:read",
        "expense:advances:create",
        "expense:advances:approve:tier1",
        "expense:advances:approve:tier2",
        "expense:advances:approve:tier3",
        "expense:advances:disburse",
        "expense:advances:settle",
        "expense:cards:read",
        "expense:cards:manage",
        "expense:cards:assign",
        "expense:cards:transactions:read",
        "expense:cards:transactions:reconcile",
        "expense:reports:read",
        "expense:reports:export",
    ),
    "expense_approver": (
        "expense:access",
        "expense:dashboard",
        "expense:claims:read",
        "expense:claims:read_team",
        "expense:claims:approve:tier2",
        "expense:claims:reject",
        "expense:advances:read",
        "expense:advances:approve:tier2",
        "expense:reports:read",
    ),
    "expense_processor": (
        "expense:access",
        "expense:dashboard",
        "expense:claims:read",
        "expense:claims:reimburse",
        "expense:claims:post",
        "expense:advances:read",
        "expense:advances:disburse",
        "expense:advances:settle",
        "expense:cards:transactions:read",
        "expense:cards:transactions:reconcile",
        "expense:reports:read",
    ),
    "expense_reviewer": (
        "expense:access",
        "expense:dashboard",
        "expense:claims:read",
        "expense:policies:read",
        "expense:limits:review",
        "expense:reports:read",
    ),
    "expense_reimburser": (
        "expense:access",
        "expense:dashboard",
        "expense:claims:read",
        "expense:claims:reimburse",
        "expense:reports:read",
    ),
    "department_manager": (
        "expense:access",
        "expense:claims:read_team",
        "expense:claims:approve:tier1",
        "expense:claims:reject",
    ),
    "employee": (
        "expense:access",
        "expense:claims:read_own",
        "expense:claims:create",
        "expense:claims:update",
        "expense:claims:delete",
        "expense:claims:submit",
        "expense:advances:read_own",
        "expense:advances:create",
    ),
}

_ALL_EXPENSE_PAYOUT_PERMISSION_CODES = tuple(
    code for code, _description in EXPENSE_PAYOUT_PERMISSION_DEFINITIONS
)

EXPENSE_PAYOUT_ROLE_GRANTS: dict[str, tuple[str, ...]] = {
    "admin": _ALL_EXPENSE_PAYOUT_PERMISSION_CODES,
    "expense_admin": _ALL_EXPENSE_PAYOUT_PERMISSION_CODES,
    "expense_processor": _ALL_EXPENSE_PAYOUT_PERMISSION_CODES,
    "expense_reimburser": _ALL_EXPENSE_PAYOUT_PERMISSION_CODES,
}

__all__ = [
    "EXPENSE_BASELINE_ROLES",
    "EXPENSE_PAYOUT_ROLE_GRANTS",
    "EXPENSE_ROLE_GRANTS",
]
