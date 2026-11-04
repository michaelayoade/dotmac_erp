"""Separate budget reset authority from read-only expense review."""

import sqlalchemy as sa
from alembic import op

revision = "20261104_expense_reset_permission"
down_revision = "20261104_expense_reset_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("""
        INSERT INTO permissions (
            id, key, description, is_active, created_at, updated_at
        )
        VALUES (gen_random_uuid(), 'expense:limits:reset',
                'Manually restore an approver''s expense allowance',
                true, NOW(), NOW())
        ON CONFLICT (key) DO NOTHING
    """)
    )
    # Preserve the existing dedicated reviewer role's reset responsibility.
    # Custom review-only roles do not receive reset authority automatically.
    connection.execute(
        sa.text("""
        INSERT INTO role_permissions (id, role_id, permission_id)
        SELECT gen_random_uuid(), r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.name IN ('admin', 'expense_reviewer') AND r.is_active
          AND p.key = 'expense:limits:reset' AND p.is_active
        ON CONFLICT (role_id, permission_id) DO NOTHING
    """)
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE key = 'expense:limits:reset')"
        )
    )
    connection.execute(
        sa.text("DELETE FROM permissions WHERE key = 'expense:limits:reset'")
    )
