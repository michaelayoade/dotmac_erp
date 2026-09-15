"""Add employee workforce provisioning monitor state.

Revision ID: 20260915_workforce_monitor
Revises: 20260914_technician_role
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260915_workforce_monitor"
down_revision = "20260914_technician_role"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employee",
        sa.Column("workforce_provisioning_status", sa.String(32), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee",
        sa.Column(
            "workforce_provisioning_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        schema="hr",
    )
    op.create_index(
        "ix_hr_employee_workforce_provisioning_status",
        "employee",
        ["workforce_provisioning_status"],
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hr_employee_workforce_provisioning_status",
        table_name="employee",
        schema="hr",
    )
    op.drop_column("employee", "workforce_provisioning_state", schema="hr")
    op.drop_column("employee", "workforce_provisioning_status", schema="hr")
