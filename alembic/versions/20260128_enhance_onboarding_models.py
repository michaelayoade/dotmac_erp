"""Enhance onboarding models with self-service and tracking fields.

Revision ID: 20260128_enhance_onboarding
Revises:
Create Date: 2026-01-28

Adds fields for:
- ChecklistTemplateItem: category, assignee role, due date calculation, document requirements
- EmployeeOnboarding: self-service portal, progress tracking, buddy assignment
- EmployeeOnboardingActivity: task assignment, due dates, document collection, reminders
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260128_enhance_onboarding"
down_revision = "20250212_add_hr_lifecycle_tables"  # Fixed: connect to initial schema
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ========================================
    # ChecklistTemplateItem enhancements
    # ========================================
    op.add_column(
        "checklist_template_item",
        sa.Column(
            "category",
            sa.String(30),
            nullable=True,
            comment="Task category/phase: PRE_BOARDING, DAY_ONE, FIRST_WEEK, FIRST_MONTH, ONGOING",
        ),
        schema="hr",
    )
    op.add_column(
        "checklist_template_item",
        sa.Column(
            "default_assignee_role",
            sa.String(50),
            nullable=True,
            comment="Default assignee role: HR, MANAGER, IT, FINANCE, EMPLOYEE, BUDDY",
        ),
        schema="hr",
    )
    op.add_column(
        "checklist_template_item",
        sa.Column(
            "days_from_start",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Days after start date when task is due (negative for pre-boarding)",
        ),
        schema="hr",
    )
    op.add_column(
        "checklist_template_item",
        sa.Column(
            "requires_document",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Whether document upload is required to complete this task",
        ),
        schema="hr",
    )
    op.add_column(
        "checklist_template_item",
        sa.Column(
            "document_type",
            sa.String(50),
            nullable=True,
            comment="Expected document type: ID_COPY, PASSPORT, SIGNED_CONTRACT, BANK_DETAILS, etc.",
        ),
        schema="hr",
    )
    op.add_column(
        "checklist_template_item",
        sa.Column(
            "instructions",
            sa.Text(),
            nullable=True,
            comment="Detailed instructions for the assignee",
        ),
        schema="hr",
    )

    # Index for category lookup
    op.create_index(
        "idx_checklist_template_item_category",
        "checklist_template_item",
        ["template_id", "category"],
        schema="hr",
    )

    # ========================================
    # EmployeeOnboarding enhancements
    # ========================================
    op.add_column(
        "employee_onboarding",
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.checklist_template.template_id"),
            nullable=True,
            comment="Checklist template used for this onboarding",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding",
        sa.Column(
            "self_service_token",
            sa.String(100),
            nullable=True,
            comment="Token for new hire self-service portal access",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding",
        sa.Column(
            "self_service_token_expires",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Token expiry timestamp",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding",
        sa.Column(
            "self_service_email_sent",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Whether welcome email with portal link has been sent",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding",
        sa.Column(
            "expected_completion_date",
            sa.Date(),
            nullable=True,
            comment="Target date for completing all onboarding tasks",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding",
        sa.Column(
            "actual_completion_date",
            sa.Date(),
            nullable=True,
            comment="Date when onboarding was marked complete",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding",
        sa.Column(
            "progress_percentage",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="Calculated progress (0-100)",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding",
        sa.Column(
            "buddy_employee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee.employee_id"),
            nullable=True,
            comment="Assigned buddy/mentor for the new employee",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding",
        sa.Column(
            "manager_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.employee.employee_id"),
            nullable=True,
            comment="Direct manager for approvals and notifications",
        ),
        schema="hr",
    )

    # Unique index for self-service token lookup
    op.create_index(
        "idx_onboarding_self_service_token",
        "employee_onboarding",
        ["self_service_token"],
        unique=True,
        schema="hr",
    )

    # ========================================
    # EmployeeOnboardingActivity enhancements
    # ========================================
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "template_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("hr.checklist_template_item.item_id"),
            nullable=True,
            comment="Template item this activity was created from",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "category",
            sa.String(30),
            nullable=True,
            comment="Task category: PRE_BOARDING, DAY_ONE, FIRST_WEEK, FIRST_MONTH, ONGOING",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "due_date",
            sa.Date(),
            nullable=True,
            comment="Task deadline",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "activity_status",
            sa.String(30),
            nullable=True,
            comment="Activity status: PENDING, IN_PROGRESS, AWAITING_DOCUMENT, COMPLETED, SKIPPED, BLOCKED",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "is_overdue",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Whether task is past due date",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "assignee_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.people.id"),
            nullable=True,
            comment="Specific person assigned to this task",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "assigned_to_employee",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="True if this is a self-service task for the new employee",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "requires_document",
            sa.Boolean(),
            nullable=False,
            server_default="false",
            comment="Whether document upload is required",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="FK to uploaded document (if requires_document)",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "completed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("public.people.id"),
            nullable=True,
            comment="Person who completed this task",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "completion_notes",
            sa.Text(),
            nullable=True,
            comment="Notes added when completing the task",
        ),
        schema="hr",
    )
    op.add_column(
        "employee_onboarding_activity",
        sa.Column(
            "reminder_sent_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of last reminder sent",
        ),
        schema="hr",
    )

    # Indexes for activity queries
    op.create_index(
        "idx_onboarding_activity_assignee",
        "employee_onboarding_activity",
        ["assignee_id"],
        schema="hr",
    )
    op.create_index(
        "idx_onboarding_activity_due_date",
        "employee_onboarding_activity",
        ["due_date", "activity_status"],
        schema="hr",
    )


def downgrade() -> None:
    # ========================================
    # EmployeeOnboardingActivity - drop columns
    # ========================================
    op.drop_index(
        "idx_onboarding_activity_due_date",
        table_name="employee_onboarding_activity",
        schema="hr",
    )
    op.drop_index(
        "idx_onboarding_activity_assignee",
        table_name="employee_onboarding_activity",
        schema="hr",
    )

    op.drop_column("employee_onboarding_activity", "reminder_sent_at", schema="hr")
    op.drop_column("employee_onboarding_activity", "completion_notes", schema="hr")
    op.drop_column("employee_onboarding_activity", "completed_by", schema="hr")
    op.drop_column("employee_onboarding_activity", "document_id", schema="hr")
    op.drop_column("employee_onboarding_activity", "requires_document", schema="hr")
    op.drop_column("employee_onboarding_activity", "assigned_to_employee", schema="hr")
    op.drop_column("employee_onboarding_activity", "assignee_id", schema="hr")
    op.drop_column("employee_onboarding_activity", "is_overdue", schema="hr")
    op.drop_column("employee_onboarding_activity", "activity_status", schema="hr")
    op.drop_column("employee_onboarding_activity", "due_date", schema="hr")
    op.drop_column("employee_onboarding_activity", "category", schema="hr")
    op.drop_column("employee_onboarding_activity", "template_item_id", schema="hr")

    # ========================================
    # EmployeeOnboarding - drop columns
    # ========================================
    op.drop_index(
        "idx_onboarding_self_service_token",
        table_name="employee_onboarding",
        schema="hr",
    )

    op.drop_column("employee_onboarding", "manager_id", schema="hr")
    op.drop_column("employee_onboarding", "buddy_employee_id", schema="hr")
    op.drop_column("employee_onboarding", "progress_percentage", schema="hr")
    op.drop_column("employee_onboarding", "actual_completion_date", schema="hr")
    op.drop_column("employee_onboarding", "expected_completion_date", schema="hr")
    op.drop_column("employee_onboarding", "self_service_email_sent", schema="hr")
    op.drop_column("employee_onboarding", "self_service_token_expires", schema="hr")
    op.drop_column("employee_onboarding", "self_service_token", schema="hr")
    op.drop_column("employee_onboarding", "template_id", schema="hr")

    # ========================================
    # ChecklistTemplateItem - drop columns
    # ========================================
    op.drop_index(
        "idx_checklist_template_item_category",
        table_name="checklist_template_item",
        schema="hr",
    )

    op.drop_column("checklist_template_item", "instructions", schema="hr")
    op.drop_column("checklist_template_item", "document_type", schema="hr")
    op.drop_column("checklist_template_item", "requires_document", schema="hr")
    op.drop_column("checklist_template_item", "days_from_start", schema="hr")
    op.drop_column("checklist_template_item", "default_assignee_role", schema="hr")
    op.drop_column("checklist_template_item", "category", schema="hr")
