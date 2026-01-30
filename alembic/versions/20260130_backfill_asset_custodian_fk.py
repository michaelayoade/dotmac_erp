"""Backfill asset custodian employee IDs and validate FK.

Revision ID: 20260130_backfill_asset_custodian_fk
Revises: 20260124_task_asset_fks
Create Date: 2026-01-30
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260130_backfill_asset_custodian_fk"
down_revision = "20260124_task_asset_fks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Map legacy custodian person IDs to employee IDs where possible.
    op.execute(
        """
        UPDATE fa.asset AS a
        SET custodian_employee_id = e.employee_id
        FROM hr.employee AS e
        WHERE a.custodian_employee_id = e.person_id
          AND a.organization_id = e.organization_id
        """
    )

    # Validate FK only if it exists (added as NOT VALID in prior migration).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'fk_asset_custodian_employee'
            ) THEN
                ALTER TABLE fa.asset VALIDATE CONSTRAINT fk_asset_custodian_employee;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # No safe downgrade: validation cannot be reversed without dropping the constraint.
    pass
