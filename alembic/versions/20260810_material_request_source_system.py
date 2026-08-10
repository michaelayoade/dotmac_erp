"""Track material request source authority for Sub cutover.

Revision ID: 20260810_material_source
Revises: 20260802_add_outbox_claim_lease_columns
"""

from alembic import op
import sqlalchemy as sa

revision = "20260810_material_source"
down_revision = "20260802_add_outbox_claim_lease_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "material_request",
        sa.Column(
            "source_system",
            sa.String(length=20),
            nullable=False,
            server_default="crm",
        ),
        schema="inv",
    )
    op.create_check_constraint(
        "ck_material_request_source_system",
        "material_request",
        "source_system IN ('crm', 'sub', 'erp')",
        schema="inv",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_material_request_source_system",
        "material_request",
        schema="inv",
        type_="check",
    )
    op.drop_column("material_request", "source_system", schema="inv")
