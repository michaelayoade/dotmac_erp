"""Add operational-context links to finance expense entries.

Revision ID: 20260814_sub_operational_sync_v2
Revises: 20260808_open_setting_domain, 20260810_material_source
Create Date: 2026-08-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260814_sub_operational_sync_v2"
down_revision: str | tuple[str, ...] = (
    "20260808_open_setting_domain",
    "20260810_material_source",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "expense_entry",
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="exp",
    )
    op.add_column(
        "expense_entry",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="exp",
    )
    op.create_foreign_key(
        "fk_expense_entry_ticket",
        "expense_entry",
        "ticket",
        ["ticket_id"],
        ["ticket_id"],
        source_schema="exp",
        referent_schema="support",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_expense_entry_task",
        "expense_entry",
        "task",
        ["task_id"],
        ["task_id"],
        source_schema="exp",
        referent_schema="pm",
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_expense_entry_ticket",
        "expense_entry",
        ["ticket_id"],
        schema="exp",
    )
    op.create_index(
        "idx_expense_entry_task",
        "expense_entry",
        ["task_id"],
        schema="exp",
    )


def downgrade() -> None:
    op.drop_index("idx_expense_entry_task", table_name="expense_entry", schema="exp")
    op.drop_index("idx_expense_entry_ticket", table_name="expense_entry", schema="exp")
    op.drop_constraint(
        "fk_expense_entry_task", "expense_entry", schema="exp", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_expense_entry_ticket",
        "expense_entry",
        schema="exp",
        type_="foreignkey",
    )
    op.drop_column("expense_entry", "task_id", schema="exp")
    op.drop_column("expense_entry", "ticket_id", schema="exp")
