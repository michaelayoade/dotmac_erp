"""Add employee location/shift fields and location geofence settings.

Revision ID: 20260123_add_employee_location_shift_fields
Revises: 4f4e6f737d70
Create Date: 2026-01-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260123_add_employee_location_shift_fields"
down_revision = "4f4e6f737d70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_column(schema: str, table: str, column: str) -> bool:
        if not inspector.has_table(table, schema=schema):
            return False
        return any(col["name"] == column for col in inspector.get_columns(table, schema=schema))

    def has_fk(schema: str, table: str, name: str) -> bool:
        if not inspector.has_table(table, schema=schema):
            return False
        return any(fk["name"] == name for fk in inspector.get_foreign_keys(table, schema=schema))

    if not has_column("core_org", "location", "latitude"):
        op.add_column(
            "location",
            sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
            schema="core_org",
        )
    if not has_column("core_org", "location", "longitude"):
        op.add_column(
            "location",
            sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
            schema="core_org",
        )
    if not has_column("core_org", "location", "geofence_radius_m"):
        op.add_column(
            "location",
            sa.Column(
                "geofence_radius_m",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("500"),
            ),
            schema="core_org",
        )
    if not has_column("core_org", "location", "geofence_enabled"):
        op.add_column(
            "location",
            sa.Column(
                "geofence_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            schema="core_org",
        )

    if not has_column("hr", "employee", "assigned_location_id"):
        op.add_column(
            "employee",
            sa.Column("assigned_location_id", postgresql.UUID(as_uuid=True), nullable=True),
            schema="hr",
        )
    if not has_column("hr", "employee", "default_shift_type_id"):
        op.add_column(
            "employee",
            sa.Column("default_shift_type_id", postgresql.UUID(as_uuid=True), nullable=True),
            schema="hr",
        )

    if not has_fk("hr", "employee", "fk_employee_assigned_location"):
        op.create_foreign_key(
            "fk_employee_assigned_location",
            "employee",
            "location",
            ["assigned_location_id"],
            ["location_id"],
            source_schema="hr",
            referent_schema="core_org",
            ondelete="SET NULL",
        )
    if not has_fk("hr", "employee", "fk_employee_default_shift_type"):
        op.create_foreign_key(
            "fk_employee_default_shift_type",
            "employee",
            "shift_type",
            ["default_shift_type_id"],
            ["shift_type_id"],
            source_schema="hr",
            referent_schema="attendance",
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_column(schema: str, table: str, column: str) -> bool:
        if not inspector.has_table(table, schema=schema):
            return False
        return any(col["name"] == column for col in inspector.get_columns(table, schema=schema))

    def has_fk(schema: str, table: str, name: str) -> bool:
        if not inspector.has_table(table, schema=schema):
            return False
        return any(fk["name"] == name for fk in inspector.get_foreign_keys(table, schema=schema))

    if has_fk("hr", "employee", "fk_employee_default_shift_type"):
        op.drop_constraint(
            "fk_employee_default_shift_type",
            "employee",
            schema="hr",
            type_="foreignkey",
        )
    if has_fk("hr", "employee", "fk_employee_assigned_location"):
        op.drop_constraint(
            "fk_employee_assigned_location",
            "employee",
            schema="hr",
            type_="foreignkey",
        )

    if has_column("hr", "employee", "default_shift_type_id"):
        op.drop_column("employee", "default_shift_type_id", schema="hr")
    if has_column("hr", "employee", "assigned_location_id"):
        op.drop_column("employee", "assigned_location_id", schema="hr")

    if has_column("core_org", "location", "geofence_enabled"):
        op.drop_column("location", "geofence_enabled", schema="core_org")
    if has_column("core_org", "location", "geofence_radius_m"):
        op.drop_column("location", "geofence_radius_m", schema="core_org")
    if has_column("core_org", "location", "longitude"):
        op.drop_column("location", "longitude", schema="core_org")
    if has_column("core_org", "location", "latitude"):
        op.drop_column("location", "latitude", schema="core_org")
