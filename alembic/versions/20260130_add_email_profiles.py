"""Add email profile tables for multi-profile SMTP support.

Creates tables:
- email_profile - SMTP configurations per organization
- module_email_routing - Module to profile mappings

Revision ID: 20260130_add_email_profiles
Revises: 20260130_add_employee_loans
Create Date: 2026-01-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260130_add_email_profiles"
down_revision = "20260130_add_settings_org_scope"  # Changed from add_employee_loans to skip problematic migration
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create email_module enum
    email_module_enum = postgresql.ENUM(
        "PAYROLL",
        "HR",
        "EXPENSE",
        "FINANCE",
        "SUPPORT",
        "SYSTEM",
        "MARKETING",
        name="email_module",
        create_type=False,
    )
    email_module_enum.create(op.get_bind(), checkfirst=True)

    # Create email_profile table
    op.create_table(
        "email_profile",
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
            nullable=True,
            comment="NULL = system-wide profile, UUID = org-specific profile",
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("smtp_host", sa.String(255), nullable=False),
        sa.Column("smtp_port", sa.Integer(), default=587),
        sa.Column("smtp_username", sa.String(255), nullable=True),
        sa.Column("smtp_password", sa.String(500), nullable=True),
        sa.Column("use_tls", sa.Boolean(), default=True),
        sa.Column("use_ssl", sa.Boolean(), default=False),
        sa.Column("from_email", sa.String(255), nullable=False),
        sa.Column("from_name", sa.String(255), default="Dotmac ERP"),
        sa.Column("reply_to", sa.String(255), nullable=True),
        sa.Column("is_default", sa.Boolean(), default=False),
        sa.Column("is_active", sa.Boolean(), default=True),
        sa.Column("pool_size", sa.Integer(), default=5),
        sa.Column("timeout_seconds", sa.Integer(), default=30),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
    )
    op.create_index("idx_email_profile_org", "email_profile", ["organization_id"])
    op.create_index(
        "idx_email_profile_default", "email_profile", ["organization_id", "is_default"]
    )

    # Create module_email_routing table
    op.create_table(
        "module_email_routing",
        sa.Column("routing_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "module",
            email_module_enum,
            nullable=False,
        ),
        sa.Column(
            "email_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("email_profile.profile_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "organization_id",
            "module",
            name="uq_module_email_routing_org_module",
        ),
    )
    op.create_index(
        "idx_module_email_routing_org", "module_email_routing", ["organization_id"]
    )


def downgrade() -> None:
    # Drop tables (with safety checks for idempotent downgrades)
    # Note: op.drop_table doesn't have if_exists, so we use raw SQL
    op.execute("DROP TABLE IF EXISTS module_email_routing CASCADE")
    op.execute("DROP TABLE IF EXISTS email_profile CASCADE")

    # Drop enum (schema-qualified for safety)
    op.execute("DROP TYPE IF EXISTS public.email_module CASCADE")
