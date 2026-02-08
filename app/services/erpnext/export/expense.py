"""
Expense Export Services - Push DotMac expense data to ERPNext.

During transition, expense claims created/modified in DotMac
need to sync back to ERPNext for approval workflows.
"""

import logging
import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.expense.expense_claim import (
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseClaimStatus,
)
from app.services.erpnext.client import ERPNextClient
from app.services.formatters import format_date as _base_format_date

from .base import BaseExportService

logger = logging.getLogger(__name__)

# Status mapping: DotMac → ERPNext
EXPENSE_STATUS_EXPORT_MAP = {
    ExpenseClaimStatus.DRAFT: "Draft",
    ExpenseClaimStatus.SUBMITTED: "Unpaid",
    ExpenseClaimStatus.PENDING_APPROVAL: "Unpaid",
    ExpenseClaimStatus.APPROVED: "Unpaid",
    ExpenseClaimStatus.REJECTED: "Rejected",
    ExpenseClaimStatus.PAID: "Paid",
    ExpenseClaimStatus.CANCELLED: "Cancelled",
}


class ExpenseCategoryExportService(BaseExportService[ExpenseCategory]):
    """Export Expense Categories to ERPNext (as Expense Claim Type)."""

    target_doctype = "Expense Claim Type"
    source_table = "expense.expense_category"

    def get_pending_exports(self) -> list[ExpenseCategory]:
        """Get expense categories that need to be exported."""
        stmt = select(ExpenseCategory).where(
            ExpenseCategory.organization_id == self.organization_id,
            ExpenseCategory.is_active == True,
        )
        return list(self.db.execute(stmt).scalars().all())

    def transform_for_export(self, entity: ExpenseCategory) -> dict[str, Any]:
        """Transform ExpenseCategory to ERPNext Expense Claim Type format."""
        return {
            "expense_type": entity.category_name,
            "description": entity.description or "",
        }

    def get_entity_id(self, entity: ExpenseCategory) -> uuid.UUID:
        return entity.category_id

    def get_erpnext_id(self, entity: ExpenseCategory) -> str | None:
        return entity.erpnext_id

    def set_erpnext_id(self, entity: ExpenseCategory, erpnext_id: str) -> None:
        entity.erpnext_id = erpnext_id
        entity.last_synced_at = datetime.utcnow()


class ExpenseClaimExportService(BaseExportService[ExpenseClaim]):
    """Export Expense Claims to ERPNext."""

    target_doctype = "Expense Claim"
    source_table = "expense.expense_claim"

    def __init__(
        self,
        db: Session,
        client: ERPNextClient,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        company: str,
    ):
        super().__init__(db, client, organization_id, user_id)
        self.company = company

    def get_pending_exports(self) -> list[ExpenseClaim]:
        """Get expense claims that need to be exported."""
        # Export claims that:
        # 1. Are not drafts (only export submitted claims)
        # 2. Are not cancelled
        stmt = select(ExpenseClaim).where(
            ExpenseClaim.organization_id == self.organization_id,
            ExpenseClaim.status.in_(
                [
                    ExpenseClaimStatus.SUBMITTED,
                    ExpenseClaimStatus.PENDING_APPROVAL,
                    ExpenseClaimStatus.APPROVED,
                    ExpenseClaimStatus.PAID,
                ]
            ),
        )
        return list(self.db.execute(stmt).scalars().all())

    def transform_for_export(self, entity: ExpenseClaim) -> dict[str, Any]:
        """Transform ExpenseClaim to ERPNext format with child items."""
        data: dict[str, Any] = {
            "company": self.company,
            "posting_date": _format_date(entity.claim_date)
            or datetime.utcnow().date().isoformat(),
            "expense_approver": None,  # Will be set by ERPNext workflow
        }

        # Status (ERPNext uses approval_status and status differently)
        EXPENSE_STATUS_EXPORT_MAP.get(entity.status, "Draft")
        data["approval_status"] = (
            "Approved"
            if entity.status
            in [
                ExpenseClaimStatus.APPROVED,
                ExpenseClaimStatus.PAID,
            ]
            else "Draft"
        )

        # Employee (required for ERPNext)
        if entity.employee and entity.employee.erpnext_id:
            data["employee"] = entity.employee.erpnext_id
        else:
            # ERPNext requires an employee - skip if not linked
            raise ValueError("ExpenseClaim must have an employee with ERPNext ID")

        # Amounts
        data["total_claimed_amount"] = float(entity.total_claimed_amount or 0)
        if entity.total_approved_amount is not None:
            data["total_sanctioned_amount"] = float(entity.total_approved_amount)

        # Payable account (if configured)
        if entity.employee and entity.employee.default_payroll_payable_account_id:
            # Would need to resolve to ERPNext account name
            pass

        # Task/Project (ERPNext links to Task)
        if entity.project_id:
            # Would need to resolve project to ERPNext project name
            pass

        # Child items (expense details)
        data["expenses"] = []
        for item in entity.items:
            item_data = self._transform_expense_item(item)
            data["expenses"].append(item_data)

        # Remarks
        if entity.purpose:
            data["remark"] = entity.purpose

        return data

    def _transform_expense_item(self, item: ExpenseClaimItem) -> dict[str, Any]:
        """Transform a single expense item for ERPNext child table."""
        item_data: dict[str, Any] = {
            "expense_date": _format_date(item.expense_date),
            "claim_amount": float(item.claimed_amount),
            "description": item.description or "",
        }

        # Expense type (category)
        if item.category and item.category.erpnext_id:
            item_data["expense_type"] = item.category.erpnext_id

        # Sanctioned amount (approved)
        if item.approved_amount is not None:
            item_data["sanctioned_amount"] = float(item.approved_amount)
        else:
            item_data["sanctioned_amount"] = float(item.claimed_amount)

        return item_data

    def get_entity_id(self, entity: ExpenseClaim) -> uuid.UUID:
        return entity.claim_id

    def get_erpnext_id(self, entity: ExpenseClaim) -> str | None:
        return entity.erpnext_id

    def set_erpnext_id(self, entity: ExpenseClaim, erpnext_id: str) -> None:
        entity.erpnext_id = erpnext_id
        entity.last_synced_at = datetime.utcnow()

    def submit_claim(self, entity: ExpenseClaim) -> tuple[bool, str | None]:
        """
        Submit expense claim in ERPNext for approval workflow.

        This is typically called after exporting the claim.
        """
        if entity.status not in [
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        ]:
            return False, "Claim must be submitted before ERPNext submission"

        return self.submit_document(entity)


def _format_date(d: date | None) -> str | None:
    """Format date for ERPNext API (YYYY-MM-DD)."""
    return _base_format_date(d) or None
