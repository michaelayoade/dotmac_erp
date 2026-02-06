"""Add customer relationships to Project and Ticket.

Revision ID: 20260124_add_customer_relationships
Revises: create_project_management_tables
Create Date: 2026-01-24

This migration adds:
- customer_id FK to core_org.project for client projects
- customer_id FK to support.ticket for customer support tickets
- Additional ticket fields: category_id, team_id, is_deleted
- Creates supporting tables: ticket_category, support_team, ticket_comment, ticket_attachment
"""

from alembic import op

revision = "20260124_add_customer_relationships"
down_revision = "create_project_management_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Add customer_id to core_org.project
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE core_org.project
        ADD COLUMN IF NOT EXISTS customer_id UUID REFERENCES ar.customer(customer_id);

        CREATE INDEX IF NOT EXISTS idx_project_customer
        ON core_org.project(customer_id);

        COMMENT ON COLUMN core_org.project.customer_id IS 'Customer for client projects (synced from ERPNext)';
    """)

    # -------------------------------------------------------------------------
    # Create support_team table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS support.support_team (
            team_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            team_name VARCHAR(100) NOT NULL,
            description TEXT,
            lead_employee_id UUID REFERENCES hr.employee(employee_id),
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_support_team_org_name UNIQUE (organization_id, team_name)
        );

        CREATE INDEX IF NOT EXISTS idx_support_team_org
        ON support.support_team(organization_id);

        COMMENT ON TABLE support.support_team IS 'Support teams for ticket assignment';
    """)

    # -------------------------------------------------------------------------
    # Create ticket_category table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS support.ticket_category (
            category_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            category_name VARCHAR(100) NOT NULL,
            description TEXT,
            parent_category_id UUID REFERENCES support.ticket_category(category_id),
            default_team_id UUID REFERENCES support.support_team(team_id),
            default_priority support.ticket_priority DEFAULT 'MEDIUM',
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            CONSTRAINT uq_ticket_category_org_name UNIQUE (organization_id, category_name)
        );

        CREATE INDEX IF NOT EXISTS idx_ticket_category_org
        ON support.ticket_category(organization_id);

        COMMENT ON TABLE support.ticket_category IS 'Categories for classifying support tickets';
    """)

    # -------------------------------------------------------------------------
    # Add new columns to support.ticket
    # -------------------------------------------------------------------------
    op.execute("""
        ALTER TABLE support.ticket
        ADD COLUMN IF NOT EXISTS customer_id UUID REFERENCES ar.customer(customer_id),
        ADD COLUMN IF NOT EXISTS category_id UUID REFERENCES support.ticket_category(category_id),
        ADD COLUMN IF NOT EXISTS team_id UUID REFERENCES support.support_team(team_id),
        ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT false;

        CREATE INDEX IF NOT EXISTS idx_ticket_customer
        ON support.ticket(customer_id);

        CREATE INDEX IF NOT EXISTS idx_ticket_category
        ON support.ticket(category_id);

        CREATE INDEX IF NOT EXISTS idx_ticket_team
        ON support.ticket(team_id);

        CREATE INDEX IF NOT EXISTS idx_ticket_is_deleted
        ON support.ticket(is_deleted);

        COMMENT ON COLUMN support.ticket.customer_id IS 'Customer linked to this ticket (synced from ERPNext)';
        COMMENT ON COLUMN support.ticket.category_id IS 'Ticket category/type';
        COMMENT ON COLUMN support.ticket.team_id IS 'Support team assigned to this ticket';
    """)

    # -------------------------------------------------------------------------
    # Create ticket_comment table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS support.ticket_comment (
            comment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ticket_id UUID NOT NULL REFERENCES support.ticket(ticket_id) ON DELETE CASCADE,
            author_id UUID REFERENCES hr.employee(employee_id),
            author_email VARCHAR(255),
            content TEXT NOT NULL,
            is_internal BOOLEAN NOT NULL DEFAULT false,
            erpnext_id VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX IF NOT EXISTS idx_ticket_comment_ticket
        ON support.ticket_comment(ticket_id);

        CREATE INDEX IF NOT EXISTS idx_ticket_comment_created
        ON support.ticket_comment(created_at);

        COMMENT ON TABLE support.ticket_comment IS 'Comments/communications on support tickets';
        COMMENT ON COLUMN support.ticket_comment.is_internal IS 'Whether this is an internal note not visible to customer';
    """)

    # -------------------------------------------------------------------------
    # Create ticket_attachment table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS support.ticket_attachment (
            attachment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ticket_id UUID NOT NULL REFERENCES support.ticket(ticket_id) ON DELETE CASCADE,
            file_name VARCHAR(255) NOT NULL,
            file_path VARCHAR(512) NOT NULL,
            file_size INTEGER,
            mime_type VARCHAR(100),
            uploaded_by_id UUID REFERENCES hr.employee(employee_id),
            erpnext_id VARCHAR(255),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_ticket_attachment_ticket
        ON support.ticket_attachment(ticket_id);

        COMMENT ON TABLE support.ticket_attachment IS 'File attachments on support tickets';
    """)


def downgrade() -> None:
    # Drop tables in reverse order of creation
    op.execute("DROP TABLE IF EXISTS support.ticket_attachment CASCADE")
    op.execute("DROP TABLE IF EXISTS support.ticket_comment CASCADE")

    # Remove columns from support.ticket
    op.execute("""
        ALTER TABLE support.ticket
        DROP COLUMN IF EXISTS customer_id,
        DROP COLUMN IF EXISTS category_id,
        DROP COLUMN IF EXISTS team_id,
        DROP COLUMN IF EXISTS is_deleted;
    """)

    # Drop category and team tables
    op.execute("DROP TABLE IF EXISTS support.ticket_category CASCADE")
    op.execute("DROP TABLE IF EXISTS support.support_team CASCADE")

    # Remove column from core_org.project
    op.execute("""
        ALTER TABLE core_org.project
        DROP COLUMN IF EXISTS customer_id;
    """)
