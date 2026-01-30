"""
AR Posting Result - Outcome of AR posting operations.
"""

from dataclasses import dataclass
from typing import Optional
from uuid import UUID


@dataclass
class ARPostingResult:
    """Result of an AR posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    posting_batch_id: Optional[UUID] = None
    message: str = ""
