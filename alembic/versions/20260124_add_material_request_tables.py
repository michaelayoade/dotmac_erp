"""Add Material Request tables to inventory schema.

Creates tables for Material Request sync from ERPNext:
- inv.material_request (header)
- inv.material_request_item (line items with project/ticket/task links)

Revision ID: 20260124_material_request
Revises: None
Create Date: 2026-01-24
"""
from alembic import op
from app.alembic_utils import ensure_enum

revision = "20260124_material_request"
down_revision = "create_support_schema"  # Fixed: connect to initial schema
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create material request tables and enums."""
    bind = op.get_bind()

    # Create enums in inv schema
    ensure_enum(
        bind,
        "material_request_type",
        "PURCHASE",
        "TRANSFER",
        "ISSUE",
        "MANUFACTURE",
        schema="inv",
    )

    ensure_enum(
        bind,
        "material_request_status",
        "DRAFT",
        "SUBMITTED",
        "PARTIALLY_ORDERED",
        "ORDERED",
        "ISSUED",
        "TRANSFERRED",
        "CANCELLED",
        schema="inv",
    )

    # -------------------------------------------------------------------------
    # Create inv.material_request (header) table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE inv.material_request (
            request_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            request_number VARCHAR(50) NOT NULL,
            request_type inv.material_request_type NOT NULL DEFAULT 'PURCHASE',
            status inv.material_request_status NOT NULL DEFAULT 'DRAFT',
            schedule_date DATE,
            requested_by_id UUID REFERENCES hr.employee(employee_id),
            default_warehouse_id UUID REFERENCES inv.warehouse(warehouse_id),
            remarks TEXT,
            -- ERPNext sync fields
            erpnext_id VARCHAR(255),
            last_synced_at TIMESTAMPTZ,
            -- Audit fields
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            created_by_id UUID REFERENCES public.people(id),
            updated_by_id UUID REFERENCES public.people(id),
            CONSTRAINT uq_material_request_org_number UNIQUE (organization_id, request_number)
        );

        CREATE INDEX idx_material_request_org ON inv.material_request(organization_id);
        CREATE INDEX idx_material_request_status ON inv.material_request(status);
        CREATE INDEX idx_material_request_type ON inv.material_request(request_type);
        CREATE INDEX idx_material_request_schedule_date ON inv.material_request(schedule_date);
        CREATE INDEX idx_material_request_requested_by ON inv.material_request(requested_by_id);
        CREATE INDEX idx_material_request_erpnext ON inv.material_request(erpnext_id);

        COMMENT ON TABLE inv.material_request IS 'Material Request header - inventory requisitions from ERPNext';
        COMMENT ON COLUMN inv.material_request.request_number IS 'Unique request number per organization (e.g., MAT-REQ-00001)';
        COMMENT ON COLUMN inv.material_request.request_type IS 'Type of request: PURCHASE (buy), TRANSFER (move), ISSUE (consume), MANUFACTURE (produce)';
        COMMENT ON COLUMN inv.material_request.status IS 'Request status tracking workflow progression';
        COMMENT ON COLUMN inv.material_request.schedule_date IS 'Required-by date for the request';
        COMMENT ON COLUMN inv.material_request.requested_by_id IS 'Employee who created the request';
        COMMENT ON COLUMN inv.material_request.default_warehouse_id IS 'Default target warehouse for items';
        COMMENT ON COLUMN inv.material_request.erpnext_id IS 'ERPNext Material Request name for sync tracking';
    """)

    # -------------------------------------------------------------------------
    # Create inv.material_request_item (line items) table
    # -------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE inv.material_request_item (
            item_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL REFERENCES core_org.organization(organization_id),
            request_id UUID NOT NULL REFERENCES inv.material_request(request_id) ON DELETE CASCADE,
            inventory_item_id UUID NOT NULL REFERENCES inv.item(item_id),
            warehouse_id UUID REFERENCES inv.warehouse(warehouse_id),
            requested_qty NUMERIC(20,6) NOT NULL,
            ordered_qty NUMERIC(20,6) NOT NULL DEFAULT 0,
            uom VARCHAR(20),
            schedule_date DATE,
            -- Cross-module links for inventory-to-project/support integration
            project_id UUID REFERENCES core_org.project(project_id),
            ticket_id UUID REFERENCES support.ticket(ticket_id),
            task_id UUID REFERENCES pm.task(task_id),
            -- Ordering and sync
            sequence INTEGER NOT NULL DEFAULT 1,
            erpnext_id VARCHAR(255),
            last_synced_at TIMESTAMPTZ,
            -- Audit
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ
        );

        CREATE INDEX idx_mri_org ON inv.material_request_item(organization_id);
        CREATE INDEX idx_mri_request ON inv.material_request_item(request_id);
        CREATE INDEX idx_mri_item ON inv.material_request_item(inventory_item_id);
        CREATE INDEX idx_mri_warehouse ON inv.material_request_item(warehouse_id);
        CREATE INDEX idx_mri_project ON inv.material_request_item(project_id);
        CREATE INDEX idx_mri_ticket ON inv.material_request_item(ticket_id);
        CREATE INDEX idx_mri_task ON inv.material_request_item(task_id);
        CREATE INDEX idx_mri_erpnext ON inv.material_request_item(erpnext_id);

        COMMENT ON TABLE inv.material_request_item IS 'Material Request line items with quantities and cross-module links';
        COMMENT ON COLUMN inv.material_request_item.inventory_item_id IS 'Reference to inv.item - the requested inventory item';
        COMMENT ON COLUMN inv.material_request_item.requested_qty IS 'Quantity requested';
        COMMENT ON COLUMN inv.material_request_item.ordered_qty IS 'Quantity already ordered/fulfilled (for tracking partial fulfillment)';
        COMMENT ON COLUMN inv.material_request_item.project_id IS 'Optional link to project for project-based inventory requests';
        COMMENT ON COLUMN inv.material_request_item.ticket_id IS 'Optional link to support ticket for support-related inventory';
        COMMENT ON COLUMN inv.material_request_item.task_id IS 'Optional link to task for task-specific inventory requirements';
        COMMENT ON COLUMN inv.material_request_item.sequence IS 'Line item ordering within the request';
    """)


def downgrade() -> None:
    """Remove material request tables and enums."""
    # Drop tables in reverse order (respect foreign key dependencies)
    op.execute("DROP TABLE IF EXISTS inv.material_request_item CASCADE")
    op.execute("DROP TABLE IF EXISTS inv.material_request CASCADE")

    # Drop enums
    op.execute("DROP TYPE IF EXISTS inv.material_request_status CASCADE")
    op.execute("DROP TYPE IF EXISTS inv.material_request_type CASCADE")
