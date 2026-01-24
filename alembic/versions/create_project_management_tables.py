"""Create Project Management schema and tables.

Revision ID: create_project_management_tables
Revises: create_support_schema
Create Date: 2026-01-24

This migration creates:
- pm schema for project management module
- Enums: task_status, task_priority, milestone_status, dependency_type, billing_status
- Tables: task, task_dependency, milestone, resource_allocation, time_entry
- Adds new fields to core_org.project (percent_complete, estimated_cost, actual_cost, etc.)
"""
from alembic import op
from app.alembic_utils import ensure_enum

revision = "create_project_management_tables"
down_revision = "20260123_add_expense_limit_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create pm schema
    op.execute("CREATE SCHEMA IF NOT EXISTS pm")

    # Create enums
    bind = op.get_bind()

    ensure_enum(
        bind,
        "task_status",
        "OPEN",
        "IN_PROGRESS",
        "PENDING_REVIEW",
        "COMPLETED",
        "CANCELLED",
        "ON_HOLD",
        schema="pm",
    )

    ensure_enum(
        bind,
        "task_priority",
        "LOW",
        "MEDIUM",
        "HIGH",
        "URGENT",
        schema="pm",
    )

    ensure_enum(
        bind,
        "milestone_status",
        "PENDING",
        "ACHIEVED",
        "MISSED",
        "CANCELLED",
        schema="pm",
    )

    ensure_enum(
        bind,
        "dependency_type",
        "FINISH_TO_START",
        "START_TO_START",
        "FINISH_TO_FINISH",
        "START_TO_FINISH",
        schema="pm",
    )

    ensure_enum(
        bind,
        "billing_status",
        "NOT_BILLED",
        "BILLED",
        "NON_BILLABLE",
        schema="pm",
    )

    ensure_enum(
        bind,
        "project_type",
        "INTERNAL",
        "CLIENT",
        "FIXED_PRICE",
        "TIME_MATERIAL",
        schema="pm",
    )

    ensure_enum(
        bind,
        "project_priority",
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
        schema="pm",
    )

    # -------------------------------------------------------------------------
    # Add new columns to core_org.project
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE core_org.project
        ADD COLUMN IF NOT EXISTS percent_complete NUMERIC(5,2) DEFAULT 0.00,
        ADD COLUMN IF NOT EXISTS estimated_cost NUMERIC(20,6),
        ADD COLUMN IF NOT EXISTS actual_cost NUMERIC(20,6),
        ADD COLUMN IF NOT EXISTS cost_center_id UUID REFERENCES core_org.cost_center(cost_center_id),
        ADD COLUMN IF NOT EXISTS project_priority pm.project_priority DEFAULT 'MEDIUM',
        ADD COLUMN IF NOT EXISTS project_type pm.project_type DEFAULT 'INTERNAL';

        CREATE INDEX IF NOT EXISTS idx_project_cost_center
        ON core_org.project(cost_center_id);

        COMMENT ON COLUMN core_org.project.percent_complete IS 'Overall project completion percentage (0-100)';
        COMMENT ON COLUMN core_org.project.estimated_cost IS 'Estimated total project cost';
        COMMENT ON COLUMN core_org.project.actual_cost IS 'Actual accumulated project cost';
        COMMENT ON COLUMN core_org.project.cost_center_id IS 'Default cost center for project expenses';
        COMMENT ON COLUMN core_org.project.project_priority IS 'Project priority level';
        COMMENT ON COLUMN core_org.project.project_type IS 'Type of project (internal, client, fixed-price, T&M)';
    """)

    # -------------------------------------------------------------------------
    # Create pm.task table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE pm.task (
            task_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            project_id UUID NOT NULL REFERENCES core_org.project(project_id),
            parent_task_id UUID REFERENCES pm.task(task_id),
            task_code VARCHAR(30) NOT NULL,
            task_name VARCHAR(200) NOT NULL,
            description TEXT,
            status pm.task_status NOT NULL DEFAULT 'OPEN',
            priority pm.task_priority NOT NULL DEFAULT 'MEDIUM',
            assigned_to_id UUID REFERENCES hr.employee(employee_id),
            start_date DATE,
            due_date DATE,
            actual_start_date DATE,
            actual_end_date DATE,
            estimated_hours NUMERIC(10,2),
            actual_hours NUMERIC(10,2) DEFAULT 0.00,
            progress_percent INTEGER DEFAULT 0 CHECK (progress_percent >= 0 AND progress_percent <= 100),
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            deleted_at TIMESTAMPTZ,
            deleted_by_id UUID REFERENCES public.people(id),
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            erpnext_id VARCHAR(255),
            last_synced_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_task_org_code UNIQUE (organization_id, task_code)
        );

        CREATE INDEX idx_task_org ON pm.task(organization_id);
        CREATE INDEX idx_task_project ON pm.task(project_id);
        CREATE INDEX idx_task_parent ON pm.task(parent_task_id);
        CREATE INDEX idx_task_status ON pm.task(status);
        CREATE INDEX idx_task_priority ON pm.task(priority);
        CREATE INDEX idx_task_assigned_to ON pm.task(assigned_to_id);
        CREATE INDEX idx_task_due_date ON pm.task(due_date);
        CREATE INDEX idx_task_erpnext ON pm.task(erpnext_id);
        CREATE INDEX idx_task_deleted ON pm.task(is_deleted) WHERE is_deleted = FALSE;

        COMMENT ON TABLE pm.task IS 'Project tasks/work items with hierarchy and status tracking';
        COMMENT ON COLUMN pm.task.task_code IS 'Unique task code per organization (auto-generated or from ERPNext)';
        COMMENT ON COLUMN pm.task.parent_task_id IS 'Parent task for hierarchy (null for top-level tasks)';
        COMMENT ON COLUMN pm.task.progress_percent IS 'Task completion percentage (0-100)';
        COMMENT ON COLUMN pm.task.erpnext_id IS 'ERPNext Task name for sync';
    """)

    # -------------------------------------------------------------------------
    # Create pm.task_dependency table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE pm.task_dependency (
            dependency_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            task_id UUID NOT NULL REFERENCES pm.task(task_id) ON DELETE CASCADE,
            depends_on_task_id UUID NOT NULL REFERENCES pm.task(task_id) ON DELETE CASCADE,
            dependency_type pm.dependency_type NOT NULL DEFAULT 'FINISH_TO_START',
            lag_days INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_task_dependency UNIQUE (task_id, depends_on_task_id),
            CONSTRAINT chk_task_dependency_self CHECK (task_id != depends_on_task_id)
        );

        CREATE INDEX idx_task_dependency_task ON pm.task_dependency(task_id);
        CREATE INDEX idx_task_dependency_depends_on ON pm.task_dependency(depends_on_task_id);

        COMMENT ON TABLE pm.task_dependency IS 'Task dependencies for scheduling and Gantt charts';
        COMMENT ON COLUMN pm.task_dependency.dependency_type IS 'Type of dependency (FS, SS, FF, SF)';
        COMMENT ON COLUMN pm.task_dependency.lag_days IS 'Days of lag between tasks (can be negative for lead time)';
    """)

    # -------------------------------------------------------------------------
    # Create pm.milestone table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE pm.milestone (
            milestone_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            project_id UUID NOT NULL REFERENCES core_org.project(project_id),
            milestone_code VARCHAR(30) NOT NULL,
            milestone_name VARCHAR(200) NOT NULL,
            description TEXT,
            target_date DATE NOT NULL,
            actual_date DATE,
            status pm.milestone_status NOT NULL DEFAULT 'PENDING',
            linked_task_id UUID REFERENCES pm.task(task_id),
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            erpnext_id VARCHAR(255),
            last_synced_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_milestone_org_code UNIQUE (organization_id, milestone_code)
        );

        CREATE INDEX idx_milestone_org ON pm.milestone(organization_id);
        CREATE INDEX idx_milestone_project ON pm.milestone(project_id);
        CREATE INDEX idx_milestone_status ON pm.milestone(status);
        CREATE INDEX idx_milestone_target_date ON pm.milestone(target_date);
        CREATE INDEX idx_milestone_linked_task ON pm.milestone(linked_task_id);
        CREATE INDEX idx_milestone_erpnext ON pm.milestone(erpnext_id);

        COMMENT ON TABLE pm.milestone IS 'Project milestones/phases with target and actual dates';
        COMMENT ON COLUMN pm.milestone.linked_task_id IS 'Optional task that represents this milestone completion';
    """)

    # -------------------------------------------------------------------------
    # Create pm.resource_allocation table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE pm.resource_allocation (
            allocation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            project_id UUID NOT NULL REFERENCES core_org.project(project_id),
            employee_id UUID NOT NULL REFERENCES hr.employee(employee_id),
            role_on_project VARCHAR(100),
            allocation_percent NUMERIC(5,2) NOT NULL CHECK (allocation_percent >= 0 AND allocation_percent <= 100),
            start_date DATE NOT NULL,
            end_date DATE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            cost_rate_per_hour NUMERIC(12,2),
            billing_rate_per_hour NUMERIC(12,2),
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_resource_allocation UNIQUE (project_id, employee_id, start_date)
        );

        CREATE INDEX idx_resource_allocation_org ON pm.resource_allocation(organization_id);
        CREATE INDEX idx_resource_allocation_project ON pm.resource_allocation(project_id);
        CREATE INDEX idx_resource_allocation_employee ON pm.resource_allocation(employee_id);
        CREATE INDEX idx_resource_allocation_active ON pm.resource_allocation(is_active) WHERE is_active = TRUE;
        CREATE INDEX idx_resource_allocation_dates ON pm.resource_allocation(start_date, end_date);

        COMMENT ON TABLE pm.resource_allocation IS 'Resource allocation to projects with utilization tracking';
        COMMENT ON COLUMN pm.resource_allocation.allocation_percent IS 'Percentage of employee time allocated to this project (0-100)';
        COMMENT ON COLUMN pm.resource_allocation.role_on_project IS 'Employee role/position on this specific project';
    """)

    # -------------------------------------------------------------------------
    # Create pm.time_entry table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE pm.time_entry (
            entry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            project_id UUID NOT NULL REFERENCES core_org.project(project_id),
            task_id UUID REFERENCES pm.task(task_id),
            employee_id UUID NOT NULL REFERENCES hr.employee(employee_id),
            entry_date DATE NOT NULL,
            hours NUMERIC(6,2) NOT NULL CHECK (hours > 0 AND hours <= 24),
            description TEXT,
            is_billable BOOLEAN NOT NULL DEFAULT TRUE,
            billing_status pm.billing_status NOT NULL DEFAULT 'NOT_BILLED',
            erpnext_timesheet_id VARCHAR(255),
            erpnext_timesheet_detail_id VARCHAR(255),
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            last_synced_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX idx_time_entry_org ON pm.time_entry(organization_id);
        CREATE INDEX idx_time_entry_project ON pm.time_entry(project_id);
        CREATE INDEX idx_time_entry_task ON pm.time_entry(task_id);
        CREATE INDEX idx_time_entry_employee ON pm.time_entry(employee_id);
        CREATE INDEX idx_time_entry_date ON pm.time_entry(entry_date);
        CREATE INDEX idx_time_entry_billable ON pm.time_entry(is_billable, billing_status);
        CREATE INDEX idx_time_entry_erpnext ON pm.time_entry(erpnext_timesheet_id);
        CREATE INDEX idx_time_entry_emp_date ON pm.time_entry(employee_id, entry_date);

        COMMENT ON TABLE pm.time_entry IS 'Time entries for project time tracking and billing';
        COMMENT ON COLUMN pm.time_entry.erpnext_timesheet_id IS 'ERPNext Timesheet parent name';
        COMMENT ON COLUMN pm.time_entry.erpnext_timesheet_detail_id IS 'ERPNext Timesheet Detail child row name';
    """)


