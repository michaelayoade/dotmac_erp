"""Add department head tracking.

Revision ID: 20260124_department_head
Revises: 20260124_add_hr_lifecycle_erpnext_fields
Create Date: 2026-01-24
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "20260124_department_head"
down_revision = "20260124_add_hr_lifecycle_erpnext_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add head_id column to department table
    op.add_column(
        "department",
        sa.Column(
            "head_id",
            UUID(as_uuid=True),
            nullable=True,
            comment="Employee who heads this department",
        ),
        schema="hr",
    )

    # Create foreign key constraint
    op.create_foreign_key(
        "fk_department_head",
        "department",
        "employee",
        ["head_id"],
        ["employee_id"],
        source_schema="hr",
        referent_schema="hr",
    )

    # Create index for efficient lookups
    op.create_index(
        "idx_department_head",
        "department",
        ["head_id"],
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index("idx_department_head", table_name="department", schema="hr")
    op.drop_constraint(
        "fk_department_head", "department", type_="foreignkey", schema="hr"
    )
    op.drop_column("department", "head_id", schema="hr")
