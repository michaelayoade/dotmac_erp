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

from app.services.inventory.posting.result import INVPostingResult
from app.services.inventory.posting.receipt import post_receipt
from app.services.inventory.posting.issue import post_issue
from app.services.inventory.posting.adjustment import post_adjustment
from app.services.inventory.posting.router import post_transaction

__all__ = [
    "INVPostingResult",
    "post_receipt",
    "post_issue",
    "post_adjustment",
    "post_transaction",
]
