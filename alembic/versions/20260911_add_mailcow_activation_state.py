"""Add ERP-owned Mailcow provisioning and activation state.

Revision ID: 20260911_mailcow_activation
Revises: 20260910_sub_expense_destination
Create Date: 2026-09-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260911_mailcow_activation"
down_revision = "20260910_sub_expense_destination"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "employee",
        sa.Column("mailcow_mailbox_provisioned_at", sa.DateTime(timezone=True)),
        schema="hr",
    )
    op.add_column(
        "employee",
        sa.Column("mailcow_provisioning_requested_at", sa.DateTime(timezone=True)),
        schema="hr",
    )
    op.add_column(
        "employee",
        sa.Column("mailcow_activation_token_hash", sa.String(length=64)),
        schema="hr",
    )
    op.add_column(
        "employee",
        sa.Column("mailcow_activation_expires_at", sa.DateTime(timezone=True)),
        schema="hr",
    )
    op.add_column(
        "employee",
        sa.Column("mailcow_activation_sent_at", sa.DateTime(timezone=True)),
        schema="hr",
    )
    op.add_column(
        "employee",
        sa.Column("mailcow_activated_at", sa.DateTime(timezone=True)),
        schema="hr",
    )
    op.create_index(
        "ix_hr_employee_mailcow_activation_token_hash",
        "employee",
        ["mailcow_activation_token_hash"],
        unique=True,
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_hr_employee_mailcow_activation_token_hash",
        table_name="employee",
        schema="hr",
    )
    op.drop_column("employee", "mailcow_activated_at", schema="hr")
    op.drop_column("employee", "mailcow_activation_sent_at", schema="hr")
    op.drop_column("employee", "mailcow_activation_expires_at", schema="hr")
    op.drop_column("employee", "mailcow_activation_token_hash", schema="hr")
    op.drop_column("employee", "mailcow_provisioning_requested_at", schema="hr")
    op.drop_column("employee", "mailcow_mailbox_provisioned_at", schema="hr")
