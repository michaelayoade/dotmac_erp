"""add_phase3_dashboard_indexes

Add composite indexes for dashboard queries and workflow filtering.

Phase 3 indexes:
- ar.invoice: (organization_id, status, created_at) for dashboard lists
- ap.supplier_invoice: (organization_id, status, created_at) for dashboard lists
- public.notification: (recipient_id, created_at) for user notification feeds
- public.notification: (organization_id, created_at) for org notification feeds
- payroll.payroll_entry: (organization_id, status) for payroll approval queue
- payroll.salary_slip: (payroll_entry_id) for bulk processing

Revision ID: 20260202_add_phase3_dashboard_indexes
Revises: 20260202_add_phase2_fk_indexes
Create Date: 2026-02-02 14:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "20260202_add_phase3_dashboard_indexes"
down_revision = "20260202_add_phase2_fk_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- AR Schema: Invoice Dashboard ---
    # Speeds up invoice list pages with status filtering and date ordering
    if inspector.has_table("invoice", schema="ar"):
        indexes = {idx["name"] for idx in inspector.get_indexes("invoice", schema="ar")}
        if "idx_invoice_org_status_created" not in indexes:
            op.create_index(
                "idx_invoice_org_status_created",
                "invoice",
                ["organization_id", "status", sa.text("created_at DESC")],
                schema="ar",
            )

    # --- AP Schema: Supplier Invoice Dashboard ---
    # Speeds up bill list pages with status filtering and date ordering
    if inspector.has_table("supplier_invoice", schema="ap"):
        indexes = {idx["name"] for idx in inspector.get_indexes("supplier_invoice", schema="ap")}
        if "idx_supplier_invoice_org_status_created" not in indexes:
            op.create_index(
                "idx_supplier_invoice_org_status_created",
                "supplier_invoice",
                ["organization_id", "status", sa.text("created_at DESC")],
                schema="ap",
            )

    # --- Public Schema: Notification User Feed ---
    # Speeds up user notification feed (bell icon dropdown)
    if inspector.has_table("notification", schema="public"):
        indexes = {idx["name"] for idx in inspector.get_indexes("notification", schema="public")}
        if "idx_notification_recipient_created" not in indexes:
            op.create_index(
                "idx_notification_recipient_created",
                "notification",
                ["recipient_id", sa.text("created_at DESC")],
                schema="public",
            )

        # Org-wide notification queries for admin dashboards
        if "idx_notification_org_created" not in indexes:
            op.create_index(
                "idx_notification_org_created",
                "notification",
                ["organization_id", sa.text("created_at DESC")],
                schema="public",
            )

    # --- Payroll Schema: Payroll Entry Status ---
    # Speeds up payroll approval queue and status filtering
    if inspector.has_table("payroll_entry", schema="payroll"):
        indexes = {idx["name"] for idx in inspector.get_indexes("payroll_entry", schema="payroll")}
        if "idx_payroll_entry_org_status" not in indexes:
            op.create_index(
                "idx_payroll_entry_org_status",
                "payroll_entry",
                ["organization_id", "status"],
                schema="payroll",
            )

    # --- Payroll Schema: Salary Slip Batch Processing ---
    # Speeds up loading all slips for a payroll entry
    if inspector.has_table("salary_slip", schema="payroll"):
        indexes = {idx["name"] for idx in inspector.get_indexes("salary_slip", schema="payroll")}
        if "idx_salary_slip_payroll_entry" not in indexes:
            op.create_index(
                "idx_salary_slip_payroll_entry",
                "salary_slip",
                ["payroll_entry_id"],
                schema="payroll",
                postgresql_where=sa.text("payroll_entry_id IS NOT NULL"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- Drop Payroll Schema Indexes ---
    if inspector.has_table("salary_slip", schema="payroll"):
        indexes = {idx["name"] for idx in inspector.get_indexes("salary_slip", schema="payroll")}
        if "idx_salary_slip_payroll_entry" in indexes:
            op.drop_index("idx_salary_slip_payroll_entry", table_name="salary_slip", schema="payroll")

    if inspector.has_table("payroll_entry", schema="payroll"):
        indexes = {idx["name"] for idx in inspector.get_indexes("payroll_entry", schema="payroll")}
        if "idx_payroll_entry_org_status" in indexes:
            op.drop_index("idx_payroll_entry_org_status", table_name="payroll_entry", schema="payroll")

    # --- Drop Public Schema Indexes ---
    if inspector.has_table("notification", schema="public"):
        indexes = {idx["name"] for idx in inspector.get_indexes("notification", schema="public")}
        if "idx_notification_org_created" in indexes:
            op.drop_index("idx_notification_org_created", table_name="notification", schema="public")
        if "idx_notification_recipient_created" in indexes:
            op.drop_index("idx_notification_recipient_created", table_name="notification", schema="public")

    # --- Drop AP Schema Index ---
    if inspector.has_table("supplier_invoice", schema="ap"):
        indexes = {idx["name"] for idx in inspector.get_indexes("supplier_invoice", schema="ap")}
        if "idx_supplier_invoice_org_status_created" in indexes:
            op.drop_index("idx_supplier_invoice_org_status_created", table_name="supplier_invoice", schema="ap")

    # --- Drop AR Schema Index ---
    if inspector.has_table("invoice", schema="ar"):
        indexes = {idx["name"] for idx in inspector.get_indexes("invoice", schema="ar")}
        if "idx_invoice_org_status_created" in indexes:
            op.drop_index("idx_invoice_org_status_created", table_name="invoice", schema="ar")
