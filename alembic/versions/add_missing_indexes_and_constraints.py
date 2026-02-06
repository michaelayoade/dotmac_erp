"""Add missing indexes and FK constraints for performance and data integrity.

This migration addresses:
1. Missing indexes on foreign key columns (RBAC, AP, AR, INV schemas)
2. Missing indexes on frequently queried columns (status, dates, dimensions)
3. Missing FK constraints on orphan UUID references

Revision ID: add_missing_indexes_and_constraints
Revises: add_inventory_extensions
Create Date: 2025-01-11
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "add_missing_indexes_and_constraints"
down_revision = "add_inventory_extensions"
branch_labels = None
depends_on = None


def _index_exists(
    inspector, table_name: str, index_name: str, schema: str | None = None
) -> bool:
    """Check if an index exists on a table."""
    try:
        indexes = inspector.get_indexes(table_name, schema=schema)
        return any(idx["name"] == index_name for idx in indexes)
    except Exception:
        return False


def _constraint_exists(
    inspector, table_name: str, constraint_name: str, schema: str | None = None
) -> bool:
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
    # SECTION 1: RBAC INDEXES (Critical for permission checks)
    # =========================================================================
    # These tables are in the public schema (no schema prefix)

    # role_permissions table
    if _table_exists(inspector, "role_permissions"):
        if not _index_exists(
            inspector, "role_permissions", "ix_role_permissions_role_id"
        ):
            op.create_index(
                "ix_role_permissions_role_id",
                "role_permissions",
                ["role_id"],
            )
        if not _index_exists(
            inspector, "role_permissions", "ix_role_permissions_permission_id"
        ):
            op.create_index(
                "ix_role_permissions_permission_id",
                "role_permissions",
                ["permission_id"],
            )

    # person_roles table
    if _table_exists(inspector, "person_roles"):
        if not _index_exists(inspector, "person_roles", "ix_person_roles_person_id"):
            op.create_index(
                "ix_person_roles_person_id",
                "person_roles",
                ["person_id"],
            )
        if not _index_exists(inspector, "person_roles", "ix_person_roles_role_id"):
            op.create_index(
                "ix_person_roles_role_id",
                "person_roles",
                ["role_id"],
            )

    # =========================================================================
    # SECTION 2: AP SCHEMA INDEXES
    # =========================================================================

    # payment_batch - status and organization_id indexes
    if _table_exists(inspector, "payment_batch", schema="ap"):
        if not _index_exists(
            inspector, "payment_batch", "idx_ap_payment_batch_status", schema="ap"
        ):
            op.create_index(
                "idx_ap_payment_batch_status",
                "payment_batch",
                ["status"],
                schema="ap",
            )
        if not _index_exists(
            inspector, "payment_batch", "idx_ap_payment_batch_org", schema="ap"
        ):
            op.create_index(
                "idx_ap_payment_batch_org",
                "payment_batch",
                ["organization_id"],
                schema="ap",
            )
        if not _index_exists(
            inspector, "payment_batch", "idx_ap_payment_batch_bank_account", schema="ap"
        ):
            op.create_index(
                "idx_ap_payment_batch_bank_account",
                "payment_batch",
                ["bank_account_id"],
                schema="ap",
            )

    # supplier_payment - organization_id, bank_account_id, status indexes
    if _table_exists(inspector, "supplier_payment", schema="ap"):
        if not _index_exists(
            inspector, "supplier_payment", "idx_ap_supplier_payment_org", schema="ap"
        ):
            op.create_index(
                "idx_ap_supplier_payment_org",
                "supplier_payment",
                ["organization_id"],
                schema="ap",
            )
        if not _index_exists(
            inspector, "supplier_payment", "idx_ap_supplier_payment_bank", schema="ap"
        ):
            op.create_index(
                "idx_ap_supplier_payment_bank",
                "supplier_payment",
                ["bank_account_id"],
                schema="ap",
            )
        if not _index_exists(
            inspector, "supplier_payment", "idx_ap_supplier_payment_status", schema="ap"
        ):
            op.create_index(
                "idx_ap_supplier_payment_status",
                "supplier_payment",
                ["organization_id", "status"],
                schema="ap",
            )
        if not _index_exists(
            inspector, "supplier_payment", "idx_ap_supplier_payment_batch", schema="ap"
        ):
            op.create_index(
                "idx_ap_supplier_payment_batch",
                "supplier_payment",
                ["payment_batch_id"],
                schema="ap",
            )

    # goods_receipt - supplier_id index
    if _table_exists(inspector, "goods_receipt", schema="ap"):
        if not _index_exists(
            inspector, "goods_receipt", "idx_ap_goods_receipt_supplier", schema="ap"
        ):
            op.create_index(
                "idx_ap_goods_receipt_supplier",
                "goods_receipt",
                ["supplier_id"],
                schema="ap",
            )
        if not _index_exists(
            inspector, "goods_receipt", "idx_ap_goods_receipt_status", schema="ap"
        ):
            op.create_index(
                "idx_ap_goods_receipt_status",
                "goods_receipt",
                ["organization_id", "status"],
                schema="ap",
            )

    # =========================================================================
    # SECTION 3: AR SCHEMA INDEXES
    # =========================================================================

    # invoice_line - tax_code_id index
    if _table_exists(inspector, "invoice_line", schema="ar"):
        if not _index_exists(
            inspector, "invoice_line", "idx_ar_invoice_line_tax_code", schema="ar"
        ):
            op.create_index(
                "idx_ar_invoice_line_tax_code",
                "invoice_line",
                ["tax_code_id"],
                schema="ar",
            )
        if not _index_exists(
            inspector, "invoice_line", "idx_ar_invoice_line_obligation", schema="ar"
        ):
            op.create_index(
                "idx_ar_invoice_line_obligation",
                "invoice_line",
                ["obligation_id"],
                schema="ar",
            )

    # =========================================================================
    # SECTION 4: INV SCHEMA INDEXES
    # =========================================================================

    # warehouse - organization_id, location_id indexes
    if _table_exists(inspector, "warehouse", schema="inv"):
        if not _index_exists(
            inspector, "warehouse", "idx_inv_warehouse_org", schema="inv"
        ):
            op.create_index(
                "idx_inv_warehouse_org",
                "warehouse",
                ["organization_id"],
                schema="inv",
            )
        if not _index_exists(
            inspector, "warehouse", "idx_inv_warehouse_location", schema="inv"
        ):
            op.create_index(
                "idx_inv_warehouse_location",
                "warehouse",
                ["location_id"],
                schema="inv",
            )

    # inventory_lot - expiry_date, supplier_id indexes
    if _table_exists(inspector, "inventory_lot", schema="inv"):
        if not _index_exists(
            inspector, "inventory_lot", "idx_inv_lot_expiry", schema="inv"
        ):
            op.create_index(
                "idx_inv_lot_expiry",
                "inventory_lot",
                ["expiry_date"],
                schema="inv",
                postgresql_where="expiry_date IS NOT NULL",
            )
        if not _index_exists(
            inspector, "inventory_lot", "idx_inv_lot_supplier", schema="inv"
        ):
            op.create_index(
                "idx_inv_lot_supplier",
                "inventory_lot",
                ["supplier_id"],
                schema="inv",
            )

    # inventory_transaction - organization_id index
    if _table_exists(inspector, "inventory_transaction", schema="inv"):
        if not _index_exists(
            inspector, "inventory_transaction", "idx_inv_txn_org", schema="inv"
        ):
            op.create_index(
                "idx_inv_txn_org",
                "inventory_transaction",
                ["organization_id"],
                schema="inv",
            )
        if not _index_exists(
            inspector, "inventory_transaction", "idx_inv_txn_type", schema="inv"
        ):
            op.create_index(
                "idx_inv_txn_type",
                "inventory_transaction",
                ["transaction_type"],
                schema="inv",
            )
        if not _index_exists(
            inspector, "inventory_transaction", "idx_inv_txn_to_warehouse", schema="inv"
        ):
            op.create_index(
                "idx_inv_txn_to_warehouse",
                "inventory_transaction",
                ["to_warehouse_id"],
                schema="inv",
                postgresql_where="to_warehouse_id IS NOT NULL",
            )

    # inventory_count - warehouse_id, status indexes
    if _table_exists(inspector, "inventory_count", schema="inv"):
        if not _index_exists(
            inspector, "inventory_count", "idx_inv_count_status", schema="inv"
        ):
            op.create_index(
                "idx_inv_count_status",
                "inventory_count",
                ["organization_id", "status"],
                schema="inv",
            )
        if not _index_exists(
            inspector, "inventory_count", "idx_inv_count_warehouse", schema="inv"
        ):
            op.create_index(
                "idx_inv_count_warehouse",
                "inventory_count",
                ["warehouse_id"],
                schema="inv",
            )

    # =========================================================================
    # SECTION 5: GL SCHEMA INDEXES
    # =========================================================================

    # journal_entry - source document lookups
    if _table_exists(inspector, "journal_entry", schema="gl"):
        if not _index_exists(
            inspector, "journal_entry", "idx_gl_journal_source_doc", schema="gl"
        ):
            op.create_index(
                "idx_gl_journal_source_doc",
                "journal_entry",
                ["source_module", "source_document_type", "source_document_id"],
                schema="gl",
                postgresql_where="source_document_id IS NOT NULL",
            )
        if not _index_exists(
            inspector, "journal_entry", "idx_gl_journal_intercompany", schema="gl"
        ):
            op.create_index(
                "idx_gl_journal_intercompany",
                "journal_entry",
                ["intercompany_org_id"],
                schema="gl",
                postgresql_where="is_intercompany = true",
            )
        if not _index_exists(
            inspector, "journal_entry", "idx_gl_journal_reversed", schema="gl"
        ):
            op.create_index(
                "idx_gl_journal_reversed",
                "journal_entry",
                ["reversed_journal_id"],
                schema="gl",
                postgresql_where="reversed_journal_id IS NOT NULL",
            )

    # journal_entry_line - dimension indexes
    if _table_exists(inspector, "journal_entry_line", schema="gl"):
        if not _index_exists(
            inspector, "journal_entry_line", "idx_gl_jel_business_unit", schema="gl"
        ):
            op.create_index(
                "idx_gl_jel_business_unit",
                "journal_entry_line",
                ["business_unit_id"],
                schema="gl",
                postgresql_where="business_unit_id IS NOT NULL",
            )

    # =========================================================================
    # SECTION 6: FK CONSTRAINTS FOR ORPHAN REFERENCES
    # =========================================================================
    # Adding proper FK constraints where UUID columns reference other tables
    # but lack actual constraints

    # inventory_transaction - to_warehouse_id FK
    if _table_exists(inspector, "inventory_transaction", schema="inv"):
        if not _constraint_exists(
            inspector, "inventory_transaction", "fk_inv_txn_to_warehouse", schema="inv"
        ):
            op.create_foreign_key(
                "fk_inv_txn_to_warehouse",
                "inventory_transaction",
                "warehouse",
                ["to_warehouse_id"],
                ["warehouse_id"],
                source_schema="inv",
                referent_schema="inv",
                ondelete="RESTRICT",
            )
        if not _constraint_exists(
            inspector, "inventory_transaction", "fk_inv_txn_to_location", schema="inv"
        ):
            op.create_foreign_key(
                "fk_inv_txn_to_location",
                "inventory_transaction",
                "warehouse_location",
                ["to_location_id"],
                ["location_id"],
                source_schema="inv",
                referent_schema="inv",
                ondelete="RESTRICT",
            )

    # inventory_lot - supplier_id FK
    if _table_exists(inspector, "inventory_lot", schema="inv"):
        if not _constraint_exists(
            inspector, "inventory_lot", "fk_inv_lot_supplier", schema="inv"
        ):
            op.create_foreign_key(
                "fk_inv_lot_supplier",
                "inventory_lot",
                "supplier",
                ["supplier_id"],
                ["supplier_id"],
                source_schema="inv",
                referent_schema="ap",
                ondelete="RESTRICT",
            )

    # =========================================================================
    # SECTION 7: COMPOSITE INDEXES FOR COMMON QUERY PATTERNS
    # =========================================================================

    # Common pattern: filter by org + date range
    if _table_exists(inspector, "supplier_payment", schema="ap"):
        if not _index_exists(
            inspector, "supplier_payment", "idx_ap_supplier_payment_date", schema="ap"
        ):
            op.create_index(
                "idx_ap_supplier_payment_date",
                "supplier_payment",
                ["organization_id", "payment_date"],
                schema="ap",
            )

    # Audit trail queries - created_by lookups
    if _table_exists(inspector, "journal_entry", schema="gl"):
        if not _index_exists(
            inspector, "journal_entry", "idx_gl_journal_created_by", schema="gl"
        ):
            op.create_index(
                "idx_gl_journal_created_by",
                "journal_entry",
                ["created_by_user_id"],
                schema="gl",
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # =========================================================================
    # Remove FK constraints (reverse order)
    # =========================================================================
    if _table_exists(inspector, "inventory_lot", schema="inv"):
        if _constraint_exists(
            inspector, "inventory_lot", "fk_inv_lot_supplier", schema="inv"
        ):
            op.drop_constraint(
                "fk_inv_lot_supplier", "inventory_lot", schema="inv", type_="foreignkey"
            )

    if _table_exists(inspector, "inventory_transaction", schema="inv"):
        if _constraint_exists(
            inspector, "inventory_transaction", "fk_inv_txn_to_location", schema="inv"
        ):
            op.drop_constraint(
                "fk_inv_txn_to_location",
                "inventory_transaction",
                schema="inv",
                type_="foreignkey",
            )
        if _constraint_exists(
            inspector, "inventory_transaction", "fk_inv_txn_to_warehouse", schema="inv"
        ):
            op.drop_constraint(
                "fk_inv_txn_to_warehouse",
                "inventory_transaction",
                schema="inv",
                type_="foreignkey",
            )

    # =========================================================================
    # Remove indexes (reverse order)
    # =========================================================================

    # GL indexes
    if _table_exists(inspector, "journal_entry", schema="gl"):
        if _index_exists(
            inspector, "journal_entry", "idx_gl_journal_created_by", schema="gl"
        ):
            op.drop_index(
                "idx_gl_journal_created_by", table_name="journal_entry", schema="gl"
            )
        if _index_exists(
            inspector, "journal_entry", "idx_gl_journal_reversed", schema="gl"
        ):
            op.drop_index(
                "idx_gl_journal_reversed", table_name="journal_entry", schema="gl"
            )
        if _index_exists(
            inspector, "journal_entry", "idx_gl_journal_intercompany", schema="gl"
        ):
            op.drop_index(
                "idx_gl_journal_intercompany", table_name="journal_entry", schema="gl"
            )
        if _index_exists(
            inspector, "journal_entry", "idx_gl_journal_source_doc", schema="gl"
        ):
            op.drop_index(
                "idx_gl_journal_source_doc", table_name="journal_entry", schema="gl"
            )

    if _table_exists(inspector, "journal_entry_line", schema="gl"):
        if _index_exists(
            inspector, "journal_entry_line", "idx_gl_jel_business_unit", schema="gl"
        ):
            op.drop_index(
                "idx_gl_jel_business_unit", table_name="journal_entry_line", schema="gl"
            )

    # INV indexes
    if _table_exists(inspector, "inventory_count", schema="inv"):
        if _index_exists(
            inspector, "inventory_count", "idx_inv_count_warehouse", schema="inv"
        ):
            op.drop_index(
                "idx_inv_count_warehouse", table_name="inventory_count", schema="inv"
            )
        if _index_exists(
            inspector, "inventory_count", "idx_inv_count_status", schema="inv"
        ):
            op.drop_index(
                "idx_inv_count_status", table_name="inventory_count", schema="inv"
            )

    if _table_exists(inspector, "inventory_transaction", schema="inv"):
        if _index_exists(
            inspector, "inventory_transaction", "idx_inv_txn_to_warehouse", schema="inv"
        ):
            op.drop_index(
                "idx_inv_txn_to_warehouse",
                table_name="inventory_transaction",
                schema="inv",
            )
        if _index_exists(
            inspector, "inventory_transaction", "idx_inv_txn_type", schema="inv"
        ):
            op.drop_index(
                "idx_inv_txn_type", table_name="inventory_transaction", schema="inv"
            )
        if _index_exists(
            inspector, "inventory_transaction", "idx_inv_txn_org", schema="inv"
        ):
            op.drop_index(
                "idx_inv_txn_org", table_name="inventory_transaction", schema="inv"
            )

    if _table_exists(inspector, "inventory_lot", schema="inv"):
        if _index_exists(
            inspector, "inventory_lot", "idx_inv_lot_supplier", schema="inv"
        ):
            op.drop_index(
                "idx_inv_lot_supplier", table_name="inventory_lot", schema="inv"
            )
        if _index_exists(
            inspector, "inventory_lot", "idx_inv_lot_expiry", schema="inv"
        ):
            op.drop_index(
                "idx_inv_lot_expiry", table_name="inventory_lot", schema="inv"
            )

    if _table_exists(inspector, "warehouse", schema="inv"):
        if _index_exists(
            inspector, "warehouse", "idx_inv_warehouse_location", schema="inv"
        ):
            op.drop_index(
                "idx_inv_warehouse_location", table_name="warehouse", schema="inv"
            )
        if _index_exists(inspector, "warehouse", "idx_inv_warehouse_org", schema="inv"):
            op.drop_index("idx_inv_warehouse_org", table_name="warehouse", schema="inv")

    # AR indexes
    if _table_exists(inspector, "invoice_line", schema="ar"):
        if _index_exists(
            inspector, "invoice_line", "idx_ar_invoice_line_obligation", schema="ar"
        ):
            op.drop_index(
                "idx_ar_invoice_line_obligation", table_name="invoice_line", schema="ar"
            )
        if _index_exists(
            inspector, "invoice_line", "idx_ar_invoice_line_tax_code", schema="ar"
        ):
            op.drop_index(
                "idx_ar_invoice_line_tax_code", table_name="invoice_line", schema="ar"
            )

    # AP indexes
    if _table_exists(inspector, "goods_receipt", schema="ap"):
        if _index_exists(
            inspector, "goods_receipt", "idx_ap_goods_receipt_status", schema="ap"
        ):
            op.drop_index(
                "idx_ap_goods_receipt_status", table_name="goods_receipt", schema="ap"
            )
        if _index_exists(
            inspector, "goods_receipt", "idx_ap_goods_receipt_supplier", schema="ap"
        ):
            op.drop_index(
                "idx_ap_goods_receipt_supplier", table_name="goods_receipt", schema="ap"
            )

    if _table_exists(inspector, "supplier_payment", schema="ap"):
        if _index_exists(
            inspector, "supplier_payment", "idx_ap_supplier_payment_date", schema="ap"
        ):
            op.drop_index(
                "idx_ap_supplier_payment_date",
                table_name="supplier_payment",
                schema="ap",
            )
        if _index_exists(
            inspector, "supplier_payment", "idx_ap_supplier_payment_batch", schema="ap"
        ):
            op.drop_index(
                "idx_ap_supplier_payment_batch",
                table_name="supplier_payment",
                schema="ap",
            )
        if _index_exists(
            inspector, "supplier_payment", "idx_ap_supplier_payment_status", schema="ap"
        ):
            op.drop_index(
                "idx_ap_supplier_payment_status",
                table_name="supplier_payment",
                schema="ap",
            )
        if _index_exists(
            inspector, "supplier_payment", "idx_ap_supplier_payment_bank", schema="ap"
        ):
            op.drop_index(
                "idx_ap_supplier_payment_bank",
                table_name="supplier_payment",
                schema="ap",
            )
        if _index_exists(
            inspector, "supplier_payment", "idx_ap_supplier_payment_org", schema="ap"
        ):
            op.drop_index(
                "idx_ap_supplier_payment_org",
                table_name="supplier_payment",
                schema="ap",
            )

    if _table_exists(inspector, "payment_batch", schema="ap"):
        if _index_exists(
            inspector, "payment_batch", "idx_ap_payment_batch_bank_account", schema="ap"
        ):
            op.drop_index(
                "idx_ap_payment_batch_bank_account",
                table_name="payment_batch",
                schema="ap",
            )
        if _index_exists(
            inspector, "payment_batch", "idx_ap_payment_batch_org", schema="ap"
        ):
            op.drop_index(
                "idx_ap_payment_batch_org", table_name="payment_batch", schema="ap"
            )
        if _index_exists(
            inspector, "payment_batch", "idx_ap_payment_batch_status", schema="ap"
        ):
            op.drop_index(
                "idx_ap_payment_batch_status", table_name="payment_batch", schema="ap"
            )

    # RBAC indexes (public schema)
    if _table_exists(inspector, "person_roles"):
        if _index_exists(inspector, "person_roles", "ix_person_roles_role_id"):
            op.drop_index("ix_person_roles_role_id", table_name="person_roles")
        if _index_exists(inspector, "person_roles", "ix_person_roles_person_id"):
            op.drop_index("ix_person_roles_person_id", table_name="person_roles")

    if _table_exists(inspector, "role_permissions"):
        if _index_exists(
            inspector, "role_permissions", "ix_role_permissions_permission_id"
        ):
            op.drop_index(
                "ix_role_permissions_permission_id", table_name="role_permissions"
            )
        if _index_exists(inspector, "role_permissions", "ix_role_permissions_role_id"):
            op.drop_index("ix_role_permissions_role_id", table_name="role_permissions")
