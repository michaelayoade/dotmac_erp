"""Extend employee info change requests for extended-profile self-service.

Revision ID: 20260721_extended_info_changes
Revises: 20260720_federated_identity
Create Date: 2026-07-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.alembic_utils import ensure_enum

revision: str = "20260721_extended_info_changes"
down_revision: str | tuple[str, ...] = "20260720_federated_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind,
        "info_change_operation",
        "CREATE",
        "UPDATE",
        schema="hr",
    )
    op.execute("ALTER TYPE hr.info_change_type ADD VALUE IF NOT EXISTS 'QUALIFICATION'")
    op.execute("ALTER TYPE hr.info_change_type ADD VALUE IF NOT EXISTS 'CERTIFICATION'")
    op.execute("ALTER TYPE hr.info_change_type ADD VALUE IF NOT EXISTS 'SKILL'")
    op.execute("ALTER TYPE hr.info_change_type ADD VALUE IF NOT EXISTS 'DEPENDENT'")
    op.execute("ALTER TYPE hr.info_change_type ADD VALUE IF NOT EXISTS 'DOCUMENT'")

    op.add_column(
        "employee_info_change_request",
        sa.Column(
            "operation",
            sa.Enum(
                "CREATE",
                "UPDATE",
                name="info_change_operation",
                schema="hr",
            ),
            nullable=False,
            server_default="UPDATE",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("target_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("pending_document_path", sa.Text(), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("pending_document_name", sa.Text(), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("pending_document_size", sa.Integer(), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("pending_document_mime_type", sa.Text(), nullable=True),
        schema="hr",
    )
    op.add_column(
        "employee_info_change_request",
        sa.Column("pending_document_checksum", sa.Text(), nullable=True),
        schema="hr",
    )
    op.create_index(
        "idx_info_change_request_section_pending",
        "employee_info_change_request",
        ["organization_id", "employee_id", "change_type", "status"],
        unique=False,
        schema="hr",
    )
    op.alter_column(
        "employee_info_change_request",
        "operation",
        server_default=None,
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_info_change_request_section_pending",
        table_name="employee_info_change_request",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "pending_document_checksum",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "pending_document_mime_type",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "pending_document_size",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "pending_document_name",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "pending_document_path",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "target_record_id",
        schema="hr",
    )
    op.drop_column(
        "employee_info_change_request",
        "operation",
        schema="hr",
    )
