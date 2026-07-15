"""Add department-scoped discipline access role.

Revision ID: 20260715_department_discipline_access
Revises: 20260712_employee_sub_app_access
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op

revision = "20260715_department_discipline_access"
down_revision = "20260712_employee_sub_app_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO permissions (id, key, description, is_active, created_at, updated_at)
        VALUES (
            gen_random_uuid(),
            'discipline:department:read',
            'View disciplinary cases for own HR department',
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
        INSERT INTO roles (id, name, description, is_active, created_at, updated_at)
        VALUES (
            gen_random_uuid(),
            'customer_experience_discipline_viewer',
            'Department-scoped discipline case visibility for Customer Experience managers',
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
    op.execute(
        """
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id
        FROM roles r
        JOIN permissions p ON p.key = 'discipline:department:read'
        WHERE r.name = 'customer_experience_discipline_viewer'
        ON CONFLICT (role_id, permission_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO person_roles (id, person_id, role_id, assigned_at)
        SELECT gen_random_uuid(), people.id, roles.id, NOW()
        FROM people
        JOIN roles ON roles.name = 'customer_experience_discipline_viewer'
        WHERE lower(people.email) = 'i.aisha@dotmac.ng'
        ON CONFLICT (person_id, role_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM person_roles pr
        USING people, roles
        WHERE pr.person_id = people.id
          AND pr.role_id = roles.id
          AND lower(people.email) = 'i.aisha@dotmac.ng'
          AND roles.name = 'customer_experience_discipline_viewer'
        """
    )
    op.execute(
        """
        DELETE FROM role_permissions rp
        USING roles r, permissions p
        WHERE rp.role_id = r.id
          AND rp.permission_id = p.id
          AND r.name = 'customer_experience_discipline_viewer'
          AND p.key = 'discipline:department:read'
        """
    )
    op.execute("DELETE FROM roles WHERE name = 'customer_experience_discipline_viewer'")
    op.execute("DELETE FROM permissions WHERE key = 'discipline:department:read'")
