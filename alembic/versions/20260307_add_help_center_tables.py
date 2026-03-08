"""Add help center tables for progress, feedback, overrides, and search analytics.

Revision ID: 20260307_help_center
Revises: 20260307_pll_idx
Create Date: 2026-03-07
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260307_help_center"
down_revision = "20260307_pll_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- help_article_status enum --
    help_article_status = postgresql.ENUM(
        "DRAFT", "PUBLISHED", "ARCHIVED", name="help_article_status", create_type=False
    )
    help_article_status.create(op.get_bind(), checkfirst=True)

    # -- help_article_override --
    op.create_table(
        "help_article_override",
        sa.Column("article_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(200), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("body_json", postgresql.JSON, nullable=True),
        sa.Column("module_key", sa.String(50), nullable=False),
        sa.Column("content_type", sa.String(50), nullable=False, server_default="workflow"),
        sa.Column(
            "status",
            postgresql.ENUM("DRAFT", "PUBLISHED", "ARCHIVED", name="help_article_status", create_type=False),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_help_article_org_slug", "help_article_override", ["organization_id", "slug"], unique=True)
    op.create_index("idx_help_article_org_status", "help_article_override", ["organization_id", "status"])
    op.create_index("idx_help_article_module", "help_article_override", ["organization_id", "module_key"])

    # -- help_user_progress --
    op.create_table(
        "help_user_progress",
        sa.Column("progress_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("article_slug", sa.String(200), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "idx_help_progress_unique",
        "help_user_progress",
        ["organization_id", "person_id", "article_slug"],
        unique=True,
    )
    op.create_index("idx_help_progress_person", "help_user_progress", ["person_id"])

    # -- help_article_feedback --
    op.create_table(
        "help_article_feedback",
        sa.Column("feedback_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("article_slug", sa.String(200), nullable=False),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "idx_help_feedback_unique",
        "help_article_feedback",
        ["organization_id", "person_id", "article_slug"],
        unique=True,
    )
    op.create_index("idx_help_feedback_slug", "help_article_feedback", ["organization_id", "article_slug"])

    # -- help_search_event --
    op.create_table(
        "help_search_event",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("query", sa.String(500), nullable=False),
        sa.Column("filters", postgresql.JSON, nullable=True),
        sa.Column("result_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("clicked_slug", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_help_search_org", "help_search_event", ["organization_id"])
    op.create_index("idx_help_search_created", "help_search_event", ["created_at"])


def downgrade() -> None:
    op.drop_table("help_search_event")
    op.drop_table("help_article_feedback")
    op.drop_table("help_user_progress")
    op.drop_table("help_article_override")
    op.execute("DROP TYPE IF EXISTS help_article_status")
