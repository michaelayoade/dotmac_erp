"""Add GeoJSON geofence support to location

Revision ID: 20260124_geojson_geofence
Revises:
Create Date: 2026-01-24

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260124_geojson_geofence"
down_revision = "20260124_add_hr_lifecycle_erpnext_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum type for geofence_type
    geofence_type_enum = postgresql.ENUM(
        "CIRCLE", "POLYGON", name="geofence_type_enum", create_type=False
    )

    # Create the enum type first
    op.execute("CREATE TYPE geofence_type_enum AS ENUM ('CIRCLE', 'POLYGON')")

    # Add geofence_type column with default CIRCLE (backwards compatible)
    op.add_column(
        "location",
        sa.Column(
            "geofence_type",
            geofence_type_enum,
            nullable=False,
            server_default=sa.text("'CIRCLE'"),
        ),
        schema="core_org",
    )

    # Add geofence_polygon column for GeoJSON storage
    op.add_column(
        "location",
        sa.Column(
            "geofence_polygon",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="GeoJSON Polygon or MultiPolygon geometry for complex boundaries",
        ),
        schema="core_org",
    )

    # Add index on geofence_polygon for faster queries
    op.create_index(
        "ix_location_geofence_polygon",
        "location",
        ["geofence_polygon"],
        schema="core_org",
        postgresql_using="gin",
    )


def downgrade() -> None:
    # Remove index
    op.drop_index(
        "ix_location_geofence_polygon",
        table_name="location",
        schema="core_org",
    )

    # Remove columns
    op.drop_column("location", "geofence_polygon", schema="core_org")
    op.drop_column("location", "geofence_type", schema="core_org")

    # Drop enum type
    op.execute("DROP TYPE geofence_type_enum")
