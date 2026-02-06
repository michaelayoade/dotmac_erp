"""
AP Posting Result - Outcome of AP posting operations.
"""

import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class APPostingResult:
    """Result of an AP posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    posting_batch_id: Optional[UUID] = None
    message: str = ""
