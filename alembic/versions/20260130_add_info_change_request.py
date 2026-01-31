"""Add employee info change request table for approval workflow.

Stores pending changes to employee bank/tax/pension info that require
approval before being applied to actual records.

Revision ID: 20260130_add_info_change_request
Revises: 20260130_merge_loans_statutory
Create Date: 2026-01-30
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260130_add_info_change_request"
down_revision = "20260130_merge_loans_statutory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enums
    info_change_type = postgresql.ENUM(
        "BANK_DETAILS", "TAX_INFO", "PENSION_INFO", "NHF_INFO", "COMBINED",
        name="info_change_type",
        schema="hr",
        create_type=False,
    )
    info_change_type.create(op.get_bind(), checkfirst=True)

    info_change_status = postgresql.ENUM(
        "PENDING", "APPROVED", "REJECTED", "CANCELLED", "EXPIRED",
        name="info_change_status",
        schema="hr",
        create_type=False,
    )
    info_change_status.create(op.get_bind(), checkfirst=True)

    # Create the table
    op.create_table(
        "employee_info_change_request",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee.employee_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "change_type",
            sa.Enum(
                "BANK_DETAILS", "TAX_INFO", "PENSION_INFO", "NHF_INFO", "COMBINED",
                name="info_change_type",
                schema="hr",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "APPROVED", "REJECTED", "CANCELLED", "EXPIRED",
                name="info_change_status",
                schema="hr",
                create_type=False,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("proposed_changes", postgresql.JSONB(), nullable=False),
        sa.Column("previous_values", postgresql.JSONB(), nullable=False),
        sa.Column("requester_notes", sa.Text(), nullable=True),
        sa.Column(
            "reviewer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="hr",
    )

    # Create indexes
    op.create_index(
        "idx_info_change_request_org",
        "employee_info_change_request",
        ["organization_id"],
        schema="hr",
    )
    op.create_index(
        "idx_info_change_request_employee",
        "employee_info_change_request",
        ["employee_id"],
        schema="hr",
    )
    op.create_index(
        "idx_info_change_request_status",
        "employee_info_change_request",
        ["organization_id", "status"],
        schema="hr",
    )
    op.create_index(
        "idx_info_change_request_pending",
        "employee_info_change_request",
        ["organization_id", "status", "created_at"],
        schema="hr",
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("idx_info_change_request_pending", table_name="employee_info_change_request", schema="hr")
    op.drop_index("idx_info_change_request_status", table_name="employee_info_change_request", schema="hr")
    op.drop_index("idx_info_change_request_employee", table_name="employee_info_change_request", schema="hr")
    op.drop_index("idx_info_change_request_org", table_name="employee_info_change_request", schema="hr")

    # Drop table
    op.drop_table("employee_info_change_request", schema="hr")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS hr.info_change_status")
    op.execute("DROP TYPE IF EXISTS hr.info_change_type")
