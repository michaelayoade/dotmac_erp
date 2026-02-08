"""Add RBAC tables for roles and permissions.

Revision ID: 20260124_rbac_tables
Revises: 20260124_geojson_geofence
Create Date: 2026-01-24

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260124_rbac_tables"
down_revision = "20260124_geojson_geofence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # If core RBAC tables already exist (from initial schema), skip to avoid conflict.
    if inspector.has_table("roles") and inspector.has_table("permissions"):
        return

    # Create roles table
    op.create_table(
        "roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    # Create permissions table
    op.create_table(
        "permissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("key", name="uq_permissions_key"),
    )

    # Create role_permissions junction table
    op.create_table(
        "role_permissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "permission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "role_id", "permission_id", name="uq_role_permissions_role_permission"
        ),
    )

    # Create person_roles junction table
    op.create_table(
        "person_roles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "person_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("person_id", "role_id", name="uq_person_roles_person_role"),
    )

    # Create indexes for faster lookups
    op.create_index("ix_roles_name", "roles", ["name"])
    op.create_index("ix_roles_is_active", "roles", ["is_active"])
    op.create_index("ix_permissions_key", "permissions", ["key"])
    op.create_index("ix_permissions_is_active", "permissions", ["is_active"])
    op.create_index("ix_role_permissions_role_id", "role_permissions", ["role_id"])
    op.create_index(
        "ix_role_permissions_permission_id", "role_permissions", ["permission_id"]
    )
    op.create_index("ix_person_roles_person_id", "person_roles", ["person_id"])
    op.create_index("ix_person_roles_role_id", "person_roles", ["role_id"])


def downgrade() -> None:
    # These tables may be shared with the initial schema; avoid destructive drops.
    return

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Drop indexes
    op.drop_index("ix_person_roles_role_id", table_name="person_roles")
    op.drop_index("ix_person_roles_person_id", table_name="person_roles")
    op.drop_index("ix_role_permissions_permission_id", table_name="role_permissions")
    op.drop_index("ix_role_permissions_role_id", table_name="role_permissions")
    op.drop_index("ix_permissions_is_active", table_name="permissions")
    op.drop_index("ix_permissions_key", table_name="permissions")
    op.drop_index("ix_roles_is_active", table_name="roles")
    op.drop_index("ix_roles_name", table_name="roles")

    # Drop tables in reverse order of creation
    op.drop_table("person_roles")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
