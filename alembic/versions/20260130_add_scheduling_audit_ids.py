"""Add audit user id columns to scheduling tables.

Revision ID: 20260130_add_scheduling_audit_ids
Revises: 20260130_fix_remaining
Create Date: 2026-01-30

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260130_add_scheduling_audit_ids"
down_revision = "20260130_fix_remaining"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # shift_pattern: add created_by_id/updated_by_id
    op.add_column(
        "shift_pattern",
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="scheduling",
    )
    op.add_column(
        "shift_pattern",
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="scheduling",
    )
    op.create_foreign_key(
        "fk_shift_pattern_created_by_id",
        "shift_pattern",
        "people",
        ["created_by_id"],
        ["id"],
        source_schema="scheduling",
    )
    op.create_foreign_key(
        "fk_shift_pattern_updated_by_id",
        "shift_pattern",
        "people",
        ["updated_by_id"],
        ["id"],
        source_schema="scheduling",
    )

    # shift_pattern_assignment: add created_by_id/updated_by_id
    op.add_column(
        "shift_pattern_assignment",
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="scheduling",
    )
    op.add_column(
        "shift_pattern_assignment",
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="scheduling",
    )
    op.create_foreign_key(
        "fk_shift_pattern_assignment_created_by_id",
        "shift_pattern_assignment",
        "people",
        ["created_by_id"],
        ["id"],
        source_schema="scheduling",
    )
    op.create_foreign_key(
        "fk_shift_pattern_assignment_updated_by_id",
        "shift_pattern_assignment",
        "people",
        ["updated_by_id"],
        ["id"],
        source_schema="scheduling",
    )

    # shift_schedule: add updated_by_id
    op.add_column(
        "shift_schedule",
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="scheduling",
    )
    op.create_foreign_key(
        "fk_shift_schedule_updated_by_id",
        "shift_schedule",
        "people",
        ["updated_by_id"],
        ["id"],
        source_schema="scheduling",
    )

    # shift_swap_request: add created_by_id/updated_by_id
    op.add_column(
        "shift_swap_request",
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="scheduling",
    )
    op.add_column(
        "shift_swap_request",
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="scheduling",
    )
    op.create_foreign_key(
        "fk_shift_swap_request_created_by_id",
        "shift_swap_request",
        "people",
        ["created_by_id"],
        ["id"],
        source_schema="scheduling",
    )
    op.create_foreign_key(
        "fk_shift_swap_request_updated_by_id",
        "shift_swap_request",
        "people",
        ["updated_by_id"],
        ["id"],
        source_schema="scheduling",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_shift_swap_request_updated_by_id",
        "shift_swap_request",
        schema="scheduling",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_shift_swap_request_created_by_id",
        "shift_swap_request",
        schema="scheduling",
        type_="foreignkey",
    )
    op.drop_column("shift_swap_request", "updated_by_id", schema="scheduling")
    op.drop_column("shift_swap_request", "created_by_id", schema="scheduling")

    op.drop_constraint(
        "fk_shift_schedule_updated_by_id",
        "shift_schedule",
        schema="scheduling",
        type_="foreignkey",
    )
    op.drop_column("shift_schedule", "updated_by_id", schema="scheduling")

    op.drop_constraint(
        "fk_shift_pattern_assignment_updated_by_id",
        "shift_pattern_assignment",
        schema="scheduling",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_shift_pattern_assignment_created_by_id",
        "shift_pattern_assignment",
        schema="scheduling",
        type_="foreignkey",
    )
    op.drop_column("shift_pattern_assignment", "updated_by_id", schema="scheduling")
    op.drop_column("shift_pattern_assignment", "created_by_id", schema="scheduling")

    op.drop_constraint(
        "fk_shift_pattern_updated_by_id",
        "shift_pattern",
        schema="scheduling",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_shift_pattern_created_by_id",
        "shift_pattern",
        schema="scheduling",
        type_="foreignkey",
    )
    op.drop_column("shift_pattern", "updated_by_id", schema="scheduling")
    op.drop_column("shift_pattern", "created_by_id", schema="scheduling")
