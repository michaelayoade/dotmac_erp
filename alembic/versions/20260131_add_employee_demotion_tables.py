"""Add employee demotion tables

Revision ID: 20260131_demotion
Revises:
Create Date: 2026-01-31

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260131_demotion"
down_revision = "20260131_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create demotion_reason enum
    demotion_reason = postgresql.ENUM(
        "DISCIPLINARY",
        "PERFORMANCE",
        "RESTRUCTURING",
        "VOLUNTARY",
        "MEDICAL",
        "OTHER",
        name="demotion_reason",
        create_type=False,
    )
    demotion_reason.create(op.get_bind(), checkfirst=True)

    # Create demotion_status enum
    demotion_status = postgresql.ENUM(
        "DRAFT",
        "PENDING_APPROVAL",
        "APPROVED",
        "EXECUTED",
        "REJECTED",
        "CANCELLED",
        name="demotion_status",
        create_type=False,
    )
    demotion_status.create(op.get_bind(), checkfirst=True)

    # Create employee_demotion table
    op.create_table(
        "employee_demotion",
        sa.Column(
            "demotion_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("employee_id", sa.UUID(), nullable=False),
        sa.Column(
            "demotion_date",
            sa.Date(),
            nullable=False,
            comment="Effective date of the demotion",
        ),
        sa.Column(
            "reason",
            demotion_reason,
            nullable=False,
            comment="Primary reason for demotion",
        ),
        sa.Column(
            "reason_details",
            sa.Text(),
            nullable=True,
            comment="Detailed explanation of the demotion reason",
        ),
        sa.Column(
            "discipline_case_id",
            sa.UUID(),
            nullable=True,
            comment="Link to disciplinary case if demotion is disciplinary action",
        ),
        sa.Column(
            "status",
            demotion_status,
            server_default="DRAFT",
            nullable=False,
            comment="Current status in approval workflow",
        ),
        sa.Column(
            "requested_by_id",
            sa.UUID(),
            nullable=True,
            comment="Person who requested/initiated the demotion",
        ),
        sa.Column(
            "approved_by_id",
            sa.UUID(),
            nullable=True,
            comment="Person who approved the demotion",
        ),
        sa.Column(
            "approved_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the demotion was approved",
        ),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the changes were applied to employee record",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        # Audit mixin columns
        sa.Column("created_by_id", sa.UUID(), nullable=True),
        sa.Column("modified_by_id", sa.UUID(), nullable=True),
        # ERPNext sync columns
        sa.Column("erpnext_id", sa.String(140), nullable=True),
        sa.Column("erpnext_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["hr.employee.employee_id"],
        ),
        sa.ForeignKeyConstraint(
            ["discipline_case_id"],
            ["hr.disciplinary_case.case_id"],
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["public.people.id"],
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_id"],
            ["public.people.id"],
        ),
        sa.PrimaryKeyConstraint("demotion_id"),
        schema="hr",
    )

    # Create indexes
    op.create_index(
        "idx_demotion_employee",
        "employee_demotion",
        ["organization_id", "employee_id"],
        schema="hr",
    )
    op.create_index(
        "idx_demotion_status",
        "employee_demotion",
        ["organization_id", "status"],
        schema="hr",
    )
    op.create_index(
        "idx_demotion_discipline_case",
        "employee_demotion",
        ["discipline_case_id"],
        schema="hr",
    )

    # Create employee_demotion_detail table
    op.create_table(
        "employee_demotion_detail",
        sa.Column(
            "detail_id",
            sa.UUID(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("demotion_id", sa.UUID(), nullable=False),
        sa.Column(
            "property_name",
            sa.String(100),
            nullable=False,
            comment="Name of the property changed (e.g., designation, grade, salary)",
        ),
        sa.Column(
            "current_value",
            sa.String(255),
            nullable=True,
            comment="Value before demotion",
        ),
        sa.Column(
            "new_value",
            sa.String(255),
            nullable=True,
            comment="Value after demotion",
        ),
        sa.Column("sequence", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["demotion_id"],
            ["hr.employee_demotion.demotion_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("detail_id"),
        schema="hr",
    )

    # Create index for detail table
    op.create_index(
        "idx_demotion_detail_demotion",
        "employee_demotion_detail",
        ["demotion_id"],
        schema="hr",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_demotion_detail_demotion",
        table_name="employee_demotion_detail",
        schema="hr",
    )
    op.drop_table("employee_demotion_detail", schema="hr")

    op.drop_index("idx_demotion_discipline_case", table_name="employee_demotion", schema="hr")
    op.drop_index("idx_demotion_status", table_name="employee_demotion", schema="hr")
    op.drop_index("idx_demotion_employee", table_name="employee_demotion", schema="hr")
    op.drop_table("employee_demotion", schema="hr")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS demotion_status")
    op.execute("DROP TYPE IF EXISTS demotion_reason")
