"""Create Support schema and Ticket table.

Revision ID: create_support_schema
Revises: 20250212_add_assets_checklists_workflow_tasks
Create Date: 2026-01-23

This migration creates:
- support schema for helpdesk/ticket tracking
- support.ticket table for syncing ERPNext Issue/HD Ticket
"""
from alembic import op
from app.alembic_utils import ensure_enum

revision = "create_support_schema"
down_revision = "20250212_add_assets_checklists_workflow_tasks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create support schema
    op.execute("CREATE SCHEMA IF NOT EXISTS support")

    # Create enums
    bind = op.get_bind()
    ensure_enum(
        bind,
        "ticket_status",
        "OPEN",
        "REPLIED",
        "ON_HOLD",
        "RESOLVED",
        "CLOSED",
        schema="support",
    )
    ensure_enum(
        bind,
        "ticket_priority",
        "LOW",
        "MEDIUM",
        "HIGH",
        "URGENT",
        schema="support",
    )

    # Create ticket table
    op.execute("""
        CREATE TABLE support.ticket (
            ticket_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            ticket_number VARCHAR(50) NOT NULL,
            subject VARCHAR(255) NOT NULL,
            description TEXT,
            status support.ticket_status NOT NULL DEFAULT 'OPEN',
            priority support.ticket_priority NOT NULL DEFAULT 'MEDIUM',
            raised_by_id UUID REFERENCES hr.employee(employee_id),
            raised_by_email VARCHAR(255),
            assigned_to_id UUID REFERENCES hr.employee(employee_id),
            project_id UUID REFERENCES core_org.project(project_id),
            resolution TEXT,
            opening_date DATE NOT NULL DEFAULT CURRENT_DATE,
            resolution_date DATE,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            erpnext_id VARCHAR(255),
            last_synced_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_ticket_org_number UNIQUE (organization_id, ticket_number)
        );

        CREATE INDEX idx_ticket_org ON support.ticket(organization_id);
        CREATE INDEX idx_ticket_number ON support.ticket(ticket_number);
        CREATE INDEX idx_ticket_status ON support.ticket(status);
        CREATE INDEX idx_ticket_erpnext ON support.ticket(erpnext_id);
        CREATE INDEX idx_ticket_project ON support.ticket(project_id);
        CREATE INDEX idx_ticket_raised_by ON support.ticket(raised_by_id);
        CREATE INDEX idx_ticket_assigned_to ON support.ticket(assigned_to_id);
        CREATE INDEX idx_ticket_opening_date ON support.ticket(opening_date);

        COMMENT ON TABLE support.ticket IS 'Support tickets synced from ERPNext Issue/HD Ticket';
        COMMENT ON COLUMN support.ticket.ticket_number IS 'Unique ticket number (ERPNext Issue name)';
        COMMENT ON COLUMN support.ticket.raised_by_email IS 'Email of person who raised ticket (for lookup before employee sync)';
        COMMENT ON COLUMN support.ticket.erpnext_id IS 'ERPNext document name for migration/sync';
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS support.ticket CASCADE")
    op.execute("DROP TYPE IF EXISTS support.ticket_priority CASCADE")
    op.execute("DROP TYPE IF EXISTS support.ticket_status CASCADE")
    op.execute("DROP SCHEMA IF EXISTS support CASCADE")
