"""Add organization_id to audit events.

Revision ID: 20260131_add_audit_events_org_id
Revises: 20260131_batch_ops
Create Date: 2026-01-31
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260131_add_audit_events_org_id"
down_revision = "20260131_batch_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_events",
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_audit_events_organization_id",
        "audit_events",
        ["organization_id"],
    )

    # Backfill organization_id for user actors
    op.execute(
        """
        UPDATE audit_events ae
        SET organization_id = p.organization_id
        FROM people p
        WHERE ae.organization_id IS NULL
          AND ae.actor_type = 'user'
          AND ae.actor_id ~* '^[0-9a-f-]{36}$'
          AND p.id = ae.actor_id::uuid
        """
    )

    # Backfill organization_id for api_key actors
    op.execute(
        """
        UPDATE audit_events ae
        SET organization_id = p.organization_id
        FROM api_keys ak
        JOIN people p ON p.id = ak.person_id
        WHERE ae.organization_id IS NULL
          AND ae.actor_type = 'api_key'
          AND ae.actor_id ~* '^[0-9a-f-]{36}$'
          AND ak.id = ae.actor_id::uuid
        """
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_organization_id", table_name="audit_events")
    op.drop_column("audit_events", "organization_id")