def downgrade() -> None:
    # Drop tables in reverse order (respect foreign key dependencies)
    op.execute("DROP TABLE IF EXISTS pm.time_entry CASCADE")
    op.execute("DROP TABLE IF EXISTS pm.resource_allocation CASCADE")
    op.execute("DROP TABLE IF EXISTS pm.milestone CASCADE")
    op.execute("DROP TABLE IF EXISTS pm.task_dependency CASCADE")
    op.execute("DROP TABLE IF EXISTS pm.task CASCADE")

    # Remove columns from core_org.project
    op.execute("""
        ALTER TABLE core_org.project
        DROP COLUMN IF EXISTS percent_complete,
        DROP COLUMN IF EXISTS estimated_cost,
        DROP COLUMN IF EXISTS actual_cost,
        DROP COLUMN IF EXISTS cost_center_id,
        DROP COLUMN IF EXISTS project_priority,
        DROP COLUMN IF EXISTS project_type;
    """)

    # Drop enums
    op.execute("DROP TYPE IF EXISTS pm.billing_status CASCADE")
    op.execute("DROP TYPE IF EXISTS pm.dependency_type CASCADE")
    op.execute("DROP TYPE IF EXISTS pm.milestone_status CASCADE")
    op.execute("DROP TYPE IF EXISTS pm.task_priority CASCADE")
    op.execute("DROP TYPE IF EXISTS pm.task_status CASCADE")
    op.execute("DROP TYPE IF EXISTS pm.project_priority CASCADE")
    op.execute("DROP TYPE IF EXISTS pm.project_type CASCADE")

    # Drop schema
    op.execute("DROP SCHEMA IF EXISTS pm CASCADE")
