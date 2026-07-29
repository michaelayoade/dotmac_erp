"""Add department-scoped discipline workflow permission.

Revision ID: 20260715_dept_discipline_workflow
Revises: 20260715_assign_ce_discipline_viewer
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op

revision = "20260715_dept_discipline_workflow"
down_revision = "20260715_assign_ce_discipline_viewer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (id, key, description, is_active, created_at, updated_at)
        VALUES (
            gen_random_uuid(),
            'discipline:department:workflow',
            'Manage disciplinary workflow acknowledgements for own HR department',
            TRUE,
            NOW(),
            NOW()
        )
        ON CONFLICT (key) DO UPDATE
        SET description = EXCLUDED.description,
            is_active = TRUE,
            updated_at = NOW()
        """
    )
    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM roles r
        JOIN permissions p ON p.key = 'discipline:department:workflow'
        WHERE r.name = 'customer_experience_discipline_viewer'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM role_permissions rp
        USING roles r, permissions p
        WHERE rp.role_id = r.id
          AND rp.permission_id = p.id
          AND r.name = 'customer_experience_discipline_viewer'
          AND p.key = 'discipline:department:workflow'
        """
    )
    op.execute("DELETE FROM permissions WHERE key = 'discipline:department:workflow'")
