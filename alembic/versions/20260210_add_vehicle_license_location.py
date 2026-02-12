"""Add vehicle license expiry date and location.

Revision ID: 20260210_add_vehicle_license_location
Revises: 20260210_seed_hr_workflow_rules
Create Date: 2026-02-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260210_add_vehicle_license_location"
down_revision = "20260210_seed_hr_workflow_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vehicle",
        sa.Column("license_expiry_date", sa.Date(), nullable=True),
        schema="fleet",
    )
    op.add_column(
        "vehicle",
        sa.Column(
            "location_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        schema="fleet",
    )
    op.create_foreign_key(
        "fk_fleet_vehicle_location",
        "vehicle",
        "location",
        ["location_id"],
        ["location_id"],
        source_schema="fleet",
        referent_schema="core_org",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_fleet_vehicle_location",
        "vehicle",
        schema="fleet",
        type_="foreignkey",
    )
    op.drop_column("vehicle", "location_id", schema="fleet")
    op.drop_column("vehicle", "license_expiry_date", schema="fleet")
