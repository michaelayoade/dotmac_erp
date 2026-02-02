"""add_phase1_performance_indexes

Add critical performance indexes for payment workflows and cash application.

Phase 1 indexes:
- ar.customer_payment: (organization_id, status) for approval workflows
- ap.supplier_payment: (organization_id, status) for batch processing
- ar.payment_allocation: (payment_id), (invoice_id) for cash application
- ap.payment_allocation: (payment_id), (invoice_id) for AP reconciliation

Revision ID: 20260202_add_phase1_performance_indexes
Revises: 20260202_set_org_timezone_lagos
Create Date: 2026-02-02 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "20260202_add_phase1_performance_indexes"
down_revision = "20260202_set_org_timezone_lagos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- AR Schema Indexes ---

    # ar.customer_payment: composite index on (organization_id, status)
    # Speeds up approval workflow queries and dashboard filtering
    if inspector.has_table("customer_payment", schema="ar"):
        indexes = {idx["name"] for idx in inspector.get_indexes("customer_payment", schema="ar")}
        if "idx_customer_payment_org_status" not in indexes:
            op.create_index(
                "idx_customer_payment_org_status",
                "customer_payment",
                ["organization_id", "status"],
                schema="ar",
            )

    # ar.payment_allocation: index on payment_id
    # Speeds up finding allocations for a payment during cash application
    if inspector.has_table("payment_allocation", schema="ar"):
        indexes = {idx["name"] for idx in inspector.get_indexes("payment_allocation", schema="ar")}
        if "idx_payment_alloc_payment" not in indexes:
            op.create_index(
                "idx_payment_alloc_payment",
                "payment_allocation",
                ["payment_id"],
                schema="ar",
            )

        # ar.payment_allocation: index on invoice_id
        # Speeds up finding allocations for an invoice during reconciliation
        if "idx_payment_alloc_invoice" not in indexes:
            op.create_index(
                "idx_payment_alloc_invoice",
                "payment_allocation",
                ["invoice_id"],
                schema="ar",
            )

    # --- AP Schema Indexes ---

    # ap.supplier_payment: composite index on (organization_id, status)
    # Speeds up batch payment processing and approval workflows
    if inspector.has_table("supplier_payment", schema="ap"):
        indexes = {idx["name"] for idx in inspector.get_indexes("supplier_payment", schema="ap")}
        if "idx_supplier_payment_org_status" not in indexes:
            op.create_index(
                "idx_supplier_payment_org_status",
                "supplier_payment",
                ["organization_id", "status"],
                schema="ap",
            )

    # ap.payment_allocation: index on payment_id
    # Speeds up finding allocations for a supplier payment
    if inspector.has_table("payment_allocation", schema="ap"):
        indexes = {idx["name"] for idx in inspector.get_indexes("payment_allocation", schema="ap")}
        if "idx_ap_payment_alloc_payment" not in indexes:
            op.create_index(
                "idx_ap_payment_alloc_payment",
                "payment_allocation",
                ["payment_id"],
                schema="ap",
            )

        # ap.payment_allocation: index on invoice_id
        # Speeds up finding allocations for a supplier invoice
        if "idx_ap_payment_alloc_invoice" not in indexes:
            op.create_index(
                "idx_ap_payment_alloc_invoice",
                "payment_allocation",
                ["invoice_id"],
                schema="ap",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- Drop AP Schema Indexes ---
    if inspector.has_table("payment_allocation", schema="ap"):
        indexes = {idx["name"] for idx in inspector.get_indexes("payment_allocation", schema="ap")}
        if "idx_ap_payment_alloc_invoice" in indexes:
            op.drop_index("idx_ap_payment_alloc_invoice", table_name="payment_allocation", schema="ap")
        if "idx_ap_payment_alloc_payment" in indexes:
            op.drop_index("idx_ap_payment_alloc_payment", table_name="payment_allocation", schema="ap")

    if inspector.has_table("supplier_payment", schema="ap"):
        indexes = {idx["name"] for idx in inspector.get_indexes("supplier_payment", schema="ap")}
        if "idx_supplier_payment_org_status" in indexes:
            op.drop_index("idx_supplier_payment_org_status", table_name="supplier_payment", schema="ap")

    # --- Drop AR Schema Indexes ---
    if inspector.has_table("payment_allocation", schema="ar"):
        indexes = {idx["name"] for idx in inspector.get_indexes("payment_allocation", schema="ar")}
        if "idx_payment_alloc_invoice" in indexes:
            op.drop_index("idx_payment_alloc_invoice", table_name="payment_allocation", schema="ar")
        if "idx_payment_alloc_payment" in indexes:
            op.drop_index("idx_payment_alloc_payment", table_name="payment_allocation", schema="ar")

    if inspector.has_table("customer_payment", schema="ar"):
        indexes = {idx["name"] for idx in inspector.get_indexes("customer_payment", schema="ar")}
        if "idx_customer_payment_org_status" in indexes:
            op.drop_index("idx_customer_payment_org_status", table_name="customer_payment", schema="ar")
