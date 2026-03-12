"""Fix AP permission grants for least-privilege compliance.

1. Seed ``ap:purchase_orders:delete`` permission (separate from :void).
2. Grant new permission to appropriate roles (NOT ap_clerk).
3. Revoke excess PO/payment permissions from ap_clerk that were
   over-granted by the 20260311_finance_perms migration filter.
4. Update the modular-migration ap_clerk exclusion list in the
   ``20260311_po_var_uq_perms`` migration's seed for ap_clerk.

After this migration the ap_clerk role matches seed_rbac.py:
  - PO: read only
  - Payments: read + create only
  - Invoices: read + create + update

Revision ID: 20260312_fix_ap_perms
Revises: 20260311_po_var_uq_perms
Create Date: 2026-03-12
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "20260312_fix_ap_perms"
down_revision: Union[str, None] = "20260311_po_var_uq_perms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── New permission ──────────────────────────────────────────────────
NEW_PERM = ("ap:purchase_orders:delete", "Delete draft purchase orders")

# Roles that get the new delete permission (NOT ap_clerk)
ROLES_WITH_PO_DELETE = [
    "admin",
    "finance_director",
    "finance_manager",
    "senior_accountant",
    "accountant",
]

# ── Excess permissions to revoke from ap_clerk ─────────────────────
# The 20260311_finance_perms migration used a broad filter that
# inadvertently granted these to ap_clerk.  seed_rbac.py restricts
# ap_clerk to PO:read, payments:{read,create}, invoices:{read,create,update}.
AP_CLERK_EXCESS = [
    "ap:purchase_orders:create",
    "ap:purchase_orders:update",
    "ap:purchase_orders:submit",
    "ap:payments:update",
    "ap:payments:delete",
    "ap:suppliers:delete",
    "ap:invoices:submit",
    "ap:payment_batches:create",
    "ap:payment_batches:update",
    "ap:payment_batches:export",
]


def _get_id(conn, table: str, column: str, value: str):
    """Return the id column for a row, or None."""
    row = conn.exec_driver_sql(
        f"SELECT id FROM {table} WHERE {column} = %s",  # noqa: S608
        (value,),
    ).fetchone()
    return row[0] if row else None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Seed the new permission (idempotent)
    perm_key, description = NEW_PERM
    if not _get_id(conn, "permissions", "key", perm_key):
        conn.exec_driver_sql(
            "INSERT INTO permissions (key, description, is_active) "
            "VALUES (%s, %s, true)",
            (perm_key, description),
        )

    # 2. Assign to roles
    perm_id = _get_id(conn, "permissions", "key", perm_key)
    if perm_id:
        for role_name in ROLES_WITH_PO_DELETE:
            role_id = _get_id(conn, "roles", "name", role_name)
            if not role_id:
                continue
            exists = conn.exec_driver_sql(
                "SELECT 1 FROM role_permissions "
                "WHERE role_id = %s AND permission_id = %s",
                (role_id, perm_id),
            ).fetchone()
            if not exists:
                conn.exec_driver_sql(
                    "INSERT INTO role_permissions (role_id, permission_id) "
                    "VALUES (%s, %s)",
                    (role_id, perm_id),
                )

    # 3. Revoke excess permissions from ap_clerk
    ap_clerk_id = _get_id(conn, "roles", "name", "ap_clerk")
    if ap_clerk_id:
        for excess_key in AP_CLERK_EXCESS:
            excess_perm_id = _get_id(conn, "permissions", "key", excess_key)
            if excess_perm_id:
                conn.exec_driver_sql(
                    "DELETE FROM role_permissions "
                    "WHERE role_id = %s AND permission_id = %s",
                    (ap_clerk_id, excess_perm_id),
                )


def downgrade() -> None:
    # Permission remains (harmless). Revoked grants are not restored
    # because the over-grant was the bug.
    pass
