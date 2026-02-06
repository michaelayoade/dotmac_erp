"""Add PM comments and comment attachments.

Revision ID: 20260126_add_pm_comments
Revises: 20260126_add_payroll_entry_pending_status
Create Date: 2026-01-26
"""

from alembic import op
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "20260126_add_pm_comments"
down_revision = "20260126_add_payroll_entry_pending_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS pm")

    bind = op.get_bind()
    ensure_enum(
        bind,
        "pm_comment_type",
        "COMMENT",
        "INTERNAL_NOTE",
        "SYSTEM",
        schema="pm",
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pm.pm_comment (
            comment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            entity_type VARCHAR(20) NOT NULL,
            entity_id UUID NOT NULL,
            comment_type pm.pm_comment_type NOT NULL DEFAULT 'COMMENT',
            content TEXT NOT NULL,
            action VARCHAR(50),
            old_value VARCHAR(255),
            new_value VARCHAR(255),
            author_id UUID REFERENCES people(id),
            is_internal BOOLEAN NOT NULL DEFAULT FALSE,
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX IF NOT EXISTS idx_pm_comment_entity
            ON pm.pm_comment(organization_id, entity_type, entity_id);
        CREATE INDEX IF NOT EXISTS idx_pm_comment_author
            ON pm.pm_comment(author_id);
        CREATE INDEX IF NOT EXISTS idx_pm_comment_created
            ON pm.pm_comment(created_at);
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS pm.pm_comment_attachment (
            comment_id UUID NOT NULL REFERENCES pm.pm_comment(comment_id) ON DELETE CASCADE,
            attachment_id UUID NOT NULL REFERENCES common.attachment(attachment_id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (comment_id, attachment_id)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pm.pm_comment_attachment CASCADE")
    op.execute("DROP TABLE IF EXISTS pm.pm_comment CASCADE")
    op.execute("DROP TYPE IF EXISTS pm.pm_comment_type CASCADE")
