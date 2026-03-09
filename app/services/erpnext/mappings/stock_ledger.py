"""
Stock Ledger Entry mapping from ERPNext to DotMac ERP.

Maps ERPNext Stock Ledger Entry → inv.inventory_transaction.
Each SLE represents a single stock movement (receipt, issue, transfer leg, etc.).
"""

import logging
from typing import Any

from app.config import settings

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    parse_datetime,
    parse_decimal,
)

logger = logging.getLogger(__name__)

# ERPNext voucher_type → DotMac TransactionType
# Key: (voucher_type, is_inflow) where is_inflow = actual_qty > 0
VOUCHER_TYPE_MAP: dict[tuple[str, bool], str] = {
    # Purchase receipts
    ("Purchase Receipt", True): "RECEIPT",
    ("Purchase Receipt", False): "RETURN",  # Purchase return
    ("Purchase Invoice", True): "RECEIPT",
    ("Purchase Invoice", False): "RETURN",
    # Sales
    ("Delivery Note", False): "SALE",
    ("Delivery Note", True): "RETURN",  # Sales return
    ("Sales Invoice", False): "SALE",
    ("Sales Invoice", True): "RETURN",
    # Stock Entry (general-purpose: transfers, issues, receipts, manufacture)
    ("Stock Entry", True): "RECEIPT",
    ("Stock Entry", False): "ISSUE",
    # Reconciliation / adjustments
    ("Stock Reconciliation", True): "ADJUSTMENT",
    ("Stock Reconciliation", False): "ADJUSTMENT",
}

# Default fallback for unknown voucher types
DEFAULT_INFLOW_TYPE = "RECEIPT"
DEFAULT_OUTFLOW_TYPE = "ISSUE"


def map_transaction_type(record: dict[str, Any]) -> str:
    """Determine TransactionType from voucher_type and qty sign."""
    voucher_type = record.get("voucher_type", "")
    actual_qty = parse_decimal(record.get("actual_qty")) or 0
    is_inflow = actual_qty > 0

    mapped = VOUCHER_TYPE_MAP.get((voucher_type, is_inflow))
    if mapped:
        return mapped

    # Fallback based on qty direction
    return DEFAULT_INFLOW_TYPE if is_inflow else DEFAULT_OUTFLOW_TYPE


class StockLedgerMapping(DocTypeMapping):
    """Map ERPNext Stock Ledger Entry to DotMac ERP inv.inventory_transaction."""

    def __init__(self):
        super().__init__(
            source_doctype="Stock Ledger Entry",
            target_table="inv.inventory_transaction",
            fields=[
                FieldMapping(
                    source="item_code",
                    target="_item_code",  # Resolved to item_id later
                    required=True,
                ),
                FieldMapping(
                    source="warehouse",
                    target="_warehouse_code",  # Resolved to warehouse_id later
                    required=True,
                ),
                FieldMapping(
                    source="posting_date",
                    target="_posting_date",
                    required=True,
                    transformer=clean_string,
                ),
                FieldMapping(
                    source="posting_time",
                    target="_posting_time",
                    required=False,
                    transformer=clean_string,
                ),
                FieldMapping(
                    source="actual_qty",
                    target="_actual_qty",  # Signed qty: + = in, - = out
                    required=True,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="qty_after_transaction",
                    target="_qty_after",  # Running balance
                    required=False,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="valuation_rate",
                    target="unit_cost",
                    required=False,
                    default=0,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="stock_value_difference",
                    target="_value_diff",  # Total cost impact (signed)
                    required=False,
                    default=0,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="voucher_type",
                    target="source_document_type",
                    required=False,
                    transformer=lambda v: clean_string(v, 30),
                ),
                FieldMapping(
                    source="voucher_no",
                    target="reference",
                    required=False,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="batch_no",
                    target="_batch_no",  # Resolved to lot_id later
                    required=False,
                    transformer=clean_string,
                ),
                FieldMapping(
                    source="stock_uom",
                    target="uom",
                    required=False,
                    default="Nos",
                    transformer=lambda v: clean_string(v, 20) or "Nos",
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with calculated fields."""
        result = super().transform_record(record)

        # Determine transaction type from voucher_type + qty sign
        result["transaction_type"] = map_transaction_type(record)

        # Currency default
        result["currency_code"] = settings.default_functional_currency_code

        return result
