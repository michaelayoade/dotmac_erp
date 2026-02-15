"""
Payment Entry mappings - ERPNext to DotMac ERP.

Maps ERPNext Payment Entry → ar.customer_payment or ap.supplier_payment
depending on payment_type (Receive vs Pay).
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


class PaymentEntryMapping(DocTypeMapping):
    """Map ERPNext Payment Entry to DotMac payment."""

    def __init__(self) -> None:
        super().__init__(
            source_doctype="Payment Entry",
            target_table="ar.customer_payment",
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
                # Payment type determines AR vs AP
                FieldMapping(
                    source="payment_type",
                    target="_payment_type",
                    required=True,
                ),
                # Party info
                FieldMapping(
                    source="party_type",
                    target="_party_type",
                ),
                FieldMapping(
                    source="party",
                    target="_party_source_name",
                ),
                # Dates
                FieldMapping(
                    source="posting_date",
                    target="payment_date",
                    required=True,
                    transformer=parse_date,
                ),
                # Amounts
                FieldMapping(
                    source="paid_amount",
                    target="amount",
                    required=True,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="received_amount",
                    target="_received_amount",
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="base_paid_amount",
                    target="functional_currency_amount",
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="source_exchange_rate",
                    target="exchange_rate",
                    transformer=parse_decimal,
                    default=1,
                ),
                # Currency
                FieldMapping(
                    source="paid_from_account_currency",
                    target="currency_code",
                    transformer=default_currency,
                ),
                # Payment method
                FieldMapping(
                    source="mode_of_payment",
                    target="_mode_of_payment",
                ),
                # Reference
                FieldMapping(
                    source="reference_no",
                    target="reference",
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="reference_date",
                    target="_reference_date",
                    transformer=parse_date,
                ),
                # Bank accounts
                FieldMapping(
                    source="paid_from",
                    target="_paid_from_account",
                ),
                FieldMapping(
                    source="paid_to",
                    target="_paid_to_account",
                ),
                # Status
                FieldMapping(
                    source="docstatus",
                    target="_docstatus",
                ),
                # Splynx dedup
                FieldMapping(
                    source="custom_splynx_id",
                    target="_splynx_id",
                ),
                # Dotmac ERPNext customization: some employee reimbursements
                # don't populate the child "references" table, but do store
                # the expense claim in this custom field.
                FieldMapping(
                    source="custom_expense_claim",
                    target="_custom_expense_claim",
                ),
            ],
        )


class PaymentEntryReferenceMapping(DocTypeMapping):
    """Map ERPNext Payment Entry Reference to allocation."""

    def __init__(self) -> None:
        super().__init__(
            source_doctype="Payment Entry Reference",
            target_table="ar.payment_allocation",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                ),
                FieldMapping(
                    source="reference_doctype",
                    target="_reference_doctype",
                ),
                FieldMapping(
                    source="reference_name",
                    target="_reference_source_name",
                ),
                FieldMapping(
                    source="allocated_amount",
                    target="allocated_amount",
                    transformer=parse_decimal,
                    default=0,
                ),
            ],
        )
