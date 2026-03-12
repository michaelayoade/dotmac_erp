"""Seed modular AP permissions and assign to finance roles.

Ensures backward-compatible role transition: users with existing
finance roles automatically receive granular AP permissions matching
the seed_rbac.py source of truth.

All inserts use ON CONFLICT DO NOTHING for full idempotency.

Revision ID: 20260311_seed_ap_perms
Revises: 20260311_fix_constraints
Create Date: 2026-03-11
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "20260311_seed_ap_perms"
down_revision: Union[str, None] = "20260311_fix_constraints"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Permission definitions (complete set from seed_rbac.py)
# ---------------------------------------------------------------------------

AP_PERMISSIONS: dict[str, str] = {
    # Suppliers
    "ap:suppliers:read": "View suppliers",
    "ap:suppliers:create": "Create suppliers",
    "ap:suppliers:update": "Edit suppliers",
    "ap:suppliers:delete": "Delete suppliers",
    # Invoices
    "ap:invoices:read": "View supplier invoices",
    "ap:invoices:create": "Create supplier invoices",
    "ap:invoices:update": "Edit draft supplier invoices",
    "ap:invoices:submit": "Submit invoices for approval",
    "ap:invoices:approve": "Approve supplier invoices",
    "ap:invoices:post": "Post supplier invoices to GL",
    "ap:invoices:void": "Void posted supplier invoices",
    # Payments
    "ap:payments:read": "View supplier payments",
    "ap:payments:create": "Create supplier payments",
    "ap:payments:update": "Modify draft supplier payments",
    "ap:payments:delete": "Delete draft supplier payments",
    "ap:payments:post": "Post supplier payments",
    "ap:payments:void": "Void posted payments",
    "ap:payments:approve:tier1": "Approve payments (tier 1)",
    "ap:payments:approve:tier2": "Approve payments (tier 2)",
    "ap:payments:approve:tier3": "Approve payments (tier 3)",
    # Purchase Orders
    "ap:purchase_orders:read": "View purchase orders",
    "ap:purchase_orders:create": "Create purchase orders",
    "ap:purchase_orders:update": "Modify draft purchase orders",
    "ap:purchase_orders:delete": "Delete draft purchase orders",
    "ap:purchase_orders:submit": "Submit POs for approval",
    "ap:purchase_orders:approve": "Approve purchase orders",
    "ap:purchase_orders:void": "Void purchase orders",
    # Goods Receipts
    "ap:goods_receipts:read": "View goods receipts",
    "ap:goods_receipts:create": "Create goods receipts",
    "ap:goods_receipts:update": "Edit goods receipts",
    "ap:goods_receipts:approve": "Approve goods receipts",
    # Payment Batches
    "ap:payment_batches:read": "View payment batches",
    "ap:payment_batches:create": "Create payment batches",
    "ap:payment_batches:update": "Edit payment batches",
    "ap:payment_batches:approve": "Approve payment batches",
    "ap:payment_batches:process": "Process payment batches",
    "ap:payment_batches:export": "Export payment batches",
    # Aging
    "ap:aging:read": "View AP aging reports",
}

# ---------------------------------------------------------------------------
# Role → permission mapping (exact match with seed_rbac.py)
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS: dict[str, list[str]] = {
    # Admin: all AP permissions
    "admin": list(AP_PERMISSIONS.keys()),

    # Finance Director: all AP permissions
    "finance_director": list(AP_PERMISSIONS.keys()),

    # Finance Manager: all except tier3 approval
    "finance_manager": [
        "ap:suppliers:read", "ap:suppliers:create", "ap:suppliers:update",
        "ap:suppliers:delete",
        "ap:invoices:read", "ap:invoices:create", "ap:invoices:update",
        "ap:invoices:submit", "ap:invoices:approve", "ap:invoices:post",
        "ap:invoices:void",
        "ap:payments:read", "ap:payments:create", "ap:payments:update",
        "ap:payments:delete", "ap:payments:post", "ap:payments:void",
        "ap:payments:approve:tier1", "ap:payments:approve:tier2",
        "ap:purchase_orders:read", "ap:purchase_orders:create",
        "ap:purchase_orders:update", "ap:purchase_orders:delete",
        "ap:purchase_orders:submit", "ap:purchase_orders:approve",
        "ap:purchase_orders:void",
        "ap:goods_receipts:read", "ap:goods_receipts:create",
        "ap:goods_receipts:update", "ap:goods_receipts:approve",
        "ap:payment_batches:read", "ap:payment_batches:create",
        "ap:payment_batches:update", "ap:payment_batches:approve",
        "ap:payment_batches:process", "ap:payment_batches:export",
        "ap:aging:read",
    ],

    # Senior Accountant: CRUD + post + tier1 approval (no void, no tier2/3)
    "senior_accountant": [
        "ap:suppliers:read", "ap:suppliers:create", "ap:suppliers:update",
        "ap:suppliers:delete",
        "ap:invoices:read", "ap:invoices:create", "ap:invoices:update",
        "ap:invoices:submit", "ap:invoices:post",
        "ap:payments:read", "ap:payments:create", "ap:payments:update",
        "ap:payments:delete", "ap:payments:approve:tier1",
        "ap:purchase_orders:read", "ap:purchase_orders:create",
        "ap:purchase_orders:update", "ap:purchase_orders:delete",
        "ap:purchase_orders:submit",
        "ap:goods_receipts:read", "ap:goods_receipts:create",
        "ap:goods_receipts:update",
        "ap:payment_batches:read", "ap:payment_batches:create",
        "ap:payment_batches:update", "ap:payment_batches:export",
        "ap:aging:read",
    ],

    # Accountant: CRUD + submit (no post, no void, no approve)
    "accountant": [
        "ap:suppliers:read", "ap:suppliers:create", "ap:suppliers:update",
        "ap:invoices:read", "ap:invoices:create", "ap:invoices:update",
        "ap:invoices:submit",
        "ap:payments:read", "ap:payments:create", "ap:payments:update",
        "ap:payments:delete",
        "ap:purchase_orders:read", "ap:purchase_orders:create",
        "ap:purchase_orders:update", "ap:purchase_orders:delete",
        "ap:purchase_orders:submit",
        "ap:goods_receipts:read", "ap:goods_receipts:create",
        "ap:goods_receipts:update",
        "ap:payment_batches:read", "ap:payment_batches:create",
        "ap:payment_batches:update",
        "ap:aging:read",
    ],

    # AP Clerk: least-privilege data entry
    "ap_clerk": [
        "ap:suppliers:read", "ap:suppliers:create", "ap:suppliers:update",
        "ap:invoices:read", "ap:invoices:create", "ap:invoices:update",
        "ap:payments:read", "ap:payments:create",
        "ap:purchase_orders:read",
        "ap:goods_receipts:read", "ap:goods_receipts:create",
        "ap:aging:read",
    ],

    # Junior Accountant: minimal create
    "junior_accountant": [
        "ap:suppliers:read",
        "ap:invoices:read", "ap:invoices:create",
        "ap:goods_receipts:read",
    ],

    # Finance Viewer: read-only
    "finance_viewer": [
        "ap:suppliers:read",
        "ap:invoices:read",
        "ap:payments:read",
        "ap:aging:read",
    ],

    # Auditor: read-only
    "auditor": [
        "ap:suppliers:read",
        "ap:invoices:read",
        "ap:payments:read",
        "ap:aging:read",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_permission(conn, perm_key: str, description: str) -> None:
    """Insert a permission row idempotently."""
    conn.exec_driver_sql(
        """
        INSERT INTO permissions (id, key, description, is_active, created_at, updated_at)
        VALUES (gen_random_uuid(), %s, %s, true, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
        """,
        (perm_key, description),
    )


def _insert_role_permission(conn, role_id, perm_id) -> None:
    """Insert a role-permission mapping idempotently."""
    conn.exec_driver_sql(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        VALUES (gen_random_uuid(), %s, %s)
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """,
        (role_id, perm_id),
    )


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------

def upgrade() -> None:
    conn = op.get_bind()

    # 1. Seed all AP permissions
    for perm_key, description in AP_PERMISSIONS.items():
        _insert_permission(conn, perm_key, description)

    # 2. Build a permission key → id lookup (single query)
    perm_rows = conn.exec_driver_sql(
        "SELECT key, id FROM permissions WHERE key LIKE 'ap:%%'"
    ).fetchall()
    perm_map = {row[0]: row[1] for row in perm_rows}

    # 3. Assign permissions to each role
    for role_name, perm_keys in ROLE_PERMISSIONS.items():
        role_row = conn.exec_driver_sql(
            "SELECT id FROM roles WHERE name = %s", (role_name,)
        ).fetchone()
        if not role_row:
            continue
        role_id = role_row[0]

        for perm_key in perm_keys:
            perm_id = perm_map.get(perm_key)
            if perm_id:
                _insert_role_permission(conn, role_id, perm_id)


def downgrade() -> None:
    # Permissions remain — they are harmless if unchecked and removing
    # them could break access for users who depend on them.
    pass
