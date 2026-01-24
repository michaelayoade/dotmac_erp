"""
Material Request Entity Mappings from ERPNext to DotMac ERP.

Maps ERPNext Material Request DocTypes to DotMac inv schema:
- Material Request → inv.material_request (header)
- Material Request Item → inv.material_request_item (line items)
"""
from dataclasses import dataclass, field
from typing import Any

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    parse_date,
    parse_datetime,
    parse_decimal,
)


# ERPNext Material Request status to DotMac MaterialRequestStatus
MATERIAL_REQUEST_STATUS_MAP = {
    "Draft": "DRAFT",
    "Submitted": "SUBMITTED",
    "Pending": "SUBMITTED",  # Alias
    "Partially Ordered": "PARTIALLY_ORDERED",
    "Ordered": "ORDERED",
    "Issued": "ISSUED",
    "Transferred": "TRANSFERRED",
    "Received": "ORDERED",  # Alias for received requests
    "Cancelled": "CANCELLED",
    "Stopped": "CANCELLED",  # Alias
}


def map_material_request_status(value: Any) -> str:
    """Map ERPNext Material Request status to DotMac enum value."""
    if value is None:
        return "DRAFT"
    return MATERIAL_REQUEST_STATUS_MAP.get(str(value), "DRAFT")


# ERPNext Material Request type to DotMac MaterialRequestType
MATERIAL_REQUEST_TYPE_MAP = {
    "Purchase": "PURCHASE",
    "Material Transfer": "TRANSFER",
    "Material Issue": "ISSUE",
    "Manufacture": "MANUFACTURE",
    "Customer Provided": "ISSUE",  # Treat customer-provided as issue
}


def map_material_request_type(value: Any) -> str:
    """Map ERPNext Material Request type to DotMac enum value."""
    if value is None:
        return "PURCHASE"
    return MATERIAL_REQUEST_TYPE_MAP.get(str(value), "PURCHASE")


@dataclass
class MaterialRequestMapping(DocTypeMapping):
    """
    Map ERPNext Material Request to DotMac ERP inv.material_request.

    ERPNext Material Request fields:
    - name: unique identifier (MAT-REQ-00001)
    - material_request_type: Purchase, Material Transfer, Material Issue, Manufacture
    - status: Draft, Submitted, Ordered, etc.
    - schedule_date: required-by date
    - set_warehouse: default target warehouse
    - requested_by: employee who created request (link to User)
    """

    source_doctype: str = "Material Request"
    target_table: str = "inv.material_request"
    unique_key: str = "name"
    fields: list[FieldMapping] = field(default_factory=lambda: [
        FieldMapping("name", "_source_name", required=True),
        FieldMapping(
            "material_request_type",
            "request_type",
            required=True,
            transformer=map_material_request_type,
        ),
        FieldMapping(
            "status",
            "status",
            required=True,
            transformer=map_material_request_status,
        ),
        FieldMapping("schedule_date", "schedule_date", transformer=parse_date),
        # Reference fields - will be resolved in sync service
        FieldMapping("set_warehouse", "_warehouse_source_name"),
        FieldMapping("requested_by", "_requested_by_user"),  # User email
        # Remarks/notes
        FieldMapping(
            "reason",
            "remarks",
            transformer=lambda v: clean_string(v, 2000),
        ),
        FieldMapping("modified", "_source_modified", transformer=parse_datetime),
    ])

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with request number generation."""
        result = super().transform_record(record)

        # Generate request_number from ERPNext name
        erpnext_name = record.get("name", "")
        if erpnext_name.startswith("MAT-REQ-"):
            result["request_number"] = erpnext_name
        else:
            # Use name as request number if it doesn't match expected format
            result["request_number"] = clean_string(erpnext_name, 50) or "MR-UNKNOWN"

        return result


@dataclass
class MaterialRequestItemMapping(DocTypeMapping):
    """
    Map ERPNext Material Request Item to DotMac ERP inv.material_request_item.

    ERPNext Material Request Item fields:
    - name: unique identifier for the row
    - item_code: reference to Item
    - warehouse: target warehouse for this item
    - qty: requested quantity
    - ordered_qty: quantity already ordered
    - stock_uom: unit of measure
    - schedule_date: required-by date (can override header)
    - project: optional project link
    """

    source_doctype: str = "Material Request Item"
    target_table: str = "inv.material_request_item"
    unique_key: str = "name"
    fields: list[FieldMapping] = field(default_factory=lambda: [
        FieldMapping("name", "_source_name", required=True),
        # Item reference - will be resolved via SyncEntity
        FieldMapping("item_code", "_item_source_name", required=True),
        # Warehouse reference
        FieldMapping("warehouse", "_warehouse_source_name"),
        # Quantities
        FieldMapping("qty", "requested_qty", required=True, transformer=parse_decimal),
        FieldMapping(
            "ordered_qty",
            "ordered_qty",
            default=0,
            transformer=parse_decimal,
        ),
        FieldMapping("stock_uom", "uom", transformer=lambda v: clean_string(v, 20)),
        # Schedule date
        FieldMapping("schedule_date", "schedule_date", transformer=parse_date),
        # Cross-module links
        FieldMapping("project", "_project_source_name"),
        # Modified timestamp
        FieldMapping("modified", "_source_modified", transformer=parse_datetime),
    ])

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with default quantity handling."""
        result = super().transform_record(record)

        # Ensure ordered_qty has a default
        if result.get("ordered_qty") is None:
            result["ordered_qty"] = 0

        return result
