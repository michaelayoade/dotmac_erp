"""Add service hook tables for extensibility.

Revision ID: 20260225_add_service_hooks
Revises: 20260225_add_stock_reservation
Create Date: 2026-02-25
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260225_add_service_hooks"
down_revision: Union[str, Sequence[str], None] = "20260225_add_stock_reservation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ensure_enum(schema: str, enum_name: str, values: tuple[str, ...]) -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    enums = {enum["name"] for enum in inspector.get_enums(schema=schema)}
    if enum_name in enums:
        return
    literals = ", ".join(f"'{value}'" for value in values)
    op.execute(f"CREATE TYPE {schema}.{enum_name} AS ENUM ({literals})")


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    _ensure_enum("platform", "hook_handler_type", ("WEBHOOK", "EVENT_OUTBOX"))
    _ensure_enum("platform", "hook_execution_mode", ("SYNC", "ASYNC"))
    _ensure_enum(
        "platform",
        "hook_execution_status",
        ("PENDING", "SUCCESS", "FAILED", "RETRYING", "DEAD"),
    )

    if not inspector.has_table("service_hook", schema="platform"):
        op.create_table(
            "service_hook",
            sa.Column(
                "hook_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("event_name", sa.String(length=100), nullable=False),
            sa.Column(
                "handler_type",
                postgresql.ENUM(
                    "WEBHOOK",
                    "EVENT_OUTBOX",
                    name="hook_handler_type",
                    schema="platform",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column(
                "execution_mode",
                postgresql.ENUM(
                    "SYNC",
                    "ASYNC",
                    name="hook_execution_mode",
                    schema="platform",
                    create_type=False,
                ),
                nullable=False,
                server_default=sa.text("'ASYNC'::platform.hook_execution_mode"),
            ),
            sa.Column(
                "handler_config",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "conditions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "priority",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("10"),
            ),
            sa.Column(
                "max_retries",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("3"),
            ),
            sa.Column(
                "retry_backoff_seconds",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("60"),
            ),
            sa.Column(
                "created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("hook_id"),
            schema="platform",
        )

    hook_indexes = {
        idx["name"] for idx in inspector.get_indexes("service_hook", schema="platform")
    }
    if "ix_hook_event_org" not in hook_indexes:
        op.create_index(
            "ix_hook_event_org",
            "service_hook",
            ["event_name", "organization_id"],
            schema="platform",
        )
    if "ix_hook_active" not in hook_indexes:
        op.create_index(
            "ix_hook_active",
            "service_hook",
            ["is_active", "event_name"],
            schema="platform",
        )

    if not inspector.has_table("service_hook_execution", schema="platform"):
        op.create_table(
            "service_hook_execution",
            sa.Column(
                "execution_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("hook_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("event_name", sa.String(length=100), nullable=False),
            sa.Column(
                "event_payload",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "status",
                postgresql.ENUM(
                    "PENDING",
                    "SUCCESS",
                    "FAILED",
                    "RETRYING",
                    "DEAD",
                    name="hook_execution_status",
                    schema="platform",
                    create_type=False,
                ),
                nullable=False,
                server_default=sa.text("'PENDING'::platform.hook_execution_status"),
            ),
            sa.Column("response_body", sa.Text(), nullable=True),
            sa.Column("response_status_code", sa.Integer(), nullable=True),
            sa.Column("error_message", sa.String(length=500), nullable=True),
            sa.Column(
                "retry_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["hook_id"],
                ["platform.service_hook.hook_id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("execution_id"),
            schema="platform",
        )

    execution_indexes = {
        idx["name"]
        for idx in inspector.get_indexes("service_hook_execution", schema="platform")
    }
    if "ix_execution_hook_status" not in execution_indexes:
        op.create_index(
            "ix_execution_hook_status",
            "service_hook_execution",
            ["hook_id", "status"],
            schema="platform",
        )
    if "ix_execution_created" not in execution_indexes:
        op.create_index(
            "ix_execution_created",
            "service_hook_execution",
            ["created_at"],
            schema="platform",
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table("service_hook_execution", schema="platform"):
        execution_indexes = {
            idx["name"]
            for idx in inspector.get_indexes(
                "service_hook_execution", schema="platform"
            )
        }
        if "ix_execution_created" in execution_indexes:
            op.drop_index(
                "ix_execution_created",
                table_name="service_hook_execution",
                schema="platform",
            )
        if "ix_execution_hook_status" in execution_indexes:
            op.drop_index(
                "ix_execution_hook_status",
                table_name="service_hook_execution",
                schema="platform",
            )
        op.drop_table("service_hook_execution", schema="platform")

    if inspector.has_table("service_hook", schema="platform"):
        hook_indexes = {
            idx["name"]
            for idx in inspector.get_indexes("service_hook", schema="platform")
        }
        if "ix_hook_active" in hook_indexes:
            op.drop_index(
                "ix_hook_active", table_name="service_hook", schema="platform"
            )
        if "ix_hook_event_org" in hook_indexes:
            op.drop_index(
                "ix_hook_event_org",
                table_name="service_hook",
                schema="platform",
            )
        op.drop_table("service_hook", schema="platform")

    enums = {enum["name"] for enum in inspector.get_enums(schema="platform")}
    if "hook_execution_status" in enums:
        op.execute("DROP TYPE platform.hook_execution_status")
    if "hook_execution_mode" in enums:
        op.execute("DROP TYPE platform.hook_execution_mode")
    if "hook_handler_type" in enums:
        op.execute("DROP TYPE platform.hook_handler_type")
