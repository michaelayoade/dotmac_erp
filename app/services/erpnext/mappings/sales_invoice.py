"""
Sales Invoice mappings - ERPNext to DotMac ERP.

Maps ERPNext Sales Invoice → ar.invoice and
Sales Invoice Item → ar.invoice_line.
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


class SalesInvoiceMapping(DocTypeMapping):
    """Map ERPNext Sales Invoice to DotMac Invoice."""

    def __init__(self) -> None:
        super().__init__(
            source_doctype="Sales Invoice",
            target_table="ar.invoice",
            fields=[
                # Name / number
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                ),
                # Customer link (resolved in sync service)
                FieldMapping(
                    source="customer",
                    target="_customer_source_name",
                    required=True,
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
                # Splynx dedup field
                FieldMapping(
                    source="custom_splynx_id",
                    target="_splynx_id",
                ),
                # Is return (credit note)
                FieldMapping(
                    source="is_return",
                    target="_is_return",
                    default=0,
                ),
                FieldMapping(
                    source="return_against",
                    target="_return_against",
                ),
                # Cost center / project (for line defaults)
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


class SalesInvoiceItemMapping(DocTypeMapping):
    """Map ERPNext Sales Invoice Item to DotMac InvoiceLine."""

    def __init__(self) -> None:
        super().__init__(
            source_doctype="Sales Invoice Item",
            target_table="ar.invoice_line",
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
                FieldMapping(
                    source="discount_percentage",
                    target="discount_percentage",
                    transformer=parse_decimal,
                    default=0,
                ),
                FieldMapping(
                    source="discount_amount",
                    target="discount_amount",
                    transformer=parse_decimal,
                    default=0,
                ),
                # Income account (resolved to revenue_account_id)
                FieldMapping(
                    source="income_account",
                    target="_income_account_source_name",
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
