"""
AP Posting Module - Modular GL posting for AP documents.

This module provides:
- Invoice posting (supplier invoices → GL)
- Payment posting (supplier payments → GL)
- Reversal posting (undo postings)
- Tax transaction creation
- Asset capitalization integration

Usage:
    from app.services.finance.ap.posting import (
        post_invoice,
        post_payment,
        reverse_invoice_posting,
        APPostingResult,
    )

    result = post_invoice(db, org_id, invoice_id, posting_date, user_id)
"""

from app.services.finance.ap.posting.result import APPostingResult
from app.services.finance.ap.posting.invoice import post_invoice
from app.services.finance.ap.posting.payment import post_payment
from app.services.finance.ap.posting.reversal import reverse_invoice_posting

__all__ = [
    "APPostingResult",
    "post_invoice",
    "post_payment",
    "reverse_invoice_posting",
]
