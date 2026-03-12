"""Seed modular AP permissions for existing finance roles.

Ensures backward-compatible role transition: users with existing
finance roles (finance_director, finance_manager, accountant, etc.)
automatically receive the new granular AP permissions.

This is a data migration — it inserts permission rows and
role_permission mappings if they don't already exist.

Revision ID: 20260311_finance_perms
Revises: 20260311_po_amendment
Create Date: 2026-03-11
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "20260311_finance_perms"
down_revision: Union[str, None] = "20260311_po_amendment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# AP permissions that should exist (from seed_rbac.py)
AP_PERMISSIONS = [
    "ap:suppliers:read",
    "ap:suppliers:create",
    "ap:suppliers:update",
    "ap:suppliers:delete",
    "ap:invoices:read",
    "ap:invoices:create",
    "ap:invoices:update",
    "ap:invoices:submit",
    "ap:invoices:approve",
    "ap:invoices:post",
    "ap:invoices:void",
    "ap:payments:read",
    "ap:payments:create",
    "ap:payments:update",
    "ap:payments:delete",
    "ap:payments:post",
    "ap:payments:void",
    "ap:payments:approve:tier1",
    "ap:payments:approve:tier2",
    "ap:payments:approve:tier3",
    "ap:purchase_orders:read",
    "ap:purchase_orders:create",
    "ap:purchase_orders:update",
    "ap:purchase_orders:submit",
    "ap:purchase_orders:approve",
    "ap:purchase_orders:void",
    "ap:goods_receipts:read",
    "ap:goods_receipts:create",
    "ap:goods_receipts:update",
    "ap:goods_receipts:approve",
    "ap:payment_batches:read",
    "ap:payment_batches:create",
    "ap:payment_batches:update",
    "ap:payment_batches:approve",
    "ap:payment_batches:process",
    "ap:payment_batches:export",
    "ap:aging:read",
]

# Role → permission mapping for backward compatibility
# Users with these roles get all listed permissions automatically
ROLE_AP_PERMS = {
    # Full access roles
    "admin": AP_PERMISSIONS,
    "finance_director": AP_PERMISSIONS,
    "finance_manager": AP_PERMISSIONS,
    # Accountant: read + create + update + delete + submit (no approve/post/void)
    "senior_accountant": [
        p
        for p in AP_PERMISSIONS
        if not any(a in p for a in [":approve", ":post", ":void"])
    ]
    + ["ap:invoices:post"],  # senior accountants can post
    "accountant": [
        p
        for p in AP_PERMISSIONS
        if not any(a in p for a in [":approve", ":post", ":void"])
    ],
    # AP clerk: read + create + update + delete + submit + tier1 approve
    # (no tier2/tier3 approvals, no post, no void)
    "ap_clerk": [
        p
        for p in AP_PERMISSIONS
        if p
        not in [
            "ap:payments:approve:tier2",
            "ap:payments:approve:tier3",
            "ap:invoices:approve",
            "ap:invoices:post",
            "ap:invoices:void",
            "ap:payments:post",
            "ap:payments:void",
            "ap:purchase_orders:approve",
            "ap:purchase_orders:void",
            "ap:goods_receipts:approve",
            "ap:payment_batches:approve",
            "ap:payment_batches:process",
        ]
    ],
    # Junior: read only
    "junior_accountant": [p for p in AP_PERMISSIONS if ":read" in p],
    # Viewer: read only
    "finance_viewer": [p for p in AP_PERMISSIONS if ":read" in p],
    # Auditor: read only
    "auditor": [p for p in AP_PERMISSIONS if ":read" in p],
}


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Ensure all AP permissions exist
    for perm_key in AP_PERMISSIONS:
        exists = conn.exec_driver_sql(
            "SELECT 1 FROM permissions WHERE key = %s", (perm_key,)
        ).fetchone()
        if not exists:
            conn.exec_driver_sql(
                "INSERT INTO permissions (id, key, description, is_active) "
                "VALUES (gen_random_uuid(), %s, %s, true)",
                (perm_key, f"AP permission: {perm_key}"),
            )

    # 2. Assign permissions to roles (idempotent)
    for role_name, perms in ROLE_AP_PERMS.items():
        # Get role ID
        role_row = conn.exec_driver_sql(
            "SELECT id FROM roles WHERE name = %s", (role_name,)
        ).fetchone()
        if not role_row:
            continue
        role_id = role_row[0]

        for perm_key in perms:
            perm_row = conn.exec_driver_sql(
                "SELECT id FROM permissions WHERE key = %s", (perm_key,)
            ).fetchone()
            if not perm_row:
                continue
            perm_id = perm_row[0]

            # Check if mapping exists
            exists = conn.exec_driver_sql(
                "SELECT 1 FROM role_permissions "
                "WHERE role_id = %s AND permission_id = %s",
                (role_id, perm_id),
            ).fetchone()
            if not exists:
                conn.exec_driver_sql(
                    "INSERT INTO role_permissions (id, role_id, permission_id) "
                    "VALUES (gen_random_uuid(), %s, %s)",
                    (role_id, perm_id),
                )


def downgrade() -> None:
    # No-op: removing permissions could break access.
    # Permissions remain but are harmless if not checked.
    pass
