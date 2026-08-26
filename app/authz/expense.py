"""Expense-owned permission definitions.

Permission codes are stable opaque product contracts. This module declares
the questions Expense asks; it deliberately does not decide which ERP roles
receive them.
"""

from __future__ import annotations

EXPENSE_PERMISSION_DEFINITIONS: tuple[tuple[str, str], ...] = (
    ("expense:access", "Access expense module"),
    ("expense:dashboard", "View expense dashboard"),
    ("expense:claims:read", "View all expense claims"),
    ("expense:claims:read_team", "View team expense claims"),
    ("expense:claims:read_own", "View own expense claims"),
    ("expense:claims:create", "Submit expense claims"),
    ("expense:claims:update", "Modify draft claims"),
    ("expense:claims:delete", "Delete draft claims"),
    ("expense:claims:submit", "Submit claims for approval"),
    ("expense:claims:approve:tier1", "Approve expenses (Tier 1 limit)"),
    ("expense:claims:approve:tier2", "Approve expenses (Tier 2 limit)"),
    ("expense:claims:approve:tier3", "Approve expenses (unlimited)"),
    ("expense:claims:reject", "Reject expense claims"),
    ("expense:claims:reimburse", "Process reimbursements"),
    ("expense:claims:post", "Post expenses to GL"),
    ("expense:categories:read", "View expense categories"),
    ("expense:categories:manage", "Manage expense categories"),
    ("expense:policies:read", "View expense policies"),
    ("expense:policies:manage", "Manage expense policies"),
    (
        "expense:limits:review",
        "Review approver decisions and reset weekly budgets",
    ),
    ("expense:advances:read", "View all cash advances"),
    ("expense:advances:read_own", "View own cash advances"),
    ("expense:advances:create", "Request cash advances"),
    ("expense:advances:approve:tier1", "Approve advances (Tier 1)"),
    ("expense:advances:approve:tier2", "Approve advances (Tier 2)"),
    ("expense:advances:approve:tier3", "Approve advances (unlimited)"),
    ("expense:advances:disburse", "Disburse cash advances"),
    ("expense:advances:settle", "Settle cash advances"),
    ("expense:cards:read", "View corporate cards"),
    ("expense:cards:manage", "Manage corporate cards"),
    ("expense:cards:assign", "Assign cards to employees"),
    ("expense:cards:transactions:read", "View card transactions"),
    (
        "expense:cards:transactions:reconcile",
        "Reconcile card transactions",
    ),
    ("expense:reports:read", "View expense reports"),
    ("expense:reports:export", "Export expense data"),
)

__all__ = ["EXPENSE_PERMISSION_DEFINITIONS"]
