"""
Warehouse mapping from ERPNext to DotMac ERP.
"""
from typing import Any

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    invert_bool,
    parse_datetime,
)


class WarehouseMapping(DocTypeMapping):
    """Map ERPNext Warehouse to DotMac ERP inv.warehouse."""

    def __init__(self):
        super().__init__(
            source_doctype="Warehouse",
            target_table="inv.warehouse",
            fields=[
                FieldMapping(
                    source="name",
                    target="warehouse_code",
                    required=True,
                    transformer=lambda v: clean_string(v, 30),
                ),
                FieldMapping(
                    source="warehouse_name",
                    target="warehouse_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="parent_warehouse",
                    target="_parent_source_name",  # Resolved later
                    required=False,
                ),
                FieldMapping(
                    source="is_group",
                    target="_is_group",  # Used for hierarchy
                    required=False,
                    default=False,
                ),
                FieldMapping(
                    source="disabled",
                    target="is_active",
                    required=False,
                    default=True,
                    transformer=invert_bool,
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
        """Transform with default warehouse flags."""
        result = super().transform_record(record)

        # Default warehouse capabilities
        # All warehouses can receive and ship unless group
        is_group = result.get("_is_group", False)
        result["is_receiving"] = not is_group
        result["is_shipping"] = not is_group
        result["is_consignment"] = False
        result["is_transit"] = False

        return result
