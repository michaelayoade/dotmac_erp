"""Add version columns for optimistic locking.

Revision ID: add_version_columns
Revises: add_saga_execution_tables
Create Date: 2026-01-19

This migration adds version columns to key tables for optimistic locking:
- ap.supplier_invoice
- ar.invoice
- gl.journal_entry
- audit.approval_request
"""
from alembic import op
import sqlalchemy as sa


revision = "add_version_columns"
down_revision = "add_saga_execution_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_column(schema: str, table: str, column: str) -> bool:
        if not inspector.has_table(table, schema=schema):
            return False
        return any(col["name"] == column for col in inspector.get_columns(table, schema=schema))

    # Add version column to ap.supplier_invoice
    if not has_column("ap", "supplier_invoice", "version"):
        op.add_column(
            "supplier_invoice",
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
                comment="Optimistic locking version",
            ),
            schema="ap",
        )

    # Add version column to ar.invoice
    if not has_column("ar", "invoice", "version"):
        op.add_column(
            "invoice",
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
                comment="Optimistic locking version",
            ),
            schema="ar",
        )

    # Add version column to gl.journal_entry
    if not has_column("gl", "journal_entry", "version"):
        op.add_column(
            "journal_entry",
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
                comment="Optimistic locking version",
            ),
            schema="gl",
        )

    # Add version column to audit.approval_request
    if not has_column("audit", "approval_request", "version"):
        op.add_column(
            "approval_request",
            sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
                comment="Optimistic locking version",
            ),
            schema="audit",
        )


def downgrade() -> None:
    op.drop_column("approval_request", "version", schema="audit")
    op.drop_column("journal_entry", "version", schema="gl")
    op.drop_column("invoice", "version", schema="ar")
    op.drop_column("supplier_invoice", "version", schema="ap")
