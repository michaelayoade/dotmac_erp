"""Add support module enhancements.

- Add ticket_comment table
- Add ticket_attachment table
- Add ticket_notification table
- Add support_team and support_team_member tables
- Add ticket_category table
- Add category_id, team_id, is_deleted to ticket table

Revision ID: 20260124_support
Revises:
Create Date: 2026-01-24

"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260124_support"
down_revision: Union[str, None] = (
    "create_support_schema"  # Fixed: connect to initial schema
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create comment_type enum
    op.execute(
        "CREATE TYPE support.comment_type AS ENUM ('COMMENT', 'INTERNAL_NOTE', 'SYSTEM')"
    )

    # Create ticket_category table
    op.create_table(
        "ticket_category",
        sa.Column(
            "category_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("category_code", sa.String(20), nullable=False),
        sa.Column("category_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(7), nullable=True),
        sa.Column("icon", sa.String(50), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("default_team_id", sa.UUID(), nullable=True),
        sa.Column("default_priority", sa.String(20), nullable=True),
        sa.Column("response_hours", sa.Integer(), nullable=True),
        sa.Column("resolution_hours", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "requires_project", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.PrimaryKeyConstraint("category_id"),
        sa.UniqueConstraint(
            "organization_id", "category_code", name="uq_ticket_category_org_code"
        ),
        schema="support",
    )
    op.create_index(
        "ix_support_ticket_category_org",
        "ticket_category",
        ["organization_id"],
        schema="support",
    )

    # Create support_team table
    op.create_table(
        "support_team",
        sa.Column(
            "team_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("team_code", sa.String(20), nullable=False),
        sa.Column("team_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("lead_id", sa.UUID(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("auto_assign", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("default_response_hours", sa.Integer(), nullable=True),
        sa.Column("default_resolution_hours", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["core_org.organization.organization_id"]
        ),
        sa.ForeignKeyConstraint(["lead_id"], ["hr.employee.employee_id"]),
        sa.PrimaryKeyConstraint("team_id"),
        sa.UniqueConstraint(
            "organization_id", "team_code", name="uq_support_team_org_code"
        ),
        schema="support",
    )
    op.create_index(
        "ix_support_support_team_org",
        "support_team",
        ["organization_id"],
        schema="support",
    )

    # Add FK from ticket_category.default_team_id to support_team
    op.create_foreign_key(
        "fk_ticket_category_default_team",
        "ticket_category",
        "support_team",
        ["default_team_id"],
        ["team_id"],
        source_schema="support",
        referent_schema="support",
    )

    # Create support_team_member table
    op.create_table(
        "support_team_member",
        sa.Column(
            "member_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("team_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(50), nullable=True),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "assignment_weight", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("assigned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["support.support_team.team_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["employee_id"], ["hr.employee.employee_id"]),
        sa.PrimaryKeyConstraint("member_id"),
        sa.UniqueConstraint("team_id", "employee_id", name="uq_team_member"),
        schema="support",
    )
    op.create_index(
        "ix_support_team_member_team",
        "support_team_member",
        ["team_id"],
        schema="support",
    )
    op.create_index(
        "ix_support_team_member_employee",
        "support_team_member",
        ["employee_id"],
        schema="support",
    )

    # Create ticket_comment table
    op.create_table(
        "ticket_comment",
        sa.Column(
            "comment_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column(
            "comment_type",
            postgresql.ENUM(
                "COMMENT",
                "INTERNAL_NOTE",
                "SYSTEM",
                name="comment_type",
                schema="support",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("action", sa.String(50), nullable=True),
        sa.Column("old_value", sa.String(255), nullable=True),
        sa.Column("new_value", sa.String(255), nullable=True),
        sa.Column("author_id", sa.UUID(), nullable=True),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["support.ticket.ticket_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["author_id"], ["public.people.id"]),
        sa.PrimaryKeyConstraint("comment_id"),
        schema="support",
    )
    op.create_index(
        "ix_support_ticket_comment_ticket",
        "ticket_comment",
        ["ticket_id"],
        schema="support",
    )

    # Create ticket_attachment table
    op.create_table(
        "ticket_attachment",
        sa.Column(
            "attachment_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column("comment_id", sa.UUID(), nullable=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("thumbnail_path", sa.String(500), nullable=True),
        sa.Column("uploaded_by_id", sa.UUID(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["support.ticket.ticket_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["comment_id"], ["support.ticket_comment.comment_id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["public.people.id"]),
        sa.PrimaryKeyConstraint("attachment_id"),
        schema="support",
    )
    op.create_index(
        "ix_support_ticket_attachment_ticket",
        "ticket_attachment",
        ["ticket_id"],
        schema="support",
    )

    # Create notification enums
    op.execute(
        "CREATE TYPE support.notification_type AS ENUM ('TICKET_CREATED', 'TICKET_ASSIGNED', 'TICKET_STATUS_CHANGE', 'TICKET_COMMENT', 'TICKET_REPLY', 'TICKET_PRIORITY_CHANGE', 'TICKET_DUE_SOON', 'TICKET_OVERDUE', 'TICKET_RESOLVED', 'TEAM_ASSIGNED')"
    )
    op.execute(
        "CREATE TYPE support.notification_channel AS ENUM ('IN_APP', 'EMAIL', 'BOTH')"
    )

    # Create ticket_notification table
    op.create_table(
        "ticket_notification",
        sa.Column(
            "notification_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("recipient_id", sa.UUID(), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=False),
        sa.Column(
            "notification_type",
            postgresql.ENUM(
                "TICKET_CREATED",
                "TICKET_ASSIGNED",
                "TICKET_STATUS_CHANGE",
                "TICKET_COMMENT",
                "TICKET_REPLY",
                "TICKET_PRIORITY_CHANGE",
                "TICKET_DUE_SOON",
                "TICKET_OVERDUE",
                "TICKET_RESOLVED",
                "TEAM_ASSIGNED",
                name="notification_type",
                schema="support",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "channel",
            postgresql.ENUM(
                "IN_APP",
                "EMAIL",
                "BOTH",
                name="notification_channel",
                schema="support",
                create_type=False,
            ),
            nullable=False,
            server_default="IN_APP",
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("action_url", sa.String(500), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("email_sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("email_sent_at", sa.DateTime(), nullable=True),
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["recipient_id"], ["public.people.id"]),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["support.ticket.ticket_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["public.people.id"]),
        sa.PrimaryKeyConstraint("notification_id"),
        schema="support",
    )
    op.create_index(
        "ix_support_notification_recipient",
        "ticket_notification",
        ["recipient_id"],
        schema="support",
    )
    op.create_index(
        "ix_support_notification_ticket",
        "ticket_notification",
        ["ticket_id"],
        schema="support",
    )
    op.create_index(
        "ix_support_notification_is_read",
        "ticket_notification",
        ["is_read"],
        schema="support",
    )
    op.create_index(
        "ix_support_notification_created",
        "ticket_notification",
        ["created_at"],
        schema="support",
    )

    # Add new columns to ticket table
    op.add_column(
        "ticket", sa.Column("category_id", sa.UUID(), nullable=True), schema="support"
    )
    op.add_column(
        "ticket", sa.Column("team_id", sa.UUID(), nullable=True), schema="support"
    )
    op.add_column(
        "ticket",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        schema="support",
    )

    op.create_foreign_key(
        "fk_ticket_category",
        "ticket",
        "ticket_category",
        ["category_id"],
        ["category_id"],
        source_schema="support",
        referent_schema="support",
    )
    op.create_foreign_key(
        "fk_ticket_team",
        "ticket",
        "support_team",
        ["team_id"],
        ["team_id"],
        source_schema="support",
        referent_schema="support",
    )
    op.create_index(
        "ix_support_ticket_is_deleted", "ticket", ["is_deleted"], schema="support"
    )


def downgrade() -> None:
    # Remove columns from ticket
    op.drop_constraint("fk_ticket_team", "ticket", schema="support", type_="foreignkey")
    op.drop_constraint(
        "fk_ticket_category", "ticket", schema="support", type_="foreignkey"
    )
    op.drop_index("ix_support_ticket_is_deleted", "ticket", schema="support")
    op.drop_column("ticket", "is_deleted", schema="support")
    op.drop_column("ticket", "team_id", schema="support")
    op.drop_column("ticket", "category_id", schema="support")

    # Drop notification table
    op.drop_index(
        "ix_support_notification_created", "ticket_notification", schema="support"
    )
    op.drop_index(
        "ix_support_notification_is_read", "ticket_notification", schema="support"
    )
    op.drop_index(
        "ix_support_notification_ticket", "ticket_notification", schema="support"
    )
    op.drop_index(
        "ix_support_notification_recipient", "ticket_notification", schema="support"
    )
    op.drop_table("ticket_notification", schema="support")
    op.execute("DROP TYPE support.notification_channel")
    op.execute("DROP TYPE support.notification_type")

    # Drop tables in reverse order
    op.drop_index(
        "ix_support_ticket_attachment_ticket", "ticket_attachment", schema="support"
    )
    op.drop_table("ticket_attachment", schema="support")

    op.drop_index(
        "ix_support_ticket_comment_ticket", "ticket_comment", schema="support"
    )
    op.drop_table("ticket_comment", schema="support")

    op.drop_index(
        "ix_support_team_member_employee", "support_team_member", schema="support"
    )
    op.drop_index(
        "ix_support_team_member_team", "support_team_member", schema="support"
    )
    op.drop_table("support_team_member", schema="support")

    op.drop_constraint(
        "fk_ticket_category_default_team",
        "ticket_category",
        schema="support",
        type_="foreignkey",
    )

    op.drop_index("ix_support_support_team_org", "support_team", schema="support")
    op.drop_table("support_team", schema="support")

    op.drop_index("ix_support_ticket_category_org", "ticket_category", schema="support")
    op.drop_table("ticket_category", schema="support")

    op.execute("DROP TYPE support.comment_type")
