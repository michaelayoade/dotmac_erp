"""Add dotmac_sub staff-sync columns to hr.employee.

Revision ID: 20260710_add_employee_dotmac_sub_account
Revises: 20260706_add_expense_claim_crm_id
Create Date: 2026-07-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260710_add_employee_dotmac_sub_account"
down_revision = "20260706_add_expense_claim_crm_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("employee", schema="hr")}

    if "dotmac_sub_account_id" not in columns:
        op.add_column(
            "employee",
            sa.Column(
                "dotmac_sub_account_id",
                sa.String(36),
                nullable=True,
                comment="dotmac_sub SystemUser id provisioned by staff sync",
            ),
            schema="hr",
        )
    if "dotmac_sub_staff_synced_at" not in columns:
        op.add_column(
            "employee",
            sa.Column(
                "dotmac_sub_staff_synced_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="Last successful staff-sync push to dotmac_sub",
            ),
            schema="hr",
        )


def downgrade() -> None:
    op.drop_column("employee", "dotmac_sub_staff_synced_at", schema="hr")
    op.drop_column("employee", "dotmac_sub_account_id", schema="hr")
