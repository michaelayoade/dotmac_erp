"""
Expense Entity Mappings from ERPNext to DotMac ERP.

Maps ERPNext Expense DocTypes to DotMac expense schema:
- Expense Claim Type → expense.expense_category
- Expense Claim → expense.expense_claim (with items)
"""

import logging
from typing import Any

from app.config import settings

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    parse_date,
    parse_datetime,
    parse_decimal,
)

logger = logging.getLogger(__name__)

# ERPNext Expense Claim status to DotMac ExpenseClaimStatus
EXPENSE_STATUS_MAP = {
    "Draft": "DRAFT",
    "Unpaid": "SUBMITTED",
    "Unsanctioned": "PENDING_APPROVAL",
    "Pending": "PENDING_APPROVAL",
    "Approved": "APPROVED",
    "Rejected": "REJECTED",
    "Paid": "PAID",
    "Cancelled": "CANCELLED",
}


def map_expense_status(value: Any) -> str:
    """Map ERPNext expense claim status."""
    if not value:
        return "DRAFT"
    return EXPENSE_STATUS_MAP.get(str(value), "SUBMITTED")


def map_approval_status(record: dict) -> str:
    """Map status based on approval_status and status fields."""
    approval_status = record.get("approval_status", "")
    status = record.get("status", "")

    if approval_status == "Approved":
        if status == "Paid":
            return "PAID"
        return "APPROVED"
    elif approval_status == "Rejected":
        return "REJECTED"
    elif approval_status == "Draft":
        return "DRAFT"
    else:
        return map_expense_status(status)


class ExpenseCategoryMapping(DocTypeMapping):
    """Map ERPNext Expense Claim Type to DotMac ERP expense.expense_category."""

    def __init__(self):
        super().__init__(
            source_doctype="Expense Claim Type",
            target_table="expense.expense_category",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="expense_type",
                    target="category_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 200),
                ),
                FieldMapping(
                    source="description",
                    target="description",
                    required=False,
                    transformer=lambda v: clean_string(v, 500),
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
        """Transform with category code generation."""
        result = super().transform_record(record)

        # Generate category code from expense_type
        expense_type = record.get("expense_type") or record.get("name", "")
        code = expense_type.upper().replace(" ", "_").replace("-", "_")
        result["category_code"] = clean_string(code, 30) or "EXP"

        # Set defaults
        result["is_active"] = True
        result["requires_receipt"] = True

        return result


class ExpenseClaimMapping(DocTypeMapping):
    """Map ERPNext Expense Claim to DotMac ERP expense.expense_claim."""

    def __init__(self):
        super().__init__(
            source_doctype="Expense Claim",
            target_table="expense.expense_claim",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                # Employee reference
                FieldMapping(
                    source="employee",
                    target="_employee_source_name",
                    required=True,
                ),
                # Approver reference
                FieldMapping(
                    source="expense_approver",
                    target="_approver_user",
                    required=False,
                ),
                # Date
                FieldMapping(
                    source="posting_date",
                    target="claim_date",
                    required=True,
                    transformer=parse_date,
                ),
                # Amounts
                FieldMapping(
                    source="total_claimed_amount",
                    target="total_claimed_amount",
                    required=True,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="total_sanctioned_amount",
                    target="total_approved_amount",
                    required=False,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="total_amount_reimbursed",
                    target="_amount_reimbursed",  # For tracking paid status
                    required=False,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="payment_reference",
                    target="payment_reference",
                    required=False,
                    transformer=lambda v: clean_string(v, 100),
                ),
                # Purpose/remarks
                FieldMapping(
                    source="remark",
                    target="purpose",
                    required=False,
                    default="Expense Reimbursement",
                    transformer=lambda v: (
                        clean_string(v, 500) or "Expense Reimbursement"
                    ),
                ),
                # Cost center reference
                FieldMapping(
                    source="cost_center",
                    target="_cost_center_source_name",
                    required=False,
                ),
                # Project reference
                FieldMapping(
                    source="project",
                    target="_project_source_name",
                    required=False,
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
        """Transform with claim number and status generation."""
        result = super().transform_record(record)

        # Generate claim number from ERPNext name
        erpnext_name = record.get("name", "")
        if erpnext_name.startswith("HR-EXP-"):
            result["claim_number"] = erpnext_name.replace("HR-", "")
        else:
            result["claim_number"] = clean_string(erpnext_name, 30) or "EXP"

        # Map status based on approval_status and status fields
        result["status"] = map_approval_status(record)

        # Set currency default
        result["currency_code"] = settings.default_functional_currency_code

        # Set net payable
        approved = result.get("total_approved_amount")
        if approved:
            result["net_payable_amount"] = approved

        # Some ERPNext versions expose paid date/reference only on full doc.
        paid_on = parse_date(record.get("paid_on")) or parse_date(
            record.get("paid_date")
        )
        if paid_on:
            result["paid_on"] = paid_on
        if not result.get("payment_reference"):
            payment_ref = clean_string(record.get("payment_reference"), 100)
            if payment_ref:
                result["payment_reference"] = payment_ref

        # Clean up internal tracking field
        result.pop("_amount_reimbursed", None)

        return result


class ExpenseClaimItemMapping(DocTypeMapping):
    """Map ERPNext Expense Claim Detail to DotMac ERP expense.expense_claim_item."""

    def __init__(self):
        super().__init__(
            source_doctype="Expense Claim Detail",
            target_table="expense.expense_claim_item",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                # Expense date
                FieldMapping(
                    source="expense_date",
                    target="expense_date",
                    required=True,
                    transformer=parse_date,
                ),
                # Expense type reference
                FieldMapping(
                    source="expense_type",
                    target="_expense_type_source_name",
                    required=True,
                ),
                # Description
                FieldMapping(
                    source="description",
                    target="description",
                    required=False,
                    transformer=lambda v: clean_string(v, 500),
                ),
                # Amounts
                FieldMapping(
                    source="amount",
                    target="claimed_amount",
                    required=True,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="sanctioned_amount",
                    target="approved_amount",
                    required=False,
                    transformer=parse_decimal,
                ),
                # Cost center reference
                FieldMapping(
                    source="cost_center",
                    target="_cost_center_source_name",
                    required=False,
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
        """Transform expense item record."""
        result = super().transform_record(record)
        return result
