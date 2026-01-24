"""Add general notification table.

Revision ID: 20260124_notification
Revises: 20260124_add_customer_relationships, 20260124_expense_gl, 20260124_support
Create Date: 2026-01-24

Merges all support/expense/customer heads and adds the general notification table.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260124_notification"
down_revision = (
    "20260124_add_customer_relationships",
    "20260124_expense_gl",
    "20260124_support",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enums
    entity_type_enum = postgresql.ENUM(
        "TICKET",
        "EXPENSE",
        "LEAVE",
        "ATTENDANCE",
        "PAYROLL",
        "EMPLOYEE",
        "APPROVAL",
        "SYSTEM",
        name="entitytype",
        create_type=False,
    )
    notification_type_enum = postgresql.ENUM(
        "ASSIGNED",
        "REASSIGNED",
        "STATUS_CHANGE",
        "APPROVED",
        "REJECTED",
        "SUBMITTED",
        "COMMENT",
        "REPLY",
        "MENTION",
        "DUE_SOON",
        "OVERDUE",
        "RESOLVED",
        "COMPLETED",
        "REMINDER",
        "ALERT",
        "INFO",
        name="notificationtype",
        create_type=False,
    )
    notification_channel_enum = postgresql.ENUM(
        "IN_APP",
        "EMAIL",
        "BOTH",
        name="notificationchannel",
        create_type=False,
    )

    # Create the enums in the database
    entity_type_enum.create(op.get_bind(), checkfirst=True)
    notification_type_enum.create(op.get_bind(), checkfirst=True)
    notification_channel_enum.create(op.get_bind(), checkfirst=True)

    # Create the notification table
    op.create_table(
        "notification",
        sa.Column(
            "notification_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column(
            "recipient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.people.id"),
            nullable=False,
        ),
        sa.Column(
            "entity_type",
            entity_type_enum,
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "notification_type",
            notification_type_enum,
            nullable=False,
        ),
        sa.Column(
            "channel",
            notification_channel_enum,
            nullable=False,
            server_default="IN_APP",
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("action_url", sa.String(500), nullable=True),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("read_at", sa.DateTime, nullable=True),
        sa.Column("email_sent", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("email_sent_at", sa.DateTime, nullable=True),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.people.id"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.text("now()"),
        ),
        schema="public",
    )

    # Create indexes
    op.create_index(
        "ix_notification_organization_id",
        "notification",
        ["organization_id"],
        schema="public",
    )
    op.create_index(
        "ix_notification_recipient_id",
        "notification",
        ["recipient_id"],
        schema="public",
    )
    op.create_index(
        "ix_notification_recipient_unread",
        "notification",
        ["recipient_id", "is_read"],
        schema="public",
    )
    op.create_index(
        "ix_notification_entity",
        "notification",
        ["entity_type", "entity_id"],
        schema="public",
    )
    op.create_index(
        "ix_notification_created_at",
        "notification",
        ["created_at"],
        schema="public",
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_notification_created_at", table_name="notification", schema="public")
    op.drop_index("ix_notification_entity", table_name="notification", schema="public")
    op.drop_index("ix_notification_recipient_unread", table_name="notification", schema="public")
    op.drop_index("ix_notification_recipient_id", table_name="notification", schema="public")
    op.drop_index("ix_notification_organization_id", table_name="notification", schema="public")

    # Drop table
    op.drop_table("notification", schema="public")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS notificationchannel")
    op.execute("DROP TYPE IF EXISTS notificationtype")
    op.execute("DROP TYPE IF EXISTS entitytype")
