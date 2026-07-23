"""Ensure the restricted Fleet Driver role and grants exist.

Revision ID: 20260723_driver_fleet_rbac
Revises: 20260722_info_change_batches
Create Date: 2026-07-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_driver_fleet_rbac"
down_revision: str | tuple[str, ...] = "20260722_info_change_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DRIVER_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ("fleet:access", "Access fleet module"),
    ("fleet:dashboard", "View fleet dashboard"),
    ("fleet:fuel:read", "View fleet fuel logs"),
    ("fleet:maintenance:read", "View fleet maintenance records"),
    ("fleet:incidents:read", "View fleet incidents"),
)


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO roles (id, name, description, is_active, created_at, updated_at)
        VALUES (
            gen_random_uuid(),
            'driver',
            'Driver with read-only access to fleet fuel, maintenance, and incidents',
            TRUE,
            NOW(),
            NOW()
        )
        ON CONFLICT (name) DO UPDATE
        SET description = EXCLUDED.description,
            is_active = TRUE,
            updated_at = NOW()
        """
    )

    for permission_key, description in DRIVER_PERMISSIONS:
        op.execute(
            sa.text(
                """
                WITH ensured_permission AS (
                    INSERT INTO permissions (
                        id,
                        key,
                        description,
                        is_active,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        gen_random_uuid(),
                        :permission_key,
                        :description,
                        TRUE,
                        NOW(),
                        NOW()
                    )
                    ON CONFLICT (key) DO UPDATE
                    SET description = EXCLUDED.description,
                        is_active = TRUE,
                        updated_at = NOW()
                    RETURNING id
                )
                INSERT INTO role_permissions (id, role_id, permission_id)
                SELECT gen_random_uuid(), roles.id, ensured_permission.id
                FROM roles
                CROSS JOIN ensured_permission
                WHERE roles.name = 'driver'
                ON CONFLICT (role_id, permission_id) DO NOTHING
                """
            ).bindparams(
                permission_key=permission_key,
                description=description,
            )
        )


def downgrade() -> None:
    # The role and permissions may have existed before this idempotent migration,
    # and role membership is live authorization data. Removing either on downgrade
    # could revoke access that this migration did not create.
    pass
