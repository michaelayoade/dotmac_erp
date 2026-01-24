"""Add payments value to settingdomain enum and merge heads.

Revision ID: 20260124_add_settingdomain_payments
Revises: 20260124_notification, 9b2a7c1d4c9a
Create Date: 2026-01-24
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260124_add_settingdomain_payments"
down_revision = ("20260124_notification", "9b2a7c1d4c9a")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_enum
                WHERE enumlabel = 'payments'
                  AND enumtypid = 'settingdomain'::regtype
            ) THEN
                ALTER TYPE settingdomain ADD VALUE 'payments';
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Postgres does not support removing enum values safely.
    pass
