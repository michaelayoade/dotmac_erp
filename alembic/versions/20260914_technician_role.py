"""Provision the Employee-equivalent Technician role.

Revision ID: 20260914_technician_role
Revises: 20260911_mailcow_activation
Create Date: 2026-09-14
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from alembic import op

revision: str = "20260914_technician_role"
down_revision: str | tuple[str, ...] = "20260911_mailcow_activation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TECHNICIAN_DESCRIPTION = "Employee self-service without ERP expense UI access"


def _require_active_role(connection: Any, role_name: str) -> Any:
    row = connection.exec_driver_sql(
        "SELECT id, is_active FROM roles WHERE name = %s",
        (role_name,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"Required RBAC role is missing: {role_name}")
    if not row[1]:
        raise RuntimeError(f"RBAC role is inactive: {role_name}")
    return row[0]


def upgrade() -> None:
    connection = op.get_bind()
    employee_role_id = _require_active_role(connection, "employee")

    connection.exec_driver_sql(
        """
        INSERT INTO roles (id, name, description, is_active, created_at, updated_at)
        VALUES (gen_random_uuid(), %s, %s, TRUE, NOW(), NOW())
        ON CONFLICT (name) DO NOTHING
        """,
        ("technician", TECHNICIAN_DESCRIPTION),
    )
    technician_role_id = _require_active_role(connection, "technician")

    connection.exec_driver_sql(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), %s, employee_grant.permission_id
        FROM role_permissions AS employee_grant
        WHERE employee_grant.role_id = %s
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """,
        (technician_role_id, employee_role_id),
    )


def downgrade() -> None:
    # Role membership is live authorization data. Removing the role or its
    # grants on downgrade could revoke access that this migration did not assign.
    pass
