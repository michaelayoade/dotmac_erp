"""Track partial material issues and line-level stock exceptions."""

from alembic import op
import sqlalchemy as sa

revision = "20260922_mr_partial_issue"
down_revision = "20260921_sub_expense_approval_v4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE inv.material_request_status ADD VALUE IF NOT EXISTS 'PARTIALLY_ISSUED'"
    )
    op.add_column(
        "material_request_item",
        sa.Column(
            "out_of_stock",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        schema="inv",
    )


def downgrade() -> None:
    op.drop_column("material_request_item", "out_of_stock", schema="inv")
    # PostgreSQL enum values cannot be removed safely without rebuilding the type.
