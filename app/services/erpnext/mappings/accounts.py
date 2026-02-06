"""
Account mapping from ERPNext to DotMac ERP.
"""

import logging
from typing import Any, Optional

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    default_currency,
    invert_bool,
    parse_datetime,
)

logger = logging.getLogger(__name__)

# ERPNext root_type to DotMac ERP category mapping
ROOT_TYPE_MAP = {
    "Asset": "ASSETS",
    "Liability": "LIABILITIES",
    "Equity": "EQUITY",
    "Income": "REVENUE",
    "Expense": "EXPENSES",
}

# ERPNext account_type to DotMac ERP subledger type
ACCOUNT_TYPE_TO_SUBLEDGER = {
    "Receivable": "AR",
    "Payable": "AP",
    "Bank": "BANK",
    "Cash": "BANK",  # Treat cash as bank subledger
    "Stock": "INVENTORY",
    "Fixed Asset": "ASSET",
    "Depreciation": None,
    "Accumulated Depreciation": None,
}

# Normal balance based on root_type
ROOT_TYPE_NORMAL_BALANCE = {
    "Asset": "DEBIT",
    "Liability": "CREDIT",
    "Equity": "CREDIT",
    "Income": "CREDIT",
    "Expense": "DEBIT",
}


def map_root_type_to_category(root_type: Any) -> str:
    """Map ERPNext root_type to DotMac ERP category."""
    if not root_type:
        return "ASSETS"
    return ROOT_TYPE_MAP.get(str(root_type), "ASSETS")


def map_account_type_to_subledger(account_type: Any) -> Optional[str]:
    """Map ERPNext account_type to DotMac ERP subledger_type."""
    if not account_type:
        return None
    return ACCOUNT_TYPE_TO_SUBLEDGER.get(str(account_type))


def map_root_type_to_normal_balance(root_type: Any) -> str:
    """Map ERPNext root_type to normal balance."""
    if not root_type:
        return "DEBIT"
    return ROOT_TYPE_NORMAL_BALANCE.get(str(root_type), "DEBIT")


class AccountMapping(DocTypeMapping):
    """Map ERPNext Account to DotMac ERP gl.account."""

    def __init__(self):
        super().__init__(
            source_doctype="Account",
            target_table="gl.account",
            fields=[
                FieldMapping(
                    source="name",
                    target="account_code",
                    required=True,
                    transformer=lambda v: clean_string(v, 50),
                ),
                FieldMapping(
                    source="account_name",
                    target="account_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 200),
                ),
                FieldMapping(
                    source="root_type",
                    target="account_category",
                    required=True,
                    transformer=map_root_type_to_category,
                ),
                FieldMapping(
                    source="account_type",
                    target="subledger_type",
                    required=False,
                    transformer=map_account_type_to_subledger,
                ),
                FieldMapping(
                    source="is_group",
                    target="is_header",
                    required=False,
                    default=False,
                ),
                FieldMapping(
                    source="parent_account",
                    target="_parent_source_name",  # Resolved later
                    required=False,
                ),
                FieldMapping(
                    source="account_currency",
                    target="currency_code",
                    required=False,
                    transformer=default_currency,
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
        """Transform with additional calculated fields."""
        result = super().transform_record(record)

        # Add normal balance based on root_type
        result["normal_balance"] = map_root_type_to_normal_balance(
            record.get("root_type")
        )

        return result


class AccountCategoryMapping(DocTypeMapping):
    """
    Map ERPNext account root_type to DotMac ERP gl.account_category.

    ERPNext doesn't have explicit account categories, so we derive them
    from the root_type and account_type fields.
    """

    def __init__(self):
        super().__init__(
            source_doctype="Account",  # Derived from Account
            target_table="gl.account_category",
            fields=[
                FieldMapping(
                    source="root_type",
                    target="category_code",
                    required=True,
                    transformer=map_root_type_to_category,
                ),
                FieldMapping(
                    source="root_type",
                    target="category_name",
                    required=True,
                    transformer=lambda v: str(v) if v else "Assets",
                ),
            ],
            unique_key="root_type",
        )
