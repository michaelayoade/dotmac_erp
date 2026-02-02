"""add_phase4_optimization_indexes

Add optimization indexes for specific query patterns and background tasks.

Phase 4 indexes:
- public.notification: email queue partial index for background email sending
- gl.journal_entry: (organization_id, posting_date) for period queries
- ar.customer: (organization_id, is_active) for active customer lookups
- ap.supplier: (organization_id, is_active) for active supplier lookups
- hr.disciplinary_case: (employee_id, status) for employee case lookups
- sync.sync_history: (organization_id, status, started_at) for sync monitoring
- inv.inventory_transaction: (organization_id, warehouse_id, transaction_date) for stock reports

Revision ID: 20260202_add_phase4_optimization_indexes
Revises: 20260202_add_phase3_dashboard_indexes
Create Date: 2026-02-02 15:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "20260202_add_phase4_optimization_indexes"
down_revision = "20260202_add_phase3_dashboard_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- Public Schema: Notification Email Queue ---
    # Partial index for background task that sends pending emails
    if inspector.has_table("notification", schema="public"):
        indexes = {idx["name"] for idx in inspector.get_indexes("notification", schema="public")}
        if "idx_notification_email_queue" not in indexes:
            op.create_index(
                "idx_notification_email_queue",
                "notification",
                ["organization_id", "created_at"],
                schema="public",
                postgresql_where=sa.text("email_sent = false AND channel IN ('EMAIL', 'BOTH')"),
            )

    # --- GL Schema: Journal Entry Period Queries ---
    # Speeds up period close and GL reporting queries
    if inspector.has_table("journal_entry", schema="gl"):
        indexes = {idx["name"] for idx in inspector.get_indexes("journal_entry", schema="gl")}
        if "idx_journal_entry_org_posting_date" not in indexes:
            op.create_index(
                "idx_journal_entry_org_posting_date",
                "journal_entry",
                ["organization_id", sa.text("posting_date DESC")],
                schema="gl",
            )

    # --- AR Schema: Active Customer Lookup ---
    # Speeds up customer dropdowns and search
    if inspector.has_table("customer", schema="ar"):
        indexes = {idx["name"] for idx in inspector.get_indexes("customer", schema="ar")}
        if "idx_customer_org_active" not in indexes:
            op.create_index(
                "idx_customer_org_active",
                "customer",
                ["organization_id", "is_active"],
                schema="ar",
            )

    # --- AP Schema: Active Supplier Lookup ---
    # Speeds up supplier dropdowns and search
    if inspector.has_table("supplier", schema="ap"):
        indexes = {idx["name"] for idx in inspector.get_indexes("supplier", schema="ap")}
        if "idx_supplier_org_active" not in indexes:
            op.create_index(
                "idx_supplier_org_active",
                "supplier",
                ["organization_id", "is_active"],
                schema="ap",
            )

    # --- HR Schema: Disciplinary Case Employee Status ---
    # Speeds up employee discipline history lookups
    if inspector.has_table("disciplinary_case", schema="hr"):
        indexes = {idx["name"] for idx in inspector.get_indexes("disciplinary_case", schema="hr")}
        if "idx_discipline_case_employee_status" not in indexes:
            op.create_index(
                "idx_discipline_case_employee_status",
                "disciplinary_case",
                ["employee_id", "status"],
                schema="hr",
            )

    # --- Sync Schema: Sync History Monitoring ---
    # Speeds up sync dashboard and job status queries
    if inspector.has_table("sync_history", schema="sync"):
        indexes = {idx["name"] for idx in inspector.get_indexes("sync_history", schema="sync")}
        if "idx_sync_history_org_status_started" not in indexes:
            op.create_index(
                "idx_sync_history_org_status_started",
                "sync_history",
                ["organization_id", "status", sa.text("started_at DESC")],
                schema="sync",
            )

    # --- Inventory Schema: Stock Movement Analysis ---
    # Speeds up warehouse stock reports and inventory analysis
    if inspector.has_table("inventory_transaction", schema="inv"):
        indexes = {idx["name"] for idx in inspector.get_indexes("inventory_transaction", schema="inv")}
        if "idx_inv_txn_org_warehouse_date" not in indexes:
            op.create_index(
                "idx_inv_txn_org_warehouse_date",
                "inventory_transaction",
                ["organization_id", "warehouse_id", sa.text("transaction_date DESC")],
                schema="inv",
            )

    # --- Expense Schema: Expense Claim Dashboard ---
    # Speeds up expense claim list with status filtering
    if inspector.has_table("expense_claim", schema="expense"):
        indexes = {idx["name"] for idx in inspector.get_indexes("expense_claim", schema="expense")}
        if "idx_expense_claim_org_status_created" not in indexes:
            op.create_index(
                "idx_expense_claim_org_status_created",
                "expense_claim",
                ["organization_id", "status", sa.text("created_at DESC")],
                schema="expense",
            )

    # --- Support Schema: Ticket Dashboard ---
    # Speeds up support ticket list with status and priority filtering
    if inspector.has_table("ticket", schema="support"):
        indexes = {idx["name"] for idx in inspector.get_indexes("ticket", schema="support")}
        if "idx_ticket_org_status_priority" not in indexes:
            op.create_index(
                "idx_ticket_org_status_priority",
                "ticket",
                ["organization_id", "status", "priority"],
                schema="support",
            )

    # --- Leave Schema: Leave Application Dashboard ---
    # Speeds up leave approval queue
    if inspector.has_table("leave_application", schema="leave"):
        indexes = {idx["name"] for idx in inspector.get_indexes("leave_application", schema="leave")}
        if "idx_leave_app_employee_status" not in indexes:
            op.create_index(
                "idx_leave_app_employee_status",
                "leave_application",
                ["employee_id", "status"],
                schema="leave",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- Drop Leave Schema Index ---
    if inspector.has_table("leave_application", schema="leave"):
        indexes = {idx["name"] for idx in inspector.get_indexes("leave_application", schema="leave")}
        if "idx_leave_app_employee_status" in indexes:
            op.drop_index("idx_leave_app_employee_status", table_name="leave_application", schema="leave")

    # --- Drop Support Schema Index ---
    if inspector.has_table("ticket", schema="support"):
        indexes = {idx["name"] for idx in inspector.get_indexes("ticket", schema="support")}
        if "idx_ticket_org_status_priority" in indexes:
            op.drop_index("idx_ticket_org_status_priority", table_name="ticket", schema="support")

    # --- Drop Expense Schema Index ---
    if inspector.has_table("expense_claim", schema="expense"):
        indexes = {idx["name"] for idx in inspector.get_indexes("expense_claim", schema="expense")}
        if "idx_expense_claim_org_status_created" in indexes:
            op.drop_index("idx_expense_claim_org_status_created", table_name="expense_claim", schema="expense")

    # --- Drop Inventory Schema Index ---
    if inspector.has_table("inventory_transaction", schema="inv"):
        indexes = {idx["name"] for idx in inspector.get_indexes("inventory_transaction", schema="inv")}
        if "idx_inv_txn_org_warehouse_date" in indexes:
            op.drop_index("idx_inv_txn_org_warehouse_date", table_name="inventory_transaction", schema="inv")

    # --- Drop Sync Schema Index ---
    if inspector.has_table("sync_history", schema="sync"):
        indexes = {idx["name"] for idx in inspector.get_indexes("sync_history", schema="sync")}
        if "idx_sync_history_org_status_started" in indexes:
            op.drop_index("idx_sync_history_org_status_started", table_name="sync_history", schema="sync")

    # --- Drop HR Schema Index ---
    if inspector.has_table("disciplinary_case", schema="hr"):
        indexes = {idx["name"] for idx in inspector.get_indexes("disciplinary_case", schema="hr")}
        if "idx_discipline_case_employee_status" in indexes:
            op.drop_index("idx_discipline_case_employee_status", table_name="disciplinary_case", schema="hr")

    # --- Drop AP Schema Index ---
    if inspector.has_table("supplier", schema="ap"):
        indexes = {idx["name"] for idx in inspector.get_indexes("supplier", schema="ap")}
        if "idx_supplier_org_active" in indexes:
            op.drop_index("idx_supplier_org_active", table_name="supplier", schema="ap")

    # --- Drop AR Schema Index ---
    if inspector.has_table("customer", schema="ar"):
        indexes = {idx["name"] for idx in inspector.get_indexes("customer", schema="ar")}
        if "idx_customer_org_active" in indexes:
            op.drop_index("idx_customer_org_active", table_name="customer", schema="ar")

    # --- Drop GL Schema Index ---
    if inspector.has_table("journal_entry", schema="gl"):
        indexes = {idx["name"] for idx in inspector.get_indexes("journal_entry", schema="gl")}
        if "idx_journal_entry_org_posting_date" in indexes:
            op.drop_index("idx_journal_entry_org_posting_date", table_name="journal_entry", schema="gl")

    # --- Drop Public Schema Index ---
    if inspector.has_table("notification", schema="public"):
        indexes = {idx["name"] for idx in inspector.get_indexes("notification", schema="public")}
        if "idx_notification_email_queue" in indexes:
            op.drop_index("idx_notification_email_queue", table_name="notification", schema="public")
