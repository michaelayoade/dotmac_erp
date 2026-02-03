"""
INVPostingAdapter - Converts inventory transactions to GL entries.

This module is a facade that delegates to the modular posting subpackage.
For implementation details, see:
- app/services/finance/inv/posting/receipt.py
- app/services/finance/inv/posting/issue.py
- app/services/finance/inv/posting/adjustment.py

Posts inventory movements (receipts, issues, adjustments) to the general ledger.
"""

from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

# Import from modular posting package
from app.services.inventory.posting import (
    INVPostingResult,
    post_receipt,
    post_issue,
    post_adjustment,
    post_transaction,
)

# Re-export for backward compatibility
__all__ = [
    "INVPostingResult",
    "INVPostingAdapter",
    "inv_posting_adapter",
]


class INVPostingAdapter:
    """
    Adapter for posting inventory transactions to the general ledger.

    Converts receipts, issues, and adjustments into journal entries.

    This class is a facade that delegates to the modular posting functions.
    """

    @staticmethod
    def post_receipt(
        db: Session,
        organization_id: UUID,
        transaction_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        ap_control_account_id: Optional[UUID] = None,
        idempotency_key: Optional[str] = None,
    ) -> INVPostingResult:
        """
        Post an inventory receipt to the general ledger.

        Creates journal entry:
        - Debit: Inventory account
        - Credit: AP Control or Goods Received Not Invoiced

        Args:
            db: Database session
            organization_id: Organization scope
            transaction_id: Transaction to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            ap_control_account_id: AP control account for the credit side
            idempotency_key: Optional idempotency key

        Returns:
            INVPostingResult with outcome
        """
        return post_receipt(
            db=db,
            organization_id=organization_id,
            transaction_id=transaction_id,
            posting_date=posting_date,
            posted_by_user_id=posted_by_user_id,
            ap_control_account_id=ap_control_account_id,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def post_issue(
        db: Session,
        organization_id: UUID,
        transaction_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        expense_account_id: Optional[UUID] = None,
        idempotency_key: Optional[str] = None,
    ) -> INVPostingResult:
        """
        Post an inventory issue to the general ledger.

        Creates journal entry:
        - Debit: COGS or Expense account
        - Credit: Inventory account

        Args:
            db: Database session
            organization_id: Organization scope
            transaction_id: Transaction to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            expense_account_id: Override expense account (defaults to COGS)
            idempotency_key: Optional idempotency key

        Returns:
            INVPostingResult with outcome
        """
        return post_issue(
            db=db,
            organization_id=organization_id,
            transaction_id=transaction_id,
            posting_date=posting_date,
            posted_by_user_id=posted_by_user_id,
            expense_account_id=expense_account_id,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def post_adjustment(
        db: Session,
        organization_id: UUID,
        transaction_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> INVPostingResult:
        """
        Post an inventory adjustment to the general ledger.

        Creates journal entry:
        - Positive adjustment: Debit Inventory, Credit Adjustment account
        - Negative adjustment: Debit Adjustment account, Credit Inventory

        Args:
            db: Database session
            organization_id: Organization scope
            transaction_id: Transaction to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            INVPostingResult with outcome
        """
        return post_adjustment(
            db=db,
            organization_id=organization_id,
            transaction_id=transaction_id,
            posting_date=posting_date,
            posted_by_user_id=posted_by_user_id,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def post_transaction(
        db: Session,
        organization_id: UUID,
        transaction_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> INVPostingResult:
        """
        Post any inventory transaction to the general ledger.

        Routes to the appropriate posting method based on transaction type.

        Args:
            db: Database session
            organization_id: Organization scope
            transaction_id: Transaction to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            INVPostingResult with outcome
        """
        return post_transaction(
            db=db,
            organization_id=organization_id,
            transaction_id=transaction_id,
            posting_date=posting_date,
            posted_by_user_id=posted_by_user_id,
            idempotency_key=idempotency_key,
        )


# Module-level singleton instance
inv_posting_adapter = INVPostingAdapter()
