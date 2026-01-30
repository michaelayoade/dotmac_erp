"""
AR Posting Package - Modular GL posting for AR documents.

This package contains the implementation of AR-to-GL posting operations:
- Invoice posting (revenue recognition)
- Payment posting (cash receipt)
- Tax transaction creation

The main APPostingAdapter in ar_posting_adapter.py serves as a facade
that delegates to the functions in this package.
"""

from app.services.finance.ar.posting.result import ARPostingResult
from app.services.finance.ar.posting.invoice import post_invoice
from app.services.finance.ar.posting.payment import post_payment
from app.services.finance.ar.posting.helpers import create_tax_transactions

__all__ = [
    "ARPostingResult",
    "post_invoice",
    "post_payment",
    "create_tax_transactions",
]
