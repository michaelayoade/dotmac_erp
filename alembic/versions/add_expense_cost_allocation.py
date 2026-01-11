"""Add cost allocation fields to expense.

Revision ID: add_expense_cost_allocation
Revises: add_expense_schema
Create Date: 2025-02-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "add_expense_cost_allocation"
down_revision = "add_expense_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add cost allocation columns
    op.add_column(
        "expense_entry",
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        schema="exp",
    )
    op.add_column(
        "expense_entry",
        sa.Column("cost_center_id", UUID(as_uuid=True), nullable=True),
        schema="exp",
    )
    op.add_column(
        "expense_entry",
        sa.Column("business_unit_id", UUID(as_uuid=True), nullable=True),
        schema="exp",
    )

    # Add foreign keys
    op.create_foreign_key(
        "fk_expense_project",
        "expense_entry",
        "project",
        ["project_id"],
        ["project_id"],
        source_schema="exp",
        referent_schema="core_org",
    )
    op.create_foreign_key(
        "fk_expense_cost_center",
        "expense_entry",
        "cost_center",
        ["cost_center_id"],
        ["cost_center_id"],
        source_schema="exp",
        referent_schema="core_org",
    )
    op.create_foreign_key(
        "fk_expense_business_unit",
        "expense_entry",
        "business_unit",
        ["business_unit_id"],
        ["business_unit_id"],
        source_schema="exp",
        referent_schema="core_org",
    )

    # Add indexes
    op.create_index(
        "idx_expense_entry_project",
        "expense_entry",
        ["project_id"],
        schema="exp",
    )
    op.create_index(
        "idx_expense_entry_cost_center",
        "expense_entry",
        ["cost_center_id"],
        schema="exp",
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("idx_expense_entry_cost_center", table_name="expense_entry", schema="exp")
    op.drop_index("idx_expense_entry_project", table_name="expense_entry", schema="exp")

    # Drop foreign keys
    op.drop_constraint("fk_expense_business_unit", "expense_entry", schema="exp", type_="foreignkey")
    op.drop_constraint("fk_expense_cost_center", "expense_entry", schema="exp", type_="foreignkey")
    op.drop_constraint("fk_expense_project", "expense_entry", schema="exp", type_="foreignkey")

    # Drop columns
    op.drop_column("expense_entry", "business_unit_id", schema="exp")
    op.drop_column("expense_entry", "cost_center_id", schema="exp")
    op.drop_column("expense_entry", "project_id", schema="exp")
