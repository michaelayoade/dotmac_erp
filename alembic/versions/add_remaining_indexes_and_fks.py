"""Add remaining indexes and FK constraints.

This migration addresses remaining gaps identified in review:
1. Quote line indexes (tax_code_id, revenue_account_id, project_id, cost_center_id)
2. Sales order line indexes (item_id, tax_code_id, revenue_account_id, project_id, cost_center_id)
3. Banking payee indexes and missing FK (default_tax_code_id)
4. Banking transaction_rule indexes and missing FK (tax_code_id)
5. BOM component warehouse_id index

Revision ID: add_remaining_indexes_and_fks
Revises: add_missing_indexes_and_constraints
Create Date: 2025-01-11
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "add_remaining_indexes_and_fks"
down_revision = "add_missing_indexes_and_constraints"
branch_labels = None
depends_on = None


def _index_exists(inspector, table_name: str, index_name: str, schema: str | None = None) -> bool:
    """Check if an index exists on a table."""
    try:
        indexes = inspector.get_indexes(table_name, schema=schema)
        return any(idx["name"] == index_name for idx in indexes)
    except Exception:
        return False


def _constraint_exists(inspector, table_name: str, constraint_name: str, schema: str | None = None) -> bool:
    """Check if a foreign key constraint exists on a table."""
    try:
        fks = inspector.get_foreign_keys(table_name, schema=schema)
        return any(fk["name"] == constraint_name for fk in fks)
    except Exception:
        return False


def _table_exists(inspector, table_name: str, schema: str | None = None) -> bool:
    """Check if a table exists."""
    try:
        return inspector.has_table(table_name, schema=schema)
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # =========================================================================
    # SECTION 1: AR SCHEMA - QUOTE LINE INDEXES
    # =========================================================================
    # Quote line has FKs but no indexes for: tax_code_id, revenue_account_id,
    # project_id, cost_center_id

    if _table_exists(inspector, "quote_line", schema="ar"):
        if not _index_exists(inspector, "quote_line", "idx_ar_quote_line_tax_code", schema="ar"):
            op.create_index(
                "idx_ar_quote_line_tax_code",
                "quote_line",
                ["tax_code_id"],
                schema="ar",
                postgresql_where="tax_code_id IS NOT NULL",
            )
        if not _index_exists(inspector, "quote_line", "idx_ar_quote_line_revenue_acct", schema="ar"):
            op.create_index(
                "idx_ar_quote_line_revenue_acct",
                "quote_line",
                ["revenue_account_id"],
                schema="ar",
                postgresql_where="revenue_account_id IS NOT NULL",
            )
        if not _index_exists(inspector, "quote_line", "idx_ar_quote_line_project", schema="ar"):
            op.create_index(
                "idx_ar_quote_line_project",
                "quote_line",
                ["project_id"],
                schema="ar",
                postgresql_where="project_id IS NOT NULL",
            )
        if not _index_exists(inspector, "quote_line", "idx_ar_quote_line_cost_center", schema="ar"):
            op.create_index(
                "idx_ar_quote_line_cost_center",
                "quote_line",
                ["cost_center_id"],
                schema="ar",
                postgresql_where="cost_center_id IS NOT NULL",
            )

    # =========================================================================
    # SECTION 2: AR SCHEMA - SALES ORDER LINE INDEXES
    # =========================================================================
    # Sales order line has FKs but no indexes for: item_id, tax_code_id,
    # revenue_account_id, project_id, cost_center_id

    if _table_exists(inspector, "sales_order_line", schema="ar"):
        if not _index_exists(inspector, "sales_order_line", "idx_ar_so_line_item", schema="ar"):
            op.create_index(
                "idx_ar_so_line_item",
                "sales_order_line",
                ["item_id"],
                schema="ar",
                postgresql_where="item_id IS NOT NULL",
            )
        if not _index_exists(inspector, "sales_order_line", "idx_ar_so_line_tax_code", schema="ar"):
            op.create_index(
                "idx_ar_so_line_tax_code",
                "sales_order_line",
                ["tax_code_id"],
                schema="ar",
                postgresql_where="tax_code_id IS NOT NULL",
            )
        if not _index_exists(inspector, "sales_order_line", "idx_ar_so_line_revenue_acct", schema="ar"):
            op.create_index(
                "idx_ar_so_line_revenue_acct",
                "sales_order_line",
                ["revenue_account_id"],
                schema="ar",
                postgresql_where="revenue_account_id IS NOT NULL",
            )
        if not _index_exists(inspector, "sales_order_line", "idx_ar_so_line_project", schema="ar"):
            op.create_index(
                "idx_ar_so_line_project",
                "sales_order_line",
                ["project_id"],
                schema="ar",
                postgresql_where="project_id IS NOT NULL",
            )
        if not _index_exists(inspector, "sales_order_line", "idx_ar_so_line_cost_center", schema="ar"):
            op.create_index(
                "idx_ar_so_line_cost_center",
                "sales_order_line",
                ["cost_center_id"],
                schema="ar",
                postgresql_where="cost_center_id IS NOT NULL",
            )
        # Fulfillment status is frequently filtered
        if not _index_exists(inspector, "sales_order_line", "idx_ar_so_line_fulfillment", schema="ar"):
            op.create_index(
                "idx_ar_so_line_fulfillment",
                "sales_order_line",
                ["fulfillment_status"],
                schema="ar",
            )

    # =========================================================================
    # SECTION 3: BANKING SCHEMA - PAYEE INDEXES AND FK
    # =========================================================================
    # Payee has FKs for default_account_id, supplier_id, customer_id but no indexes
    # Payee is missing FK for default_tax_code_id

    if _table_exists(inspector, "payee", schema="banking"):
        # Add indexes on FK columns
        if not _index_exists(inspector, "payee", "idx_banking_payee_default_acct", schema="banking"):
            op.create_index(
                "idx_banking_payee_default_acct",
                "payee",
                ["default_account_id"],
                schema="banking",
                postgresql_where="default_account_id IS NOT NULL",
            )
        if not _index_exists(inspector, "payee", "idx_banking_payee_supplier", schema="banking"):
            op.create_index(
                "idx_banking_payee_supplier",
                "payee",
                ["supplier_id"],
                schema="banking",
                postgresql_where="supplier_id IS NOT NULL",
            )
        if not _index_exists(inspector, "payee", "idx_banking_payee_customer", schema="banking"):
            op.create_index(
                "idx_banking_payee_customer",
                "payee",
                ["customer_id"],
                schema="banking",
                postgresql_where="customer_id IS NOT NULL",
            )
        if not _index_exists(inspector, "payee", "idx_banking_payee_tax_code", schema="banking"):
            op.create_index(
                "idx_banking_payee_tax_code",
                "payee",
                ["default_tax_code_id"],
                schema="banking",
                postgresql_where="default_tax_code_id IS NOT NULL",
            )

        # Add missing FK constraint for default_tax_code_id
        if not _constraint_exists(inspector, "payee", "fk_payee_default_tax_code", schema="banking"):
            op.create_foreign_key(
                "fk_payee_default_tax_code",
                "payee",
                "tax_code",
                ["default_tax_code_id"],
                ["tax_code_id"],
                source_schema="banking",
                referent_schema="tax",
                ondelete="SET NULL",
            )

    # =========================================================================
    # SECTION 4: BANKING SCHEMA - TRANSACTION RULE INDEXES AND FK
    # =========================================================================
    # Transaction rule has FKs for bank_account_id, target_account_id but no indexes
    # Transaction rule is missing FK for tax_code_id

    if _table_exists(inspector, "transaction_rule", schema="banking"):
        # Add indexes on FK columns
        if not _index_exists(inspector, "transaction_rule", "idx_banking_rule_bank_acct", schema="banking"):
            op.create_index(
                "idx_banking_rule_bank_acct",
                "transaction_rule",
                ["bank_account_id"],
                schema="banking",
                postgresql_where="bank_account_id IS NOT NULL",
            )
        if not _index_exists(inspector, "transaction_rule", "idx_banking_rule_target_acct", schema="banking"):
            op.create_index(
                "idx_banking_rule_target_acct",
                "transaction_rule",
                ["target_account_id"],
                schema="banking",
                postgresql_where="target_account_id IS NOT NULL",
            )
        if not _index_exists(inspector, "transaction_rule", "idx_banking_rule_tax_code", schema="banking"):
            op.create_index(
                "idx_banking_rule_tax_code",
                "transaction_rule",
                ["tax_code_id"],
                schema="banking",
                postgresql_where="tax_code_id IS NOT NULL",
            )

        # Add missing FK constraint for tax_code_id
        if not _constraint_exists(inspector, "transaction_rule", "fk_rule_tax_code", schema="banking"):
            op.create_foreign_key(
                "fk_rule_tax_code",
                "transaction_rule",
                "tax_code",
                ["tax_code_id"],
                ["tax_code_id"],
                source_schema="banking",
                referent_schema="tax",
                ondelete="SET NULL",
            )

    # =========================================================================
    # SECTION 5: INV SCHEMA - BOM COMPONENT WAREHOUSE INDEX
    # =========================================================================

    if _table_exists(inspector, "bom_component", schema="inv"):
        if not _index_exists(inspector, "bom_component", "idx_inv_bom_component_warehouse", schema="inv"):
            op.create_index(
                "idx_inv_bom_component_warehouse",
                "bom_component",
                ["warehouse_id"],
                schema="inv",
                postgresql_where="warehouse_id IS NOT NULL",
            )

    # =========================================================================
    # SECTION 6: AR SCHEMA - SHIPMENT INDEXES
    # =========================================================================
    # Shipment tracking queries often filter by tracking number and delivery status

    if _table_exists(inspector, "shipment", schema="ar"):
        if not _index_exists(inspector, "shipment", "idx_ar_shipment_tracking", schema="ar"):
            op.create_index(
                "idx_ar_shipment_tracking",
                "shipment",
                ["tracking_number"],
                schema="ar",
                postgresql_where="tracking_number IS NOT NULL",
            )
        if not _index_exists(inspector, "shipment", "idx_ar_shipment_delivered", schema="ar"):
            op.create_index(
                "idx_ar_shipment_delivered",
                "shipment",
                ["is_delivered"],
                schema="ar",
            )

    # =========================================================================
    # SECTION 7: AR SCHEMA - QUOTE STATUS AND CONVERSION TRACKING
    # =========================================================================
    # Useful for reporting on quote conversion rates

    if _table_exists(inspector, "quote", schema="ar"):
        if not _index_exists(inspector, "quote", "idx_ar_quote_converted_invoice", schema="ar"):
            op.create_index(
                "idx_ar_quote_converted_invoice",
                "quote",
                ["converted_to_invoice_id"],
                schema="ar",
                postgresql_where="converted_to_invoice_id IS NOT NULL",
            )
        if not _index_exists(inspector, "quote", "idx_ar_quote_converted_so", schema="ar"):
            op.create_index(
                "idx_ar_quote_converted_so",
                "quote",
                ["converted_to_so_id"],
                schema="ar",
                postgresql_where="converted_to_so_id IS NOT NULL",
            )
        if not _index_exists(inspector, "quote", "idx_ar_quote_valid_until", schema="ar"):
            op.create_index(
                "idx_ar_quote_valid_until",
                "quote",
                ["organization_id", "valid_until"],
                schema="ar",
                postgresql_where="status NOT IN ('CONVERTED', 'VOID', 'REJECTED')",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # =========================================================================
    # Remove FK constraints (reverse order)
    # =========================================================================
    if _table_exists(inspector, "transaction_rule", schema="banking"):
        if _constraint_exists(inspector, "transaction_rule", "fk_rule_tax_code", schema="banking"):
            op.drop_constraint("fk_rule_tax_code", "transaction_rule", schema="banking", type_="foreignkey")

    if _table_exists(inspector, "payee", schema="banking"):
        if _constraint_exists(inspector, "payee", "fk_payee_default_tax_code", schema="banking"):
            op.drop_constraint("fk_payee_default_tax_code", "payee", schema="banking", type_="foreignkey")

    # =========================================================================
    # Remove indexes (reverse order)
    # =========================================================================

    # Quote indexes
    if _table_exists(inspector, "quote", schema="ar"):
        if _index_exists(inspector, "quote", "idx_ar_quote_valid_until", schema="ar"):
            op.drop_index("idx_ar_quote_valid_until", table_name="quote", schema="ar")
        if _index_exists(inspector, "quote", "idx_ar_quote_converted_so", schema="ar"):
            op.drop_index("idx_ar_quote_converted_so", table_name="quote", schema="ar")
        if _index_exists(inspector, "quote", "idx_ar_quote_converted_invoice", schema="ar"):
            op.drop_index("idx_ar_quote_converted_invoice", table_name="quote", schema="ar")

    # Shipment indexes
    if _table_exists(inspector, "shipment", schema="ar"):
        if _index_exists(inspector, "shipment", "idx_ar_shipment_delivered", schema="ar"):
            op.drop_index("idx_ar_shipment_delivered", table_name="shipment", schema="ar")
        if _index_exists(inspector, "shipment", "idx_ar_shipment_tracking", schema="ar"):
            op.drop_index("idx_ar_shipment_tracking", table_name="shipment", schema="ar")

    # BOM component indexes
    if _table_exists(inspector, "bom_component", schema="inv"):
        if _index_exists(inspector, "bom_component", "idx_inv_bom_component_warehouse", schema="inv"):
            op.drop_index("idx_inv_bom_component_warehouse", table_name="bom_component", schema="inv")

    # Transaction rule indexes
    if _table_exists(inspector, "transaction_rule", schema="banking"):
        if _index_exists(inspector, "transaction_rule", "idx_banking_rule_tax_code", schema="banking"):
            op.drop_index("idx_banking_rule_tax_code", table_name="transaction_rule", schema="banking")
        if _index_exists(inspector, "transaction_rule", "idx_banking_rule_target_acct", schema="banking"):
            op.drop_index("idx_banking_rule_target_acct", table_name="transaction_rule", schema="banking")
        if _index_exists(inspector, "transaction_rule", "idx_banking_rule_bank_acct", schema="banking"):
            op.drop_index("idx_banking_rule_bank_acct", table_name="transaction_rule", schema="banking")

    # Payee indexes
    if _table_exists(inspector, "payee", schema="banking"):
        if _index_exists(inspector, "payee", "idx_banking_payee_tax_code", schema="banking"):
            op.drop_index("idx_banking_payee_tax_code", table_name="payee", schema="banking")
        if _index_exists(inspector, "payee", "idx_banking_payee_customer", schema="banking"):
            op.drop_index("idx_banking_payee_customer", table_name="payee", schema="banking")
        if _index_exists(inspector, "payee", "idx_banking_payee_supplier", schema="banking"):
            op.drop_index("idx_banking_payee_supplier", table_name="payee", schema="banking")
        if _index_exists(inspector, "payee", "idx_banking_payee_default_acct", schema="banking"):
            op.drop_index("idx_banking_payee_default_acct", table_name="payee", schema="banking")

    # Sales order line indexes
    if _table_exists(inspector, "sales_order_line", schema="ar"):
        if _index_exists(inspector, "sales_order_line", "idx_ar_so_line_fulfillment", schema="ar"):
            op.drop_index("idx_ar_so_line_fulfillment", table_name="sales_order_line", schema="ar")
        if _index_exists(inspector, "sales_order_line", "idx_ar_so_line_cost_center", schema="ar"):
            op.drop_index("idx_ar_so_line_cost_center", table_name="sales_order_line", schema="ar")
        if _index_exists(inspector, "sales_order_line", "idx_ar_so_line_project", schema="ar"):
            op.drop_index("idx_ar_so_line_project", table_name="sales_order_line", schema="ar")
        if _index_exists(inspector, "sales_order_line", "idx_ar_so_line_revenue_acct", schema="ar"):
            op.drop_index("idx_ar_so_line_revenue_acct", table_name="sales_order_line", schema="ar")
        if _index_exists(inspector, "sales_order_line", "idx_ar_so_line_tax_code", schema="ar"):
            op.drop_index("idx_ar_so_line_tax_code", table_name="sales_order_line", schema="ar")
        if _index_exists(inspector, "sales_order_line", "idx_ar_so_line_item", schema="ar"):
            op.drop_index("idx_ar_so_line_item", table_name="sales_order_line", schema="ar")

    # Quote line indexes
    if _table_exists(inspector, "quote_line", schema="ar"):
        if _index_exists(inspector, "quote_line", "idx_ar_quote_line_cost_center", schema="ar"):
            op.drop_index("idx_ar_quote_line_cost_center", table_name="quote_line", schema="ar")
        if _index_exists(inspector, "quote_line", "idx_ar_quote_line_project", schema="ar"):
            op.drop_index("idx_ar_quote_line_project", table_name="quote_line", schema="ar")
        if _index_exists(inspector, "quote_line", "idx_ar_quote_line_revenue_acct", schema="ar"):
            op.drop_index("idx_ar_quote_line_revenue_acct", table_name="quote_line", schema="ar")
        if _index_exists(inspector, "quote_line", "idx_ar_quote_line_tax_code", schema="ar"):
            op.drop_index("idx_ar_quote_line_tax_code", table_name="quote_line", schema="ar")
