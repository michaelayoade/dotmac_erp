"""Provision Expense RBAC, payout dependencies, and their baseline grants.

The Expense routes have referenced these permission keys for years, but the
production deployment path runs migrations rather than ``scripts/seed_rbac``.
This migration closes that gap without making the mutable full seed part of a
deployment.

This is deliberately additive.  Existing roles, permissions, descriptions,
memberships, direct grants, and role-permission links are preserved.  An
inactive desired role or permission is a conflict: the migration refuses to
silently reactivate an operator-disabled record.

Revision ID: 20260826_expense_permissions
Revises: 20260825_retire_dotmac_crm
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from alembic import op

revision: str = "20260826_expense_permissions"
down_revision: str | None = "20260825_retire_dotmac_crm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Frozen from the authored declarations at the revision that introduced this
# migration. A migration must remain executable after those declarations move.
EXPENSE_PERMISSIONS: tuple[tuple[str, str], ...] = (
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

EXPENSE_PAYOUT_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("payments:read", "View payment intents and resolve bank details"),
    ("payments:expense:initialize", "Prepare an expense reimbursement payout"),
    (
        "payments:transfer:initiate",
        "Execute an outbound expense transfer (moves money)",
    ),
)

ROLE_DESCRIPTIONS: dict[str, str] = {
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

_ALL_EXPENSE_PERMISSION_CODES = tuple(code for code, _ in EXPENSE_PERMISSIONS)

ROLE_EXPENSE_GRANTS: dict[str, tuple[str, ...]] = {
    "admin": _ALL_EXPENSE_PERMISSION_CODES,
    "auditor": (
        "expense:access",
        "expense:claims:read",
    ),
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

ROLE_EXPENSE_PAYOUT_GRANTS: dict[str, tuple[str, ...]] = {
    "admin": tuple(code for code, _ in EXPENSE_PAYOUT_PERMISSIONS),
    "expense_admin": tuple(code for code, _ in EXPENSE_PAYOUT_PERMISSIONS),
    "expense_processor": tuple(code for code, _ in EXPENSE_PAYOUT_PERMISSIONS),
    "expense_reimburser": tuple(code for code, _ in EXPENSE_PAYOUT_PERMISSIONS),
}

ROLE_GRANTS: dict[str, tuple[str, ...]] = {
    role: (
        *ROLE_EXPENSE_GRANTS.get(role, ()),
        *ROLE_EXPENSE_PAYOUT_GRANTS.get(role, ()),
    )
    for role in ROLE_DESCRIPTIONS
}


def _ensure_active_role(conn: Any, role_name: str, description: str) -> Any:
    conn.exec_driver_sql(
        """
        INSERT INTO roles (id, name, description, is_active, created_at, updated_at)
        VALUES (gen_random_uuid(), %s, %s, true, NOW(), NOW())
        ON CONFLICT (name) DO NOTHING
        """,
        (role_name, description),
    )
    row = conn.exec_driver_sql(
        "SELECT id, is_active FROM roles WHERE name = %s",
        (role_name,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Expense RBAC role was not materialized: {role_name}")
    if not row[1]:
        raise RuntimeError(f"Expense RBAC role is inactive: {role_name}")
    return row[0]


def _ensure_active_permission(conn: Any, code: str, description: str) -> Any:
    conn.exec_driver_sql(
        """
        INSERT INTO permissions (
            id, key, description, is_active, created_at, updated_at
        )
        VALUES (gen_random_uuid(), %s, %s, true, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
        """,
        (code, description),
    )
    row = conn.exec_driver_sql(
        "SELECT id, is_active FROM permissions WHERE key = %s",
        (code,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Expense RBAC permission was not materialized: {code}")
    if not row[1]:
        raise RuntimeError(f"Expense RBAC permission is inactive: {code}")
    return row[0]


def upgrade() -> None:
    conn = op.get_bind()
    role_ids = {
        role_name: _ensure_active_role(conn, role_name, description)
        for role_name, description in ROLE_DESCRIPTIONS.items()
    }
    permission_ids = {
        code: _ensure_active_permission(conn, code, description)
        for code, description in (*EXPENSE_PERMISSIONS, *EXPENSE_PAYOUT_PERMISSIONS)
    }

    for role_name, permission_codes in ROLE_GRANTS.items():
        for permission_code in permission_codes:
            conn.exec_driver_sql(
                """
                INSERT INTO role_permissions (id, role_id, permission_id)
                VALUES (gen_random_uuid(), %s, %s)
                ON CONFLICT (role_id, permission_id) DO NOTHING
                """,
                (role_ids[role_name], permission_ids[permission_code]),
            )


def downgrade() -> None:
    # Additive baseline grants cannot be distinguished from operator grants in
    # the current schema.  Removing them would therefore be destructive.
    pass
