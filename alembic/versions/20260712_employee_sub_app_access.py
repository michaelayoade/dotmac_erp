"""Add HR-managed dotmac_sub application access.

Revision ID: 20260712_employee_sub_app_access
Revises: 20260712_add_mono_last_ingest_at
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260712_employee_sub_app_access"
down_revision = "20260712_add_mono_last_ingest_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employee",
        sa.Column(
            "dotmac_sub_access_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment="Whether HR authorizes this employee to use dotmac_sub",
        ),
        schema="hr",
    )
    op.add_column(
        "employee",
        sa.Column(
            "dotmac_sub_roles",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[\"staff\"]'::jsonb"),
            comment="dotmac_sub role names managed by ERP HR",
        ),
        schema="hr",
    )
    op.execute(
        """
        UPDATE hr.employee
        SET dotmac_sub_access_enabled = true
        WHERE dotmac_sub_account_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_column("employee", "dotmac_sub_roles", schema="hr")
    op.drop_column("employee", "dotmac_sub_access_enabled", schema="hr")
