"""Add module-specific email override support.

Revision ID: 20260206_add_module_email_overrides
Revises: 20260206_add_ap_indexes_and_fk
Create Date: 2026-02-06
"""

import sqlalchemy as sa

from alembic import op

revision = "20260206_add_module_email_overrides"
down_revision = "20260206_add_ap_indexes_and_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Extend email_module enum with module-specific values
    op.execute("ALTER TYPE email_module ADD VALUE IF NOT EXISTS 'SUPPORT'")
    op.execute("ALTER TYPE email_module ADD VALUE IF NOT EXISTS 'PEOPLE_PAYROLL'")
    op.execute("ALTER TYPE email_module ADD VALUE IF NOT EXISTS 'INVENTORY_FLEET'")
    op.execute("ALTER TYPE email_module ADD VALUE IF NOT EXISTS 'PROCUREMENT'")
    op.execute("ALTER TYPE email_module ADD VALUE IF NOT EXISTS 'EXPENSE'")

    # Add use_default flag to module routing
    op.add_column(
        "module_email_routing",
        sa.Column(
            "use_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        schema="public",
    )

    # Existing routings imply custom profiles
    op.execute(
        """
        UPDATE public.module_email_routing
        SET use_default = false
        WHERE email_profile_id IS NOT NULL
        """
    )

    # Allow routing to omit a profile when using defaults
    op.alter_column(
        "module_email_routing",
        "email_profile_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=True,
        schema="public",
    )

    # Remove default for use_default going forward
    op.alter_column(
        "module_email_routing",
        "use_default",
        server_default=None,
        schema="public",
    )


def downgrade() -> None:
    # Downgrade is not fully reversible due to enum changes.
    op.drop_column("module_email_routing", "use_default", schema="public")
    op.alter_column(
        "module_email_routing",
        "email_profile_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        nullable=False,
        schema="public",
    )
