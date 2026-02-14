"""Add tenant and actor linkage columns to audit_events.

Revision ID: 20260213_add_audit_event_actor_links
Revises: 0ba9d5aea52b, 20260213_add_gt_pension_pfa
Create Date: 2026-02-13

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260213_add_audit_event_actor_links"
down_revision = ("0ba9d5aea52b", "20260213_add_gt_pension_pfa")
branch_labels = None
depends_on = None


def _has_fk(inspector, table_name: str, fk_name: str) -> bool:
    return any(
        fk.get("name") == fk_name for fk in inspector.get_foreign_keys(table_name)
    )


def _has_index(inspector, table_name: str, index_name: str) -> bool:
    return any(
        idx.get("name") == index_name for idx in inspector.get_indexes(table_name)
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("audit_events"):
        return

    columns = {col["name"] for col in inspector.get_columns("audit_events")}

    if "organization_id" not in columns:
        op.add_column(
            "audit_events",
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        )

    if "actor_person_id" not in columns:
        op.add_column(
            "audit_events",
            sa.Column("actor_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        )

    inspector = inspect(bind)

    if not _has_index(inspector, "audit_events", "ix_audit_events_organization_id"):
        op.create_index(
            "ix_audit_events_organization_id",
            "audit_events",
            ["organization_id"],
        )

    if not _has_index(inspector, "audit_events", "ix_audit_events_actor_person_id"):
        op.create_index(
            "ix_audit_events_actor_person_id",
            "audit_events",
            ["actor_person_id"],
        )

    if not _has_fk(inspector, "audit_events", "fk_audit_events_actor_person_id_people"):
        op.create_foreign_key(
            "fk_audit_events_actor_person_id_people",
            "audit_events",
            "people",
            ["actor_person_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Backfill actor_person_id from actor_id when actor_id is a UUID and actor is a user.
    op.execute(
        """
        UPDATE audit_events
        SET actor_person_id = actor_id::uuid
        WHERE actor_person_id IS NULL
          AND actor_type = 'user'
          AND actor_id ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$';
        """
    )

    # Backfill organization_id from linked person record when available.
    op.execute(
        """
        UPDATE audit_events ae
        SET organization_id = p.organization_id
        FROM people p
        WHERE ae.organization_id IS NULL
          AND ae.actor_person_id = p.id;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)

    if not inspector.has_table("audit_events"):
        return

    if _has_fk(inspector, "audit_events", "fk_audit_events_actor_person_id_people"):
        op.drop_constraint(
            "fk_audit_events_actor_person_id_people",
            "audit_events",
            type_="foreignkey",
        )

    if _has_index(inspector, "audit_events", "ix_audit_events_actor_person_id"):
        op.drop_index("ix_audit_events_actor_person_id", table_name="audit_events")

    if _has_index(inspector, "audit_events", "ix_audit_events_organization_id"):
        op.drop_index("ix_audit_events_organization_id", table_name="audit_events")

    columns = {col["name"] for col in inspector.get_columns("audit_events")}

    if "actor_person_id" in columns:
        op.drop_column("audit_events", "actor_person_id")

    if "organization_id" in columns:
        op.drop_column("audit_events", "organization_id")
