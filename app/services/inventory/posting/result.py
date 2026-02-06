"""
INV Posting Result - Outcome of inventory posting operations.
"""

import logging

from app.services.finance.posting.base import PostingResult

logger = logging.getLogger(__name__)


class INVPostingResult(PostingResult):
    """Result of an inventory posting operation."""
