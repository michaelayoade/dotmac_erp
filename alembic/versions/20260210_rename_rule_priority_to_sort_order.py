"""Rename transaction_rule.priority to sort_order.

Replaces the confusing numeric priority field (higher=first? lower=first?)
with a clear sort_order column (0=evaluated first, sequential).

Existing priority values are normalized into sequential 0-based positions
per organization, preserving the original evaluation order (priority DESC).

Revision ID: 20260210_rename_rule_priority_to_sort_order
Revises: 20260210_add_crm_sync_columns
Create Date: 2026-02-10
"""

import sqlalchemy as sa

from alembic import op

revision = "20260210_rename_rule_priority_to_sort_order"
down_revision = "20260210_add_crm_sync_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Idempotent: only rename if 'priority' column exists
    has_priority = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'banking' "
            "AND table_name = 'transaction_rule' "
            "AND column_name = 'priority'"
        )
    ).scalar()

    if not has_priority:
        return

    # Rename column
    op.alter_column(
        "transaction_rule",
        "priority",
        new_column_name="sort_order",
        schema="banking",
    )

    # Update column comment
    op.alter_column(
        "transaction_rule",
        "sort_order",
        comment="Execution order (0 = evaluated first, auto-managed)",
        schema="banking",
    )

    # Normalize: convert existing priority values (DESC order) into
    # sequential 0-based sort_order per organization.
    # Rules that had higher priority values (evaluated first under the old
    # DESC ordering) get lower sort_order values (evaluated first under ASC).
    conn.execute(
        sa.text("""
            WITH ranked AS (
                SELECT rule_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY organization_id
                           ORDER BY sort_order DESC, rule_name
                       ) - 1 AS new_order
                FROM banking.transaction_rule
            )
            UPDATE banking.transaction_rule t
            SET sort_order = r.new_order
            FROM ranked r
            WHERE t.rule_id = r.rule_id
        """)
    )

    # Set default to 0 for new rules
    op.alter_column(
        "transaction_rule",
        "sort_order",
        server_default="0",
        schema="banking",
    )


def downgrade() -> None:
    conn = op.get_bind()

    has_sort_order = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'banking' "
            "AND table_name = 'transaction_rule' "
            "AND column_name = 'sort_order'"
        )
    ).scalar()

    if not has_sort_order:
        return

    # Reverse normalize: convert sort_order ASC back to priority DESC-style
    # values (multiply by 10 for spacing, invert order)
    conn.execute(
        sa.text("""
            WITH ranked AS (
                SELECT rule_id,
                       (ROW_NUMBER() OVER (
                           PARTITION BY organization_id
                           ORDER BY sort_order DESC
                       )) * 10 AS new_priority
                FROM banking.transaction_rule
            )
            UPDATE banking.transaction_rule t
            SET sort_order = r.new_priority
            FROM ranked r
            WHERE t.rule_id = r.rule_id
        """)
    )

    op.alter_column(
        "transaction_rule",
        "sort_order",
        new_column_name="priority",
        server_default=None,
        comment="Higher value = checked first",
        schema="banking",
    )
