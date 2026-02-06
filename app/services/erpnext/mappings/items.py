"""
Item mapping from ERPNext to DotMac ERP.
"""

import logging
from typing import Any

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    invert_bool,
    parse_datetime,
    parse_decimal,
)

logger = logging.getLogger(__name__)

# ERPNext valuation_method to DotMac ERP costing_method
VALUATION_METHOD_MAP = {
    "FIFO": "FIFO",
    "Moving Average": "WEIGHTED_AVERAGE",
    "Specific Identification": "SPECIFIC_IDENTIFICATION",
}


def map_valuation_method(value: Any) -> str:
    """Map ERPNext valuation method to DotMac ERP costing method."""
    if not value:
        return "WEIGHTED_AVERAGE"
    return VALUATION_METHOD_MAP.get(str(value), "WEIGHTED_AVERAGE")


def map_item_type(record: dict[str, Any]) -> str:
    """Determine item type from ERPNext fields."""
    is_stock_item = record.get("is_stock_item", 0)

    if is_stock_item:
        return "INVENTORY"
    return "NON_INVENTORY"


class ItemMapping(DocTypeMapping):
    """Map ERPNext Item to DotMac ERP inv.item."""

    def __init__(self):
        super().__init__(
            source_doctype="Item",
            target_table="inv.item",
            fields=[
                FieldMapping(
                    source="item_code",
                    target="item_code",
                    required=True,
                    transformer=lambda v: clean_string(v, 50),
                ),
                FieldMapping(
                    source="item_name",
                    target="item_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 200),
                ),
                FieldMapping(
                    source="description",
                    target="description",
                    required=False,
                    transformer=clean_string,
                ),
                FieldMapping(
                    source="item_group",
                    target="_category_source_name",  # Resolved later
                    required=True,
                ),
                FieldMapping(
                    source="stock_uom",
                    target="base_uom",
                    required=True,
                    default="Nos",
                    transformer=lambda v: clean_string(v, 20) or "Nos",
                ),
                FieldMapping(
                    source="is_stock_item",
                    target="track_inventory",
                    required=False,
                    default=True,
                    transformer=lambda v: bool(v),
                ),
                FieldMapping(
                    source="has_batch_no",
                    target="track_lots",
                    required=False,
                    default=False,
                    transformer=lambda v: bool(v),
                ),
                FieldMapping(
                    source="has_serial_no",
                    target="track_serial_numbers",
                    required=False,
                    default=False,
                    transformer=lambda v: bool(v),
                ),
                FieldMapping(
                    source="valuation_method",
                    target="costing_method",
                    required=False,
                    default="WEIGHTED_AVERAGE",
                    transformer=map_valuation_method,
                ),
                FieldMapping(
                    source="standard_rate",
                    target="standard_cost",
                    required=False,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="last_purchase_rate",
                    target="last_purchase_cost",
                    required=False,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="disabled",
                    target="is_active",
                    required=False,
                    default=True,
                    transformer=invert_bool,
                ),
                FieldMapping(
                    source="is_purchase_item",
                    target="is_purchaseable",
                    required=False,
                    default=True,
                    transformer=lambda v: bool(v) if v is not None else True,
                ),
                FieldMapping(
                    source="is_sales_item",
                    target="is_saleable",
                    required=False,
                    default=True,
                    transformer=lambda v: bool(v) if v is not None else True,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="item_code",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with additional calculated fields."""
        result = super().transform_record(record)

        # Determine item type
        result["item_type"] = map_item_type(record)

        # Default currency
        result["currency_code"] = "NGN"

        return result


class ItemCategoryMapping(DocTypeMapping):
    """Map ERPNext Item Group to DotMac ERP inv.item_category."""

    def __init__(self):
        super().__init__(
            source_doctype="Item Group",
            target_table="inv.item_category",
            fields=[
                FieldMapping(
                    source="name",
                    target="category_code",
                    required=True,
                    transformer=lambda v: clean_string(v, 30),
                ),
                FieldMapping(
                    source="item_group_name",
                    target="category_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="parent_item_group",
                    target="_parent_source_name",  # Resolved later
                    required=False,
                ),
                FieldMapping(
                    source="is_group",
                    target="_is_group",  # Used for hierarchy building
                    required=False,
                    default=False,
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
        """Transform with default values."""
        result = super().transform_record(record)

        # Default is_active
        result["is_active"] = True

        return result
