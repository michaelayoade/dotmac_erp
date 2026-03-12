"""Add unique constraint on variation_id and seed missing AP permissions.

1. Partial unique index on (organization_id, variation_id) WHERE variation_id IS NOT NULL
   — enforces DB-level idempotency for variation amendments.
2. Seeds ap:payments:update, ap:payments:delete, ap:purchase_orders:update
   permissions and assigns them to the appropriate roles.

Revision ID: 20260311_po_var_uq_perms
Revises: 20260311_finance_perms
Create Date: 2026-03-11
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa

from alembic import op

revision: str = "20260311_po_var_uq_perms"
down_revision: Union[str, None] = "20260311_finance_perms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# New permissions to add
NEW_PERMISSIONS = [
    ("ap:payments:update", "Modify draft supplier payments"),
    ("ap:payments:delete", "Delete draft supplier payments"),
    ("ap:purchase_orders:update", "Modify draft purchase orders"),
]

# Roles that should get the new update/delete permissions.
# Keep ap_clerk least-privilege; 20260311_finance_perms already seeds its
# narrower baseline access and 20260312_fix_ap_perms corrects existing DBs.
ROLES_WITH_UPDATE_DELETE = [
    "admin",
    "finance_director",
    "finance_manager",
    "senior_accountant",
    "accountant",
]


def _insert_permission(conn, perm_key: str, description: str) -> None:
    """Insert a permission row in a way that works on old and new schemas."""
    conn.exec_driver_sql(
        """
        INSERT INTO permissions (
            id,
            key,
            description,
            is_active,
            created_at,
            updated_at
        )
        VALUES (gen_random_uuid(), %s, %s, true, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
        """,
        (perm_key, description),
    )


def _insert_role_permission(conn, role_id, perm_id) -> None:
    """Insert a role-permission mapping without relying on DB defaults."""
    conn.exec_driver_sql(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        VALUES (gen_random_uuid(), %s, %s)
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """,
        (role_id, perm_id),
    )


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Add partial unique index on variation_id (idempotent)
    op.create_index(
        "uq_po_variation_id",
        "purchase_order",
        ["organization_id", "variation_id"],
        schema="ap",
        unique=True,
        postgresql_where=sa.text("variation_id IS NOT NULL"),
    )

    # 2. Seed new permissions
    for perm_key, description in NEW_PERMISSIONS:
        _insert_permission(conn, perm_key, description)

    # 3. Assign new permissions to appropriate roles
    for role_name in ROLES_WITH_UPDATE_DELETE:
        role_row = conn.exec_driver_sql(
            "SELECT id FROM roles WHERE name = %s", (role_name,)
        ).fetchone()
        if not role_row:
            continue
        role_id = role_row[0]

        for perm_key, _ in NEW_PERMISSIONS:
            perm_row = conn.exec_driver_sql(
                "SELECT id FROM permissions WHERE key = %s", (perm_key,)
            ).fetchone()
            if not perm_row:
                continue
            perm_id = perm_row[0]

            _insert_role_permission(conn, role_id, perm_id)


def downgrade() -> None:
    op.drop_index("uq_po_variation_id", table_name="purchase_order", schema="ap")
    # Permissions remain — harmless if not checked
