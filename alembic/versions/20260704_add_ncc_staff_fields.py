"""Add NCC staff fields: employee.nationality + designation.ncc_staff_category.

Both are additive, nullable columns for the NCC year-end return's Section G
head-count matrix (Nigerian vs Expatriate; Managerial/Senior-Technical/
Junior-Technical/Other). Backward-compatible — existing rows read NULL.

Revision ID: 20260704_add_ncc_staff_fields
Revises: 20260628_server_health_category
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260704_add_ncc_staff_fields"
down_revision = "20260628_server_health_category"
branch_labels = None
depends_on = None


def _has_column(inspector, schema: str, table: str, column: str) -> bool:
    if not inspector.has_table(table, schema=schema):
        return False
    return any(c["name"] == column for c in inspector.get_columns(table, schema=schema))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "hr", "employee", "nationality"):
        op.add_column(
            "employee",
            sa.Column("nationality", sa.String(100), nullable=True),
            schema="hr",
        )

    ncc_enum = postgresql.ENUM(
        "MANAGERIAL",
        "SENIOR_TECHNICAL",
        "JUNIOR_TECHNICAL",
        "OTHER",
        name="hr_ncc_staff_category",
        create_type=False,
    )
    ncc_enum.create(bind, checkfirst=True)
    if not _has_column(inspector, "hr", "designation", "ncc_staff_category"):
        op.add_column(
            "designation",
            sa.Column("ncc_staff_category", ncc_enum, nullable=True),
            schema="hr",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "hr", "designation", "ncc_staff_category"):
        op.drop_column("designation", "ncc_staff_category", schema="hr")
    if _has_column(inspector, "hr", "employee", "nationality"):
        op.drop_column("employee", "nationality", schema="hr")
    postgresql.ENUM(name="hr_ncc_staff_category").drop(bind, checkfirst=True)
