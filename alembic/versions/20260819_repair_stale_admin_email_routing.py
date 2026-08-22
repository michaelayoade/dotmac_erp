"""Repair stale ADMIN email module routing.

Revision ID: 20260819_repair_stale_admin_email_routing
Revises: 20260819_remove_retired_splynx_schedule
Create Date: 2026-08-19

Production logs showed repeated warnings for one ADMIN routing row pointing at
an email profile that is missing or inactive. The runtime already ignores that
broken module profile and falls through to normal profile/default resolution;
this migration removes only that stale pointer so the fallback path is explicit
and the warning stops.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "20260819_repair_stale_admin_email_routing"
down_revision = "20260819_remove_retired_splynx_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


STALE_ORGANIZATION_ID = "00000000-0000-0000-0000-000000000001"
STALE_PROFILE_ID = "1091e5a8-be69-43fb-a709-1a4d5bb550b5"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("module_email_routing", schema="public"):
        return
    if not inspector.has_table("email_profile", schema="public"):
        return

    bind.execute(
        sa.text(
            """
            DELETE FROM public.module_email_routing AS routing
             WHERE routing.organization_id = CAST(:organization_id AS uuid)
               AND routing.module = CAST('ADMIN' AS public.email_module)
               AND routing.email_profile_id = CAST(:profile_id AS uuid)
               AND NOT EXISTS (
                     SELECT 1
                       FROM public.email_profile AS profile
                      WHERE profile.profile_id = routing.email_profile_id
                        AND profile.is_active IS TRUE
                   )
            """
        ),
        {
            "organization_id": STALE_ORGANIZATION_ID,
            "profile_id": STALE_PROFILE_ID,
        },
    )


def downgrade() -> None:
    """No safe downgrade: the stale routing row contained no usable profile."""
