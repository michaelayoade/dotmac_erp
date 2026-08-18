"""add dotmac_sub customer metrics snapshot

Revision ID: 20260818_dotmac_sub_customer_metrics
Revises: 20260814_database_roles
Create Date: 2026-08-18 15:45:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260818_dotmac_sub_customer_metrics"
down_revision = "20260814_database_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "customer",
        sa.Column(
            "dotmac_sub_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Latest dotmac_sub commercial/customer lifecycle metrics snapshot",
        ),
        schema="ar",
    )


def downgrade() -> None:
    op.drop_column("customer", "dotmac_sub_metrics", schema="ar")
