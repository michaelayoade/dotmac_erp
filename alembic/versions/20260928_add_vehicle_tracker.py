"""Add durable Fleet vehicle-to-tracker mappings.

Revision ID: 20260928_add_vehicle_tracker
Revises: 20260928_file_orphan_cleanup_records
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260928_add_vehicle_tracker"
down_revision = "20260928_file_orphan_cleanup_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_fleet_vehicle_id_org",
        "vehicle",
        ["vehicle_id", "organization_id"],
        schema="fleet",
    )
    op.create_table(
        "vehicle_tracker",
        sa.Column(
            "tracker_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vehicle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=50),
            server_default="TRACCAR",
            nullable=False,
        ),
        sa.Column(
            "unique_id",
            sa.String(length=100),
            nullable=False,
            comment="Tracker IMEI / Traccar uniqueId",
        ),
        sa.Column(
            "external_device_id",
            sa.String(length=100),
            nullable=True,
            comment="Traccar internal device ID",
        ),
        sa.Column("tracker_manufacturer", sa.String(length=100), nullable=True),
        sa.Column("tracker_model", sa.String(length=100), nullable=True),
        sa.Column("protocol", sa.String(length=50), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(trim(provider)) > 0",
            name="ck_fleet_vehicle_tracker_provider_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(unique_id)) > 0",
            name="ck_fleet_vehicle_tracker_unique_id_nonempty",
        ),
        sa.CheckConstraint(
            "(is_active AND unassigned_at IS NULL) OR "
            "(NOT is_active AND unassigned_at IS NOT NULL)",
            name="ck_fleet_vehicle_tracker_assignment_state",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id", "organization_id"],
            ["fleet.vehicle.vehicle_id", "fleet.vehicle.organization_id"],
            name="fk_fleet_vehicle_tracker_vehicle_org",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tracker_id"),
        schema="fleet",
    )
    op.create_index(
        "idx_fleet_vehicle_tracker_org",
        "vehicle_tracker",
        ["organization_id"],
        schema="fleet",
    )
    op.create_index(
        "idx_fleet_vehicle_tracker_vehicle_history",
        "vehicle_tracker",
        ["organization_id", "vehicle_id", "assigned_at"],
        schema="fleet",
    )
    op.create_index(
        "uq_fleet_vehicle_tracker_active_vehicle",
        "vehicle_tracker",
        ["organization_id", "vehicle_id"],
        unique=True,
        schema="fleet",
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "uq_fleet_vehicle_tracker_active_unique_id",
        "vehicle_tracker",
        ["organization_id", "provider", "unique_id"],
        unique=True,
        schema="fleet",
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "uq_fleet_vehicle_tracker_active_external_id",
        "vehicle_tracker",
        ["organization_id", "provider", "external_device_id"],
        unique=True,
        schema="fleet",
        postgresql_where=sa.text("is_active AND external_device_id IS NOT NULL"),
    )
    op.execute("ALTER TABLE fleet.vehicle_tracker ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE fleet.vehicle_tracker FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY vehicle_tracker_tenant_isolation
            ON fleet.vehicle_tracker
            USING (
                organization_id = NULLIF(
                    current_setting('app.current_organization_id', true), ''
                )::uuid
            )
            WITH CHECK (
                organization_id = NULLIF(
                    current_setting('app.current_organization_id', true), ''
                )::uuid
            )
        """
    )
    # Unlinking is an UPDATE so assignment history cannot be erased by the
    # tenant application identity.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON TABLE fleet.vehicle_tracker TO app_user"
    )


def downgrade() -> None:
    op.drop_table("vehicle_tracker", schema="fleet")
    op.drop_constraint(
        "uq_fleet_vehicle_id_org",
        "vehicle",
        schema="fleet",
        type_="unique",
    )
