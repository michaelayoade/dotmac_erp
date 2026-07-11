"""Add ar.dotmac_sub_sync_watermark (incremental AR sync high-watermark).

Incident: the AR pull re-listed every dotmac_sub invoice each cycle via OFFSET
pagination over an unindexed ``created_at`` global sort, building long-running
DB sessions that starved dotmac_sub's app connection pool (QueuePool timeouts).

The fix pulls incrementally with an ``updated_since`` watermark. This table is
the per-org, per-entity cursor: the highest ``updated_at`` already synced for
invoices / payments / credit-notes. NULL / missing means "never synced" → the
next pull is a full pull, then it increments.

Revision ID: 20260711_add_dotmac_sub_sync_watermark
Revises: 20260710_add_employee_dotmac_sub_account
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260711_add_dotmac_sub_sync_watermark"
down_revision = "20260710_add_employee_dotmac_sub_account"
branch_labels = None
depends_on = None

_TABLE = "dotmac_sub_sync_watermark"
_SCHEMA = "ar"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_TABLE, schema=_SCHEMA):
        return

    op.create_table(
        _TABLE,
        sa.Column(
            "watermark_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column(
            "watermark_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Highest dotmac_sub updated_at synced for this entity type",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("watermark_id"),
        sa.UniqueConstraint(
            "organization_id",
            "entity_type",
            name="uq_dotmac_sub_watermark_org_entity",
        ),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_TABLE, schema=_SCHEMA):
        op.drop_table(_TABLE, schema=_SCHEMA)
