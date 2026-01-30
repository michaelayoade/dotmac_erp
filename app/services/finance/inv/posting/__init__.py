"""
INV Posting Package - Modular GL posting for inventory transactions.

This package contains the implementation of inventory-to-GL posting operations:
- Receipt posting (goods received)
- Issue posting (goods issued/sold)
- Adjustment posting (count adjustments, scrap)
- Transaction router (auto-routes based on type)

The main INVPostingAdapter in inv_posting_adapter.py serves as a facade
that delegates to the functions in this package.
"""

from app.services.finance.inv.posting.result import INVPostingResult
from app.services.finance.inv.posting.receipt import post_receipt
from app.services.finance.inv.posting.issue import post_issue
from app.services.finance.inv.posting.adjustment import post_adjustment
from app.services.finance.inv.posting.router import post_transaction

__all__ = [
    "INVPostingResult",
    "post_receipt",
    "post_issue",
    "post_adjustment",
    "post_transaction",
]
