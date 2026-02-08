"""
APPostingAdapter - Converts AP documents to GL entries.

This module is a facade that delegates to the modular posting subpackage.
For implementation details, see:
- app/services/finance/ap/posting/invoice.py
- app/services/finance/ap/posting/payment.py
- app/services/finance/ap/posting/reversal.py

Transforms supplier invoices and payments into journal entries
and posts them to the general ledger.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

# Import from modular posting package
from app.services.finance.ap.posting import (
    APPostingResult,
    post_invoice,
    post_payment,
    reverse_invoice_posting,
)

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    "APPostingResult",
    "APPostingAdapter",
    "ap_posting_adapter",
]


class APPostingAdapter:
    """
    Adapter for posting AP documents to the general ledger.

    Converts supplier invoices and payments into journal entries
    and coordinates posting through the LedgerPostingService.

    This class is a facade that delegates to the modular posting functions.
    """

    @staticmethod
    def post_invoice(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: str | None = None,
        use_saga: bool = False,
        correlation_id: str | None = None,
    ) -> APPostingResult:
        """
        Post a supplier invoice to the general ledger.

        Creates a journal entry with:
        - Debit: Expense/Asset accounts (from invoice lines)
        - Credit: AP Control account

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key
            use_saga: If True, use saga pattern for transactional guarantees
            correlation_id: Optional correlation ID for tracing

        Returns:
            APPostingResult with outcome
        """
        return post_invoice(
            db=db,
            organization_id=organization_id,
            invoice_id=invoice_id,
            posting_date=posting_date,
            posted_by_user_id=posted_by_user_id,
            idempotency_key=idempotency_key,
            use_saga=use_saga,
            correlation_id=correlation_id,
        )

    @staticmethod
    def post_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: str | None = None,
    ) -> APPostingResult:
        """
        Post a supplier payment to the general ledger.

        Creates a journal entry with:
        - Debit: AP Control account
        - Credit: Bank/Cash account

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            APPostingResult with outcome
        """
        return post_payment(
            db=db,
            organization_id=organization_id,
            payment_id=payment_id,
            posting_date=posting_date,
            posted_by_user_id=posted_by_user_id,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def reverse_invoice_posting(
        db: Session,
        organization_id: UUID,
        invoice_id: UUID,
        reversal_date: date,
        reversed_by_user_id: UUID,
        reason: str,
    ) -> APPostingResult:
        """
        Reverse a posted invoice's GL entries.

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to reverse
            reversal_date: Date for reversal
            reversed_by_user_id: User reversing
            reason: Reason for reversal

        Returns:
            APPostingResult with reversal outcome
        """
        return reverse_invoice_posting(
            db=db,
            organization_id=organization_id,
            invoice_id=invoice_id,
            reversal_date=reversal_date,
            reversed_by_user_id=reversed_by_user_id,
            reason=reason,
        )


# Module-level singleton instance
ap_posting_adapter = APPostingAdapter()
