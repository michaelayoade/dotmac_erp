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
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def columns_exist(table_name: str, schema: str, columns: list[str]) -> bool:
        existing = {
            col["name"] for col in inspector.get_columns(table_name, schema=schema)
        }
        return all(col in existing for col in columns)

    def fk_exists(
        table_name: str,
        schema: str,
        constrained_columns: list[str],
        referred_table: str,
        referred_schema: str,
    ) -> bool:
        for fk in inspector.get_foreign_keys(table_name, schema=schema):
            if fk.get("constrained_columns") != constrained_columns:
                continue
            if fk.get("referred_table") != referred_table:
                continue
            if fk.get("referred_schema") != referred_schema:
                continue
            return True
        return False

    def index_exists(table_name: str, schema: str, columns: list[str]) -> bool:
        for idx in inspector.get_indexes(table_name, schema=schema):
            if idx.get("column_names") == columns:
                return True
        return False

    existing_columns = {
        col["name"] for col in inspector.get_columns("expense_entry", schema="exp")
    }

    # Add cost allocation columns only if they don't exist
    if "project_id" not in existing_columns:
        op.add_column(
            "expense_entry",
            sa.Column("project_id", UUID(as_uuid=True), nullable=True),
            schema="exp",
        )
    if "cost_center_id" not in existing_columns:
        op.add_column(
            "expense_entry",
            sa.Column("cost_center_id", UUID(as_uuid=True), nullable=True),
            schema="exp",
        )
    if "business_unit_id" not in existing_columns:
        op.add_column(
            "expense_entry",
            sa.Column("business_unit_id", UUID(as_uuid=True), nullable=True),
            schema="exp",
        )

    # Add foreign keys only if they don't exist
    if columns_exist("expense_entry", "exp", ["project_id"]) and not fk_exists(
        "expense_entry",
        "exp",
        ["project_id"],
        "project",
        "core_org",
    ):
        op.create_foreign_key(
            "fk_expense_project",
            "expense_entry",
            "project",
            ["project_id"],
            ["project_id"],
            source_schema="exp",
            referent_schema="core_org",
        )
    if columns_exist("expense_entry", "exp", ["cost_center_id"]) and not fk_exists(
        "expense_entry",
        "exp",
        ["cost_center_id"],
        "cost_center",
        "core_org",
    ):
        op.create_foreign_key(
            "fk_expense_cost_center",
            "expense_entry",
            "cost_center",
            ["cost_center_id"],
            ["cost_center_id"],
            source_schema="exp",
            referent_schema="core_org",
        )
    if columns_exist("expense_entry", "exp", ["business_unit_id"]) and not fk_exists(
        "expense_entry",
        "exp",
        ["business_unit_id"],
        "business_unit",
        "core_org",
    ):
        op.create_foreign_key(
            "fk_expense_business_unit",
            "expense_entry",
            "business_unit",
            ["business_unit_id"],
            ["business_unit_id"],
            source_schema="exp",
            referent_schema="core_org",
        )

    # Add indexes only if they don't exist
    if columns_exist("expense_entry", "exp", ["project_id"]) and not index_exists(
        "expense_entry",
        "exp",
        ["project_id"],
    ):
        op.create_index(
            "idx_expense_entry_project",
            "expense_entry",
            ["project_id"],
            schema="exp",
        )
    if columns_exist("expense_entry", "exp", ["cost_center_id"]) and not index_exists(
        "expense_entry",
        "exp",
        ["cost_center_id"],
    ):
        op.create_index(
            "idx_expense_entry_cost_center",
            "expense_entry",
            ["cost_center_id"],
            schema="exp",
        )


def downgrade() -> None:
    # Drop indexes
    op.drop_index(
        "idx_expense_entry_cost_center", table_name="expense_entry", schema="exp"
    )
    op.drop_index("idx_expense_entry_project", table_name="expense_entry", schema="exp")

    # Drop foreign keys
    op.drop_constraint(
        "fk_expense_business_unit", "expense_entry", schema="exp", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_expense_cost_center", "expense_entry", schema="exp", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_expense_project", "expense_entry", schema="exp", type_="foreignkey"
    )

    # Drop columns
    op.drop_column("expense_entry", "business_unit_id", schema="exp")
    op.drop_column("expense_entry", "cost_center_id", schema="exp")
    op.drop_column("expense_entry", "project_id", schema="exp")
