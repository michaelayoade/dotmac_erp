"""
AR Posting Result - Outcome of AR posting operations.
"""

import logging

from app.services.finance.posting.base import PostingResult

logger = logging.getLogger(__name__)


class ARPostingResult(PostingResult):
    """Result of an AR posting operation."""
