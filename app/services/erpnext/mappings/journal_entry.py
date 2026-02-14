"""
Journal Entry mappings - ERPNext to DotMac ERP.

Maps ERPNext Journal Entry → gl.journal_entry and
Journal Entry Account → gl.journal_entry_line.
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


class JournalEntryMapping(DocTypeMapping):
    """Map ERPNext Journal Entry to DotMac JournalEntry."""

    def __init__(self) -> None:
        super().__init__(
            source_doctype="Journal Entry",
            target_table="gl.journal_entry",
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
                # Type
                FieldMapping(
                    source="voucher_type",
                    target="_voucher_type",
                ),
                # Dates
                FieldMapping(
                    source="posting_date",
                    target="posting_date",
                    required=True,
                    transformer=parse_date,
                ),
                # Amounts
                FieldMapping(
                    source="total_debit",
                    target="total_debit",
                    transformer=parse_decimal,
                    default=0,
                ),
                FieldMapping(
                    source="total_credit",
                    target="total_credit",
                    transformer=parse_decimal,
                    default=0,
                ),
                # Currency
                FieldMapping(
                    source="multi_currency",
                    target="_multi_currency",
                    default=0,
                ),
                # Description
                FieldMapping(
                    source="remark",
                    target="description",
                    transformer=lambda v: clean_string(v, 1000) or "Journal Entry",
                ),
                FieldMapping(
                    source="user_remark",
                    target="_user_remark",
                    transformer=lambda v: clean_string(v, 500),
                ),
                # Status
                FieldMapping(
                    source="docstatus",
                    target="_docstatus",
                ),
                # Cheque / reference
                FieldMapping(
                    source="cheque_no",
                    target="reference",
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="cheque_date",
                    target="_cheque_date",
                    transformer=parse_date,
                ),
            ],
        )


class JournalEntryAccountMapping(DocTypeMapping):
    """Map ERPNext Journal Entry Account to DotMac JournalEntryLine."""

    def __init__(self) -> None:
        super().__init__(
            source_doctype="Journal Entry Account",
            target_table="gl.journal_entry_line",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                ),
                # Account
                FieldMapping(
                    source="account",
                    target="_account_source_name",
                    required=True,
                ),
                # Amounts
                FieldMapping(
                    source="debit_in_account_currency",
                    target="debit_amount",
                    transformer=parse_decimal,
                    default=0,
                ),
                FieldMapping(
                    source="credit_in_account_currency",
                    target="credit_amount",
                    transformer=parse_decimal,
                    default=0,
                ),
                FieldMapping(
                    source="debit",
                    target="debit_amount_functional",
                    transformer=parse_decimal,
                    default=0,
                ),
                FieldMapping(
                    source="credit",
                    target="credit_amount_functional",
                    transformer=parse_decimal,
                    default=0,
                ),
                FieldMapping(
                    source="exchange_rate",
                    target="exchange_rate",
                    transformer=parse_decimal,
                    default=1,
                ),
                FieldMapping(
                    source="account_currency",
                    target="currency_code",
                    transformer=default_currency,
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
                # Party
                FieldMapping(
                    source="party_type",
                    target="_party_type",
                ),
                FieldMapping(
                    source="party",
                    target="_party_source_name",
                ),
            ],
        )
