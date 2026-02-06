"""
Asset mapping from ERPNext to DotMac ERP.
"""

import logging
from typing import Any

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    parse_date,
    parse_datetime,
    parse_decimal,
)

logger = logging.getLogger(__name__)

# ERPNext depreciation_method to DotMac ERP
DEPRECIATION_METHOD_MAP = {
    "Straight Line": "STRAIGHT_LINE",
    "Written Down Value": "DECLINING_BALANCE",
    "Double Declining Balance": "DOUBLE_DECLINING",
    "Manual": "STRAIGHT_LINE",  # Default for manual
}

# ERPNext status to DotMac ERP status
ASSET_STATUS_MAP = {
    "Draft": "DRAFT",
    "Submitted": "ACTIVE",
    "Partially Depreciated": "ACTIVE",
    "Fully Depreciated": "FULLY_DEPRECIATED",
    "Sold": "DISPOSED",
    "Scrapped": "DISPOSED",
    "In Maintenance": "ACTIVE",
    "Out of Order": "ACTIVE",
}


def map_depreciation_method(value: Any) -> str:
    """Map ERPNext depreciation method."""
    if not value:
        return "STRAIGHT_LINE"
    return DEPRECIATION_METHOD_MAP.get(str(value), "STRAIGHT_LINE")


def map_asset_status(value: Any) -> str:
    """Map ERPNext asset status."""
    if not value:
        return "DRAFT"
    return ASSET_STATUS_MAP.get(str(value), "ACTIVE")


def calculate_useful_life_months(record: dict[str, Any]) -> int:
    """
    Calculate useful life in months from ERPNext fields.

    ERPNext uses:
    - total_number_of_depreciations: number of depreciation entries
    - frequency_of_depreciation: months between entries (typically 1 or 12)
    """
    total_deps = record.get("total_number_of_depreciations") or 0
    frequency = record.get("frequency_of_depreciation") or 1

    if total_deps == 0:
        return 60  # Default 5 years

    return int(total_deps * frequency)


class AssetMapping(DocTypeMapping):
    """Map ERPNext Asset to DotMac ERP fa.asset."""

    def __init__(self):
        super().__init__(
            source_doctype="Asset",
            target_table="fa.asset",
            fields=[
                FieldMapping(
                    source="name",
                    target="asset_number",
                    required=True,
                    transformer=lambda v: clean_string(v, 50),
                ),
                FieldMapping(
                    source="asset_name",
                    target="asset_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 200),
                ),
                FieldMapping(
                    source="asset_category",
                    target="_category_source_name",  # Resolved later
                    required=True,
                ),
                FieldMapping(
                    source="location",
                    target="_location_source_name",  # Resolved later
                    required=False,
                ),
                FieldMapping(
                    source="purchase_date",
                    target="acquisition_date",
                    required=True,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="available_for_use_date",
                    target="in_service_date",
                    required=False,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="gross_purchase_amount",
                    target="acquisition_cost",
                    required=True,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="depreciation_method",
                    target="depreciation_method",
                    required=False,
                    default="STRAIGHT_LINE",
                    transformer=map_depreciation_method,
                ),
                FieldMapping(
                    source="expected_value_after_useful_life",
                    target="residual_value",
                    required=False,
                    default=0,
                    transformer=lambda v: parse_decimal(v) or 0,
                ),
                FieldMapping(
                    source="opening_accumulated_depreciation",
                    target="accumulated_depreciation",
                    required=False,
                    default=0,
                    transformer=lambda v: parse_decimal(v) or 0,
                ),
                FieldMapping(
                    source="value_after_depreciation",
                    target="net_book_value",
                    required=False,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="status",
                    target="status",
                    required=False,
                    default="ACTIVE",
                    transformer=map_asset_status,
                ),
                FieldMapping(
                    source="serial_no",
                    target="serial_number",
                    required=False,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="disposal_date",
                    target="disposal_date",
                    required=False,
                    transformer=parse_date,
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
        """Transform with additional calculated fields."""
        result = super().transform_record(record)

        # Calculate useful life in months
        result["useful_life_months"] = calculate_useful_life_months(record)

        # Calculate remaining life (approximate)
        # This will be recalculated properly in the sync service
        result["remaining_life_months"] = result["useful_life_months"]

        # Default currency
        result["currency_code"] = "NGN"

        # Calculate NBV if not provided
        if result.get("net_book_value") is None:
            cost = result.get("acquisition_cost") or 0
            accum = result.get("accumulated_depreciation") or 0
            result["net_book_value"] = cost - accum

        return result


class AssetCategoryMapping(DocTypeMapping):
    """Map ERPNext Asset Category to DotMac ERP fa.asset_category."""

    def __init__(self):
        super().__init__(
            source_doctype="Asset Category",
            target_table="fa.asset_category",
            fields=[
                FieldMapping(
                    source="name",
                    target="category_code",
                    required=True,
                    transformer=lambda v: clean_string(v, 30),
                ),
                FieldMapping(
                    source="asset_category_name",
                    target="category_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 100),
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
        """Transform with default depreciation settings."""
        result = super().transform_record(record)

        # Default depreciation settings
        # These should be configured properly for each category
        result["depreciation_method"] = "STRAIGHT_LINE"
        result["useful_life_months"] = 60  # 5 years default
        result["residual_value_percent"] = 0
        result["is_active"] = True

        return result
