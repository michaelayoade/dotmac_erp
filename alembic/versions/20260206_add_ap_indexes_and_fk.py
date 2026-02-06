"""Add composite indexes and FK constraint to AP tables.

Revision ID: 20260206_add_ap_indexes_and_fk
Revises: 20260206_add_banking_status_indexes
Create Date: 2026-02-06
"""

import sqlalchemy as sa
from alembic import op

revision = "20260206_add_ap_indexes_and_fk"
down_revision = "20260206_add_banking_status_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # supplier_payment: add (organization_id, status) index
    if inspector.has_table("supplier_payment", schema="ap"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("supplier_payment", schema="ap")
        }
        if "idx_supplier_payment_org_status" not in indexes:
            op.create_index(
                "idx_supplier_payment_org_status",
                "supplier_payment",
                ["organization_id", "status"],
                schema="ap",
            )

    # goods_receipt: add (organization_id, status) index
    if inspector.has_table("goods_receipt", schema="ap"):
        indexes = {
            idx["name"] for idx in inspector.get_indexes("goods_receipt", schema="ap")
        }
        if "idx_receipt_org_status" not in indexes:
            op.create_index(
                "idx_receipt_org_status",
                "goods_receipt",
                ["organization_id", "status"],
                schema="ap",
            )

    # payment_batch: add (organization_id, status) index
    if inspector.has_table("payment_batch", schema="ap"):
        indexes = {
            idx["name"] for idx in inspector.get_indexes("payment_batch", schema="ap")
        }
        if "idx_payment_batch_org_status" not in indexes:
            op.create_index(
                "idx_payment_batch_org_status",
                "payment_batch",
                ["organization_id", "status"],
                schema="ap",
            )

    # ap_aging_snapshot: add (organization_id, fiscal_period_id) index
    if inspector.has_table("ap_aging_snapshot", schema="ap"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("ap_aging_snapshot", schema="ap")
        }
        if "idx_ap_aging_org_period" not in indexes:
            op.create_index(
                "idx_ap_aging_org_period",
                "ap_aging_snapshot",
                ["organization_id", "fiscal_period_id"],
                schema="ap",
            )

    # supplier_invoice_line: add indexes on po_line_id and goods_receipt_line_id
    if inspector.has_table("supplier_invoice_line", schema="ap"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("supplier_invoice_line", schema="ap")
        }
        if "idx_inv_line_po_line" not in indexes:
            op.create_index(
                "idx_inv_line_po_line",
                "supplier_invoice_line",
                ["po_line_id"],
                schema="ap",
            )
        if "idx_inv_line_gr_line" not in indexes:
            op.create_index(
                "idx_inv_line_gr_line",
                "supplier_invoice_line",
                ["goods_receipt_line_id"],
                schema="ap",
            )

    # supplier_payment: add FK from payment_batch_id to payment_batch.batch_id
    if inspector.has_table("supplier_payment", schema="ap"):
        fks = {
            fk["name"]
            for fk in inspector.get_foreign_keys("supplier_payment", schema="ap")
            if fk.get("name")
        }
        if "fk_supplier_payment_batch" not in fks:
            # Only add FK if payment_batch table exists
            if inspector.has_table("payment_batch", schema="ap"):
                op.create_foreign_key(
                    "fk_supplier_payment_batch",
                    "supplier_payment",
                    "payment_batch",
                    ["payment_batch_id"],
                    ["batch_id"],
                    source_schema="ap",
                    referent_schema="ap",
                    ondelete="SET NULL",
                )

    # supplier_payment: add FK from bank_account_id to banking.bank_account
    if inspector.has_table("supplier_payment", schema="ap"):
        fks = {
            fk["name"]
            for fk in inspector.get_foreign_keys("supplier_payment", schema="ap")
            if fk.get("name")
        }
        if "fk_supplier_payment_bank_account" not in fks:
            if inspector.has_table("bank_account", schema="banking"):
                op.create_foreign_key(
                    "fk_supplier_payment_bank_account",
                    "supplier_payment",
                    "bank_account",
                    ["bank_account_id"],
                    ["bank_account_id"],
                    source_schema="ap",
                    referent_schema="banking",
                )

    # payment_batch: add FK from bank_account_id to banking.bank_account
    if inspector.has_table("payment_batch", schema="ap"):
        fks = {
            fk["name"]
            for fk in inspector.get_foreign_keys("payment_batch", schema="ap")
            if fk.get("name")
        }
        if "fk_payment_batch_bank_account" not in fks:
            if inspector.has_table("bank_account", schema="banking"):
                op.create_foreign_key(
                    "fk_payment_batch_bank_account",
                    "payment_batch",
                    "bank_account",
                    ["bank_account_id"],
                    ["bank_account_id"],
                    source_schema="ap",
                    referent_schema="banking",
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Drop FKs
    if inspector.has_table("payment_batch", schema="ap"):
        fks = {
            fk["name"]
            for fk in inspector.get_foreign_keys("payment_batch", schema="ap")
            if fk.get("name")
        }
        if "fk_payment_batch_bank_account" in fks:
            op.drop_constraint(
                "fk_payment_batch_bank_account",
                "payment_batch",
                schema="ap",
                type_="foreignkey",
            )

    if inspector.has_table("supplier_payment", schema="ap"):
        fks = {
            fk["name"]
            for fk in inspector.get_foreign_keys("supplier_payment", schema="ap")
            if fk.get("name")
        }
        if "fk_supplier_payment_bank_account" in fks:
            op.drop_constraint(
                "fk_supplier_payment_bank_account",
                "supplier_payment",
                schema="ap",
                type_="foreignkey",
            )
        if "fk_supplier_payment_batch" in fks:
            op.drop_constraint(
                "fk_supplier_payment_batch",
                "supplier_payment",
                schema="ap",
                type_="foreignkey",
            )

    # Drop invoice line indexes
    if inspector.has_table("supplier_invoice_line", schema="ap"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("supplier_invoice_line", schema="ap")
        }
        if "idx_inv_line_gr_line" in indexes:
            op.drop_index(
                "idx_inv_line_gr_line",
                table_name="supplier_invoice_line",
                schema="ap",
            )
        if "idx_inv_line_po_line" in indexes:
            op.drop_index(
                "idx_inv_line_po_line",
                table_name="supplier_invoice_line",
                schema="ap",
            )

    # Drop composite indexes
    if inspector.has_table("ap_aging_snapshot", schema="ap"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("ap_aging_snapshot", schema="ap")
        }
        if "idx_ap_aging_org_period" in indexes:
            op.drop_index(
                "idx_ap_aging_org_period",
                table_name="ap_aging_snapshot",
                schema="ap",
            )

    if inspector.has_table("payment_batch", schema="ap"):
        indexes = {
            idx["name"] for idx in inspector.get_indexes("payment_batch", schema="ap")
        }
        if "idx_payment_batch_org_status" in indexes:
            op.drop_index(
                "idx_payment_batch_org_status",
                table_name="payment_batch",
                schema="ap",
            )

    if inspector.has_table("goods_receipt", schema="ap"):
        indexes = {
            idx["name"] for idx in inspector.get_indexes("goods_receipt", schema="ap")
        }
        if "idx_receipt_org_status" in indexes:
            op.drop_index(
                "idx_receipt_org_status",
                table_name="goods_receipt",
                schema="ap",
            )

    if inspector.has_table("supplier_payment", schema="ap"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes("supplier_payment", schema="ap")
        }
        if "idx_supplier_payment_org_status" in indexes:
            op.drop_index(
                "idx_supplier_payment_org_status",
                table_name="supplier_payment",
                schema="ap",
            )
