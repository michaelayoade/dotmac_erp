"""Add GT pension fund administrator to PFA directory.

Revision ID: 20260213_add_gt_pension_pfa
Revises: 20260130_add_source_bank_account
Create Date: 2026-02-13

"""

from alembic import op

revision = "20260213_add_gt_pension_pfa"
down_revision = "20260130_add_source_bank_account"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO core_org.pfa_directory (pfa_code, pfa_name, short_name, aliases, is_active)
        VALUES ('040', 'GUARANTY TRUST PENSION MANAGERS', 'GT', ARRAY['GT'], true)
        ON CONFLICT (pfa_code) DO UPDATE
        SET
            pfa_name = EXCLUDED.pfa_name,
            short_name = EXCLUDED.short_name,
            aliases = EXCLUDED.aliases,
            is_active = true;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM core_org.pfa_directory
        WHERE pfa_code = '040' AND pfa_name = 'GUARANTY TRUST PENSION MANAGERS';
        """
    )
