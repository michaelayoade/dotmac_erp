"""Add fiscal position tables for tax/account remapping.

Revision ID: 20260224_add_fiscal_position
Revises: 20260224_add_settingdomain_banking, 20260224_add_weekly_approver_budget_and_resets
Create Date: 2026-02-24
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260224_add_fiscal_position"
down_revision: Union[str, tuple[str, ...], None] = (
    "20260224_add_settingdomain_banking",
    "20260224_add_weekly_approver_budget_and_resets",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    # Ensure tax schema exists (should already, but be safe)
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS tax"))

    # 1. fiscal_position
    if not inspector.has_table("fiscal_position", schema="tax"):
        op.create_table(
            "fiscal_position",
            sa.Column(
                "fiscal_position_id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "auto_apply", sa.Boolean(), nullable=False, server_default="false"
            ),
            sa.Column("country_code", sa.String(3), nullable=True),
            sa.Column("state_code", sa.String(10), nullable=True),
            sa.Column("customer_type", sa.String(50), nullable=True),
            sa.Column("supplier_type", sa.String(50), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="10"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("fiscal_position_id"),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
            ),
            sa.UniqueConstraint(
                "organization_id", "name", name="uq_fiscal_position_name"
            ),
            schema="tax",
        )
        op.create_index(
            "ix_fiscal_position_organization_id",
            "fiscal_position",
            ["organization_id"],
            schema="tax",
        )

    # 2. fiscal_position_tax_map
    if not inspector.has_table("fiscal_position_tax_map", schema="tax"):
        op.create_table(
            "fiscal_position_tax_map",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "fiscal_position_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column(
                "tax_source_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column(
                "tax_dest_id",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["fiscal_position_id"],
                ["tax.fiscal_position.fiscal_position_id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["tax_source_id"],
                ["tax.tax_code.tax_code_id"],
            ),
            sa.ForeignKeyConstraint(
                ["tax_dest_id"],
                ["tax.tax_code.tax_code_id"],
            ),
            schema="tax",
        )
        op.create_index(
            "ix_fp_tax_map_fiscal_position_id",
            "fiscal_position_tax_map",
            ["fiscal_position_id"],
            schema="tax",
        )

    # 3. fiscal_position_account_map
    if not inspector.has_table("fiscal_position_account_map", schema="tax"):
        op.create_table(
            "fiscal_position_account_map",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "fiscal_position_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column(
                "account_source_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.Column(
                "account_dest_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.ForeignKeyConstraint(
                ["fiscal_position_id"],
                ["tax.fiscal_position.fiscal_position_id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["account_source_id"],
                ["gl.account.account_id"],
            ),
            sa.ForeignKeyConstraint(
                ["account_dest_id"],
                ["gl.account.account_id"],
            ),
            schema="tax",
        )
        op.create_index(
            "ix_fp_account_map_fiscal_position_id",
            "fiscal_position_account_map",
            ["fiscal_position_id"],
            schema="tax",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tax_tables = inspector.get_table_names(schema="tax")

    if "fiscal_position_account_map" in tax_tables:
        op.drop_table("fiscal_position_account_map", schema="tax")
    if "fiscal_position_tax_map" in tax_tables:
        op.drop_table("fiscal_position_tax_map", schema="tax")
    if "fiscal_position" in tax_tables:
        op.drop_table("fiscal_position", schema="tax")
