"""Add domain_setting_history table for settings change tracking.

Revision ID: 20260124_setting_history
Revises: 20260124_material_request, 20260124_notification, 9b2a7c1d4c9a
Create Date: 2026-01-24

Merges current heads and adds a history table for tracking all domain
setting changes with user, timestamp, old/new values for audit and rollback.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260124_setting_history"
down_revision = (
    "20260124_material_request",
    "20260124_notification",
    "9b2a7c1d4c9a",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum for change action type
    change_action_enum = postgresql.ENUM(
        "CREATE",
        "UPDATE",
        "DELETE",
        name="settingchangeaction",
        create_type=False,
    )
    change_action_enum.create(op.get_bind(), checkfirst=True)

    # Create the domain_setting_history table
    op.create_table(
        "domain_setting_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "setting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.domain_settings.id", ondelete="SET NULL"),
            nullable=True,  # Allow NULL if setting is hard-deleted
            index=True,
        ),
        sa.Column(
            "domain",
            sa.String(50),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "key",
            sa.String(120),
            nullable=False,
        ),
        sa.Column(
            "action",
            change_action_enum,
            nullable=False,
        ),
        # Old values (NULL for CREATE)
        sa.Column("old_value_type", sa.String(20), nullable=True),
        sa.Column("old_value_text", sa.Text, nullable=True),
        sa.Column("old_value_json", postgresql.JSON, nullable=True),
        sa.Column("old_is_secret", sa.Boolean, nullable=True),
        sa.Column("old_is_active", sa.Boolean, nullable=True),
        # New values (NULL for DELETE)
        sa.Column("new_value_type", sa.String(20), nullable=True),
        sa.Column("new_value_text", sa.Text, nullable=True),
        sa.Column("new_value_json", postgresql.JSON, nullable=True),
        sa.Column("new_is_secret", sa.Boolean, nullable=True),
        sa.Column("new_is_active", sa.Boolean, nullable=True),
        # Audit metadata
        sa.Column(
            "changed_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.people.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "change_reason",
            sa.Text,
            nullable=True,
        ),
        sa.Column(
            "ip_address",
            sa.String(45),  # IPv6 max length
            nullable=True,
        ),
        sa.Column(
            "user_agent",
            sa.String(500),
            nullable=True,
        ),
        schema="public",
    )

    # Create indexes for common query patterns
    op.create_index(
        "ix_domain_setting_history_domain_key",
        "domain_setting_history",
        ["domain", "key"],
        schema="public",
    )
    op.create_index(
        "ix_domain_setting_history_changed_at",
        "domain_setting_history",
        ["changed_at"],
        schema="public",
    )
    op.create_index(
        "ix_domain_setting_history_changed_by",
        "domain_setting_history",
        ["changed_by_id"],
        schema="public",
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index(
        "ix_domain_setting_history_changed_by",
        table_name="domain_setting_history",
        schema="public",
    )
    op.drop_index(
        "ix_domain_setting_history_changed_at",
        table_name="domain_setting_history",
        schema="public",
    )
    op.drop_index(
        "ix_domain_setting_history_domain_key",
        table_name="domain_setting_history",
        schema="public",
    )

    # Drop table
    op.drop_table("domain_setting_history", schema="public")

    # Drop enum
    op.execute("DROP TYPE IF EXISTS settingchangeaction")
