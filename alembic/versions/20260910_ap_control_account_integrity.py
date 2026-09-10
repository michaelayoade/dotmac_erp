"""Require valid tenant-scoped AP control accounts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_ap_control_integrity"
down_revision = "20260909_leave_probation_restriction"
branch_labels = None
depends_on = None


_VALID_AP_ACCOUNT = """
    SELECT 1
    FROM gl.account AS account
    JOIN gl.account_category AS category
      ON category.category_id = account.category_id
     AND category.organization_id = account.organization_id
    WHERE account.account_id = {account_expression}
      AND account.organization_id = {organization_expression}
      AND account.is_active IS TRUE
      AND account.subledger_type = 'AP'
      AND category.ifrs_category = 'LIABILITIES'
"""

_TENANT_ACCOUNT = """
    SELECT 1
    FROM gl.account AS account
    WHERE account.account_id = {account_expression}
      AND account.organization_id = {organization_expression}
"""

_VALID_INVOICE_ACCOUNT = _VALID_AP_ACCOUNT.format(
    account_expression="invoice.ap_control_account_id",
    organization_expression="invoice.organization_id",
)
_VALID_SUPPLIER_ACCOUNT = _VALID_AP_ACCOUNT.format(
    account_expression="supplier.ap_control_account_id",
    organization_expression="supplier.organization_id",
)
_TENANT_INVOICE_ACCOUNT = _TENANT_ACCOUNT.format(
    account_expression="invoice.ap_control_account_id",
    organization_expression="invoice.organization_id",
)
_TENANT_SUPPLIER_ACCOUNT = _TENANT_ACCOUNT.format(
    account_expression="supplier.ap_control_account_id",
    organization_expression="supplier.organization_id",
)


def upgrade() -> None:
    bind = op.get_bind()

    # A draft stores the supplier's AP account as an accounting snapshot. Repair
    # only invalid, non-posted snapshots and only when the supplier now has a
    # demonstrably valid replacement; never guess an account.
    bind.execute(
        sa.text(
            f"""
            UPDATE ap.supplier_invoice AS invoice
               SET ap_control_account_id = supplier.ap_control_account_id
              FROM ap.supplier AS supplier
             WHERE supplier.supplier_id = invoice.supplier_id
               AND supplier.organization_id = invoice.organization_id
               AND invoice.status NOT IN ('POSTED', 'PARTIALLY_PAID', 'PAID')
               AND NOT EXISTS (
                   {_VALID_INVOICE_ACCOUNT}
               )
               AND EXISTS (
                   {_VALID_SUPPLIER_ACCOUNT}
               )
            """
        )
    )

    invalid_suppliers = bind.scalar(
        sa.text(
            f"""
            SELECT count(*)
              FROM ap.supplier AS supplier
             WHERE NOT EXISTS (
                 {_TENANT_SUPPLIER_ACCOUNT}
             )
                OR (
                    supplier.is_active IS TRUE
                    AND NOT EXISTS (
                        {_VALID_SUPPLIER_ACCOUNT}
                    )
                )
            """
        )
    )
    invalid_invoices = bind.scalar(
        sa.text(
            f"""
            SELECT count(*)
              FROM ap.supplier_invoice AS invoice
             WHERE NOT EXISTS (
                 {_TENANT_INVOICE_ACCOUNT}
             )
                OR (
                    invoice.status NOT IN ('POSTED', 'PARTIALLY_PAID', 'PAID')
                    AND NOT EXISTS (
                        {_VALID_INVOICE_ACCOUNT}
                    )
                )
            """
        )
    )
    if invalid_suppliers or invalid_invoices:
        raise RuntimeError(
            "AP control account integrity check failed: "
            f"{invalid_suppliers or 0} supplier(s) and "
            f"{invalid_invoices or 0} invoice(s) have invalid AP account "
            "references. Correct each active supplier's payable account before "
            "retrying this migration."
        )

    op.create_unique_constraint(
        "uq_account_organization_account_id",
        "account",
        ["organization_id", "account_id"],
        schema="gl",
    )
    op.create_foreign_key(
        "fk_supplier_organization_ap_control_account",
        "supplier",
        "account",
        ["organization_id", "ap_control_account_id"],
        ["organization_id", "account_id"],
        source_schema="ap",
        referent_schema="gl",
    )
    op.create_foreign_key(
        "fk_supplier_invoice_organization_ap_control_account",
        "supplier_invoice",
        "account",
        ["organization_id", "ap_control_account_id"],
        ["organization_id", "account_id"],
        source_schema="ap",
        referent_schema="gl",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_supplier_invoice_organization_ap_control_account",
        "supplier_invoice",
        schema="ap",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_supplier_organization_ap_control_account",
        "supplier",
        schema="ap",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_account_organization_account_id",
        "account",
        schema="gl",
        type_="unique",
    )
