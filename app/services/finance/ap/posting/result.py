"""
AP Posting Result - Outcome of AP posting operations.
"""

import logging

from app.services.finance.posting.base import PostingResult

logger = logging.getLogger(__name__)


class APPostingResult(PostingResult):
    """Result of an AP posting operation."""
