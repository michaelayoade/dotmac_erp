"""add assets checklist workflow tasks

Revision ID: 20250212_add_assets_checklists_workflow_tasks
Revises: 20250212_add_hr_lifecycle_tables
Create Date: 2025-02-12 00:00:00.000000
"""
from alembic import op
from app.alembic_utils import ensure_enum
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20250212_add_assets_checklists_workflow_tasks"
down_revision = "20250212_add_hr_lifecycle_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    ensure_enum(
        bind,
        "asset_assignment_status",
        "ISSUED",
        "RETURNED",
        "TRANSFERRED",
        "LOST",
        schema="hr",
    )
    ensure_enum(
        bind,
        "asset_condition",
        "NEW",
        "GOOD",
        "FAIR",
        "POOR",
        "DAMAGED",
        schema="hr",
    )
    ensure_enum(
        bind,
        "checklist_template_type",
        "ONBOARDING",
        "SEPARATION",
        schema="hr",
    )
    ensure_enum(
        bind,
        "workflow_task_status",
        "PENDING",
        "IN_PROGRESS",
        "COMPLETED",
        "CANCELLED",
        "EXPIRED",
        schema="hr",
    )
    ensure_enum(
        bind,
        "workflow_task_priority",
        "LOW",
        "MEDIUM",
        "HIGH",
        "URGENT",
        schema="hr",
    )

    op.create_table(
        "asset_assignment",
        sa.Column(
            "assignment_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column(
            "asset_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("fa.asset.asset_id"),
            nullable=False,
        ),
        sa.Column(
            "employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee.employee_id"),
            nullable=False,
        ),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("expected_return_date", sa.Date(), nullable=True),
        sa.Column("returned_on", sa.Date(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(
                "ISSUED",
                "RETURNED",
                "TRANSFERRED",
                "LOST",
                name="asset_assignment_status",
                schema="hr",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "condition_on_issue",
            postgresql.ENUM(
                "NEW",
                "GOOD",
                "FAIR",
                "POOR",
                "DAMAGED",
                name="asset_condition",
                schema="hr",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "condition_on_return",
            postgresql.ENUM(
                "NEW",
                "GOOD",
                "FAIR",
                "POOR",
                "DAMAGED",
                name="asset_condition",
                schema="hr",
                create_type=False,
            ),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "transfer_from_assignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.asset_assignment.assignment_id"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        schema="hr",
    )
    op.create_index(
        "idx_asset_assignment_asset",
        "asset_assignment",
        ["organization_id", "asset_id"],
        schema="hr",
    )
    op.create_index(
        "idx_asset_assignment_employee",
        "asset_assignment",
        ["organization_id", "employee_id"],
        schema="hr",
    )
    op.create_index(
        "idx_asset_assignment_status",
        "asset_assignment",
        ["organization_id", "status"],
        schema="hr",
    )

    op.create_table(
        "checklist_template",
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column("template_code", sa.String(length=30), nullable=False),
        sa.Column("template_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "template_type",
            postgresql.ENUM(
                "ONBOARDING",
                "SEPARATION",
                name="checklist_template_type",
                schema="hr",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("people.id"),
            nullable=True,
        ),
        schema="hr",
    )
    op.create_index(
        "idx_checklist_template_type",
        "checklist_template",
        ["organization_id", "template_type"],
        schema="hr",
    )

    op.create_table(
        "checklist_template_item",
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.checklist_template.template_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_name", sa.String(length=500), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sequence", sa.Integer(), nullable=True),
        schema="hr",
    )
    op.create_index(
        "idx_checklist_template_item_template",
        "checklist_template_item",
        ["template_id"],
        schema="hr",
    )

    op.create_table(
        "workflow_task",
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("core_org.organization.organization_id"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("module", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("action_url", sa.String(length=500), nullable=True),
        sa.Column(
            "assignee_employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee.employee_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "PENDING",
                "IN_PROGRESS",
                "COMPLETED",
                "CANCELLED",
                "EXPIRED",
                name="workflow_task_status",
                schema="hr",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "priority",
            postgresql.ENUM(
                "LOW",
                "MEDIUM",
                "HIGH",
                "URGENT",
                name="workflow_task_priority",
                schema="hr",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_workflow_task_assignee",
        "workflow_task",
        ["organization_id", "assignee_employee_id"],
    )
    op.create_index(
        "idx_workflow_task_status",
        "workflow_task",
        ["organization_id", "status"],
    )
    op.create_index(
        "idx_workflow_task_module",
        "workflow_task",
        ["organization_id", "module"],
    )


def downgrade() -> None:
    op.drop_index("idx_workflow_task_module", table_name="workflow_task")
    op.drop_index("idx_workflow_task_status", table_name="workflow_task")
    op.drop_index("idx_workflow_task_assignee", table_name="workflow_task")
    op.drop_table("workflow_task")

    op.drop_index("idx_checklist_template_item_template", table_name="checklist_template_item", schema="hr")
    op.drop_table("checklist_template_item", schema="hr")
    op.drop_index("idx_checklist_template_type", table_name="checklist_template", schema="hr")
    op.drop_table("checklist_template", schema="hr")

    op.drop_index("idx_asset_assignment_status", table_name="asset_assignment", schema="hr")
    op.drop_index("idx_asset_assignment_employee", table_name="asset_assignment", schema="hr")
    op.drop_index("idx_asset_assignment_asset", table_name="asset_assignment", schema="hr")
    op.drop_table("asset_assignment", schema="hr")

    op.execute("DROP TYPE IF EXISTS hr.workflow_task_priority")
    op.execute("DROP TYPE IF EXISTS hr.workflow_task_status")
    op.execute("DROP TYPE IF EXISTS hr.checklist_template_type")
    op.execute("DROP TYPE IF EXISTS hr.asset_condition")
    op.execute("DROP TYPE IF EXISTS hr.asset_assignment_status")
