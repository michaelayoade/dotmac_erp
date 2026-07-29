"""Add info change batches and document checksum support.

Revision ID: 20260722_info_change_batches
Revises: 20260722_sla_policy_documents
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_info_change_batches"
down_revision: str | tuple[str, ...] = "20260722_sla_policy_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    info_change_type_enum = postgresql.ENUM(
        "BANK_DETAILS",
        "TAX_INFO",
        "PENSION_INFO",
        "NHF_INFO",
        "COMBINED",
        "QUALIFICATION",
        "CERTIFICATION",
        "SKILL",
        "DEPENDENT",
        "DOCUMENT",
        name="info_change_type",
        schema="hr",
        create_type=False,
    )

    if not inspector.has_table("employee_info_change_batch", schema="hr"):
        op.create_table(
            "employee_info_change_batch",
            sa.Column(
                "batch_id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "organization_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey(
                    "core_org.organization.organization_id",
                    ondelete="CASCADE",
                ),
                nullable=False,
            ),
            sa.Column(
                "employee_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("hr.employee.employee_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "change_type",
                info_change_type_enum,
                nullable=False,
            ),
            sa.Column("requester_notes", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            schema="hr",
        )

    batch_indexes = {
        idx["name"]
        for idx in inspector.get_indexes("employee_info_change_batch", schema="hr")
        if idx.get("name")
    }
    if "idx_info_change_batch_org" not in batch_indexes:
        op.create_index(
            "idx_info_change_batch_org",
            "employee_info_change_batch",
            ["organization_id"],
            unique=False,
            schema="hr",
        )
    if "idx_info_change_batch_employee_type" not in batch_indexes:
        op.create_index(
            "idx_info_change_batch_employee_type",
            "employee_info_change_batch",
            ["organization_id", "employee_id", "change_type", "created_at"],
            unique=False,
            schema="hr",
        )

    request_columns = {
        column["name"]
        for column in inspector.get_columns(
            "employee_info_change_request",
            schema="hr",
        )
    }
    if "batch_id" not in request_columns:
        op.add_column(
            "employee_info_change_request",
            sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
            schema="hr",
        )
    if "batch_item_order" not in request_columns:
        op.add_column(
            "employee_info_change_request",
            sa.Column("batch_item_order", sa.Integer(), nullable=True),
            schema="hr",
        )

    request_fks = {
        fk["name"]
        for fk in inspector.get_foreign_keys(
            "employee_info_change_request",
            schema="hr",
        )
        if fk.get("name")
    }
    if "fk_info_change_request_batch_id" not in request_fks:
        op.create_foreign_key(
            "fk_info_change_request_batch_id",
            "employee_info_change_request",
            "employee_info_change_batch",
            ["batch_id"],
            ["batch_id"],
            source_schema="hr",
            referent_schema="hr",
            ondelete="SET NULL",
        )

    request_indexes = {
        idx["name"]
        for idx in inspector.get_indexes(
            "employee_info_change_request",
            schema="hr",
        )
        if idx.get("name")
    }
    if "idx_info_change_request_batch_order" not in request_indexes:
        op.create_index(
            "idx_info_change_request_batch_order",
            "employee_info_change_request",
            ["batch_id", "batch_item_order"],
            unique=False,
            schema="hr",
        )

    document_columns = {
        column["name"]
        for column in inspector.get_columns("employee_document", schema="hr")
    }
    if "content_checksum" not in document_columns:
        op.add_column(
            "employee_document",
            sa.Column("content_checksum", sa.String(length=64), nullable=True),
            schema="hr",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    document_columns = {
        column["name"]
        for column in inspector.get_columns("employee_document", schema="hr")
    }
    if "content_checksum" in document_columns:
        op.drop_column("employee_document", "content_checksum", schema="hr")

    request_indexes = {
        idx["name"]
        for idx in inspector.get_indexes(
            "employee_info_change_request",
            schema="hr",
        )
        if idx.get("name")
    }
    if "idx_info_change_request_batch_order" in request_indexes:
        op.drop_index(
            "idx_info_change_request_batch_order",
            table_name="employee_info_change_request",
            schema="hr",
        )

    request_fks = {
        fk["name"]
        for fk in inspector.get_foreign_keys(
            "employee_info_change_request",
            schema="hr",
        )
        if fk.get("name")
    }
    if "fk_info_change_request_batch_id" in request_fks:
        op.drop_constraint(
            "fk_info_change_request_batch_id",
            "employee_info_change_request",
            schema="hr",
            type_="foreignkey",
        )

    request_columns = {
        column["name"]
        for column in inspector.get_columns(
            "employee_info_change_request",
            schema="hr",
        )
    }
    if "batch_item_order" in request_columns:
        op.drop_column(
            "employee_info_change_request",
            "batch_item_order",
            schema="hr",
        )
    if "batch_id" in request_columns:
        op.drop_column(
            "employee_info_change_request",
            "batch_id",
            schema="hr",
        )

    if inspector.has_table("employee_info_change_batch", schema="hr"):
        batch_indexes = {
            idx["name"]
            for idx in inspector.get_indexes("employee_info_change_batch", schema="hr")
            if idx.get("name")
        }
        if "idx_info_change_batch_employee_type" in batch_indexes:
            op.drop_index(
                "idx_info_change_batch_employee_type",
                table_name="employee_info_change_batch",
                schema="hr",
            )
        if "idx_info_change_batch_org" in batch_indexes:
            op.drop_index(
                "idx_info_change_batch_org",
                table_name="employee_info_change_batch",
                schema="hr",
            )
        op.drop_table("employee_info_change_batch", schema="hr")
