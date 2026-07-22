"""Add uploaded document metadata to help article overrides.

Revision ID: 20260722_sla_policy_documents
Revises: 20260721_extended_info_changes
Create Date: 2026-07-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260722_sla_policy_documents"
down_revision: str | tuple[str, ...] = "20260721_extended_info_changes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "help_article_override",
        sa.Column(
            "file_path",
            sa.String(length=500),
            nullable=True,
            comment="S3 object key for an uploaded policy document",
        ),
    )
    op.add_column(
        "help_article_override",
        sa.Column(
            "file_name",
            sa.String(length=255),
            nullable=True,
            comment="Original uploaded document filename",
        ),
    )
    op.add_column(
        "help_article_override",
        sa.Column(
            "file_content_type",
            sa.String(length=100),
            nullable=True,
            comment="Uploaded document MIME type",
        ),
    )
    op.add_column(
        "help_article_override",
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "help_article_override",
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=True,
            comment="SHA-256 hash of the uploaded document",
        ),
    )


def downgrade() -> None:
    op.drop_column("help_article_override", "content_hash")
    op.drop_column("help_article_override", "file_size_bytes")
    op.drop_column("help_article_override", "file_content_type")
    op.drop_column("help_article_override", "file_name")
    op.drop_column("help_article_override", "file_path")
