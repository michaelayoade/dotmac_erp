"""
Contact mapping (Customers/Suppliers) from ERPNext to DotMac Books.
"""
from typing import Any

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    default_currency,
    invert_bool,
    parse_datetime,
)


# ERPNext customer_type to DotMac Books
CUSTOMER_TYPE_MAP = {
    "Company": "COMPANY",
    "Individual": "INDIVIDUAL",
    "Partnership": "COMPANY",
    "Proprietorship": "INDIVIDUAL",
}


def map_customer_type(value: Any) -> str:
    """Map ERPNext customer type."""
    if not value:
        return "COMPANY"
    return CUSTOMER_TYPE_MAP.get(str(value), "COMPANY")


class CustomerMapping(DocTypeMapping):
    """Map ERPNext Customer to DotMac Books ar.customer."""

    def __init__(self):
        super().__init__(
            source_doctype="Customer",
            target_table="ar.customer",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",  # ERPNext internal name
                    required=True,
                ),
                FieldMapping(
                    source="customer_name",
                    target="legal_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 200),
                ),
                FieldMapping(
                    source="customer_name",
                    target="trading_name",  # Use same as legal_name
                    required=False,
                    transformer=lambda v: clean_string(v, 200),
                ),
                FieldMapping(
                    source="customer_type",
                    target="customer_type",
                    required=False,
                    default="COMPANY",
                    transformer=map_customer_type,
                ),
                FieldMapping(
                    source="default_currency",
                    target="currency_code",
                    required=False,
                    transformer=default_currency,
                ),
                FieldMapping(
                    source="tax_id",
                    target="tax_id",
                    required=False,
                    transformer=lambda v: clean_string(v, 50),
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
        """Transform with customer code generation."""
        result = super().transform_record(record)

        # Generate customer code from ERPNext name
        # Truncate to 30 chars for code
        erpnext_name = record.get("name", "")
        result["customer_code"] = clean_string(erpnext_name, 30) or "CUST"

        return result


class SupplierMapping(DocTypeMapping):
    """Map ERPNext Supplier to DotMac Books ap.supplier."""

    def __init__(self):
        super().__init__(
            source_doctype="Supplier",
            target_table="ap.supplier",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",  # ERPNext internal name
                    required=True,
                ),
                FieldMapping(
                    source="supplier_name",
                    target="legal_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 200),
                ),
                FieldMapping(
                    source="supplier_name",
                    target="trading_name",  # Use same as legal_name
                    required=False,
                    transformer=lambda v: clean_string(v, 200),
                ),
                FieldMapping(
                    source="supplier_type",
                    target="supplier_type",
                    required=False,
                    default="COMPANY",
                    transformer=map_customer_type,  # Same mapping logic
                ),
                FieldMapping(
                    source="default_currency",
                    target="currency_code",
                    required=False,
                    transformer=default_currency,
                ),
                FieldMapping(
                    source="tax_id",
                    target="tax_id",
                    required=False,
                    transformer=lambda v: clean_string(v, 50),
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
        """Transform with supplier code generation."""
        result = super().transform_record(record)

        # Generate supplier code from ERPNext name
        erpnext_name = record.get("name", "")
        result["supplier_code"] = clean_string(erpnext_name, 30) or "SUPP"

        return result
