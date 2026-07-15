"""Assign Customer Experience discipline viewer with RLS context.

Revision ID: 20260715_assign_ce_discipline_viewer
Revises: 20260715_department_discipline_access
Create Date: 2026-07-15
"""

from __future__ import annotations

from alembic import op

revision = "20260715_assign_ce_discipline_viewer"
down_revision = "20260715_department_discipline_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "SELECT set_config('app.current_organization_id', "
        "'00000000-0000-0000-0000-000000000001', false)"
    )
    op.execute("SELECT set_config('app.bypass_rls', 'true', false)")
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
        "SELECT set_config('app.current_organization_id', "
        "'00000000-0000-0000-0000-000000000001', false)"
    )
    op.execute("SELECT set_config('app.bypass_rls', 'true', false)")
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
