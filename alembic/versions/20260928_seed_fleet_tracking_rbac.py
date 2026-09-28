"""Seed Fleet tracking permissions without granting Driver access.

Revision ID: 20260928_seed_fleet_tracking_rbac
Revises: 20260928_add_vehicle_tracker
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_seed_fleet_tracking_rbac"
down_revision = "20260928_add_vehicle_tracker"
branch_labels = None
depends_on = None

TRACKING_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("fleet:tracking:read", "View fleet tracker mappings"),
    ("fleet:tracking:manage", "Manage fleet tracker mappings"),
    ("fleet:commands:send", "Send commands to fleet tracking devices"),
)

OPERATIONS_MANAGER_GRANTS: tuple[str, ...] = (
    "fleet:tracking:read",
    "fleet:tracking:manage",
)


def upgrade() -> None:
    for permission_key, description in TRACKING_PERMISSIONS:
        op.execute(
            sa.text(
                """
                INSERT INTO permissions (
                    id, key, description, is_active, created_at, updated_at
                )
                VALUES (
                    gen_random_uuid(), :permission_key, :description,
                    TRUE, NOW(), NOW()
                )
                ON CONFLICT (key) DO UPDATE
                SET description = EXCLUDED.description,
                    is_active = TRUE,
                    updated_at = NOW()
                """
            ).bindparams(
                permission_key=permission_key,
                description=description,
            )
        )

    for permission_key in OPERATIONS_MANAGER_GRANTS:
        op.execute(
            sa.text(
                """
                INSERT INTO role_permissions (id, role_id, permission_id)
                SELECT gen_random_uuid(), roles.id, permissions.id
                FROM roles
                JOIN permissions ON permissions.key = :permission_key
                WHERE roles.name = 'operations_manager'
                ON CONFLICT (role_id, permission_id) DO NOTHING
                """
            ).bindparams(permission_key=permission_key)
        )


def downgrade() -> None:
    # Permission and role membership are live authorization data. Removing
    # either on downgrade could revoke grants this migration did not create.
    pass
