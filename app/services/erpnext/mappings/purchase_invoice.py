"""
Purchase Invoice mappings - ERPNext to DotMac ERP.

Maps ERPNext Purchase Invoice → ap.supplier_invoice and
Purchase Invoice Item → ap.supplier_invoice_line.
"""

from __future__ import annotations

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    default_currency,
    parse_date,
    parse_decimal,
)


class PurchaseInvoiceMapping(DocTypeMapping):
    """Map ERPNext Purchase Invoice to DotMac SupplierInvoice."""

    def __init__(self) -> None:
        super().__init__(
            source_doctype="Purchase Invoice",
            target_table="ap.supplier_invoice",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                ),
                # Supplier link
                FieldMapping(
                    source="supplier",
                    target="_supplier_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="supplier_name",
                    target="_supplier_display_name",
                ),
                # Supplier's own invoice number
                FieldMapping(
                    source="bill_no",
                    target="supplier_invoice_number",
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="bill_date",
                    target="_bill_date",
                    transformer=parse_date,
                ),
                # Dates
                FieldMapping(
                    source="posting_date",
                    target="invoice_date",
                    required=True,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="due_date",
                    target="due_date",
                    transformer=parse_date,
                ),
                # Amounts
                FieldMapping(
                    source="net_total",
                    target="subtotal",
                    transformer=parse_decimal,
                    default=0,
                ),
                FieldMapping(
                    source="total_taxes_and_charges",
                    target="tax_amount",
                    transformer=parse_decimal,
                    default=0,
                ),
                FieldMapping(
                    source="grand_total",
                    target="total_amount",
                    required=True,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="outstanding_amount",
                    target="outstanding_amount",
                    transformer=parse_decimal,
                    default=0,
                ),
                FieldMapping(
                    source="base_grand_total",
                    target="functional_currency_amount",
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="conversion_rate",
                    target="exchange_rate",
                    transformer=parse_decimal,
                    default=1,
                ),
                # Currency
                FieldMapping(
                    source="currency",
                    target="currency_code",
                    transformer=default_currency,
                ),
                # Status
                FieldMapping(
                    source="docstatus",
                    target="_docstatus",
                ),
                FieldMapping(
                    source="status",
                    target="_erpnext_status",
                ),
                # Is return (debit note)
                FieldMapping(
                    source="is_return",
                    target="_is_return",
                    default=0,
                ),
                # Cost center / project
                FieldMapping(
                    source="cost_center",
                    target="_cost_center_source_name",
                ),
                FieldMapping(
                    source="project",
                    target="_project_source_name",
                ),
            ],
        )


class PurchaseInvoiceItemMapping(DocTypeMapping):
    """Map ERPNext Purchase Invoice Item to DotMac SupplierInvoiceLine."""

    def __init__(self) -> None:
        super().__init__(
            source_doctype="Purchase Invoice Item",
            target_table="ap.supplier_invoice_line",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                ),
                FieldMapping(
                    source="item_code",
                    target="_item_source_name",
                ),
                FieldMapping(
                    source="item_name",
                    target="_item_name",
                ),
                FieldMapping(
                    source="description",
                    target="description",
                    transformer=lambda v: clean_string(v, 1000),
                ),
                FieldMapping(
                    source="qty",
                    target="quantity",
                    transformer=parse_decimal,
                    default=1,
                ),
                FieldMapping(
                    source="rate",
                    target="unit_price",
                    transformer=parse_decimal,
                    default=0,
                ),
                FieldMapping(
                    source="amount",
                    target="line_amount",
                    transformer=parse_decimal,
                    default=0,
                ),
                # Expense account (resolved to expense_account_id)
                FieldMapping(
                    source="expense_account",
                    target="_expense_account_source_name",
                ),
                FieldMapping(
                    source="cost_center",
                    target="_cost_center_source_name",
                ),
                FieldMapping(
                    source="project",
                    target="_project_source_name",
                ),
            ],
        )
