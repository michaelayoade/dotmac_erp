"""
ARPostingAdapter - Converts AR documents to GL entries.

This module is a facade that delegates to the modular posting subpackage.
For implementation details, see:
- app/services/finance/ar/posting/invoice.py
- app/services/finance/ar/posting/payment.py

Transforms invoices and payments into journal entries
and posts them to the general ledger.
"""

from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

# Import from modular posting package
from app.services.finance.ar.posting import (
    ARPostingResult,
    post_invoice,
    post_payment,
)

# Re-export for backward compatibility
__all__ = [
    "ARPostingResult",
    "ARPostingAdapter",
    "ar_posting_adapter",
]


class ARPostingAdapter:
    """
    Adapter for posting AR documents to the general ledger.

    Converts invoices and payments into journal entries
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
        idempotency_key: Optional[str] = None,
    ) -> ARPostingResult:
        """
        Post an AR invoice to the general ledger.

        Creates a journal entry with:
        - Debit: AR Control account
        - Credit: Revenue accounts (from invoice lines)

        Args:
            db: Database session
            organization_id: Organization scope
            invoice_id: Invoice to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            ARPostingResult with outcome
        """
        return post_invoice(
            db=db,
            organization_id=organization_id,
            invoice_id=invoice_id,
            posting_date=posting_date,
            posted_by_user_id=posted_by_user_id,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def post_payment(
        db: Session,
        organization_id: UUID,
        payment_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> ARPostingResult:
        """
        Post a customer payment to the general ledger.

        Creates a journal entry with:
        - Debit: Bank/Cash account
        - Credit: AR Control account

        Args:
            db: Database session
            organization_id: Organization scope
            payment_id: Payment to post
            posting_date: Date for the GL posting
            posted_by_user_id: User posting
            idempotency_key: Optional idempotency key

        Returns:
            ARPostingResult with outcome
        """
        return post_payment(
            db=db,
            organization_id=organization_id,
            payment_id=payment_id,
            posting_date=posting_date,
            posted_by_user_id=posted_by_user_id,
            idempotency_key=idempotency_key,
        )


# Module-level singleton instance
ar_posting_adapter = ARPostingAdapter()
