"""
GLPostingAdapter - GL posting adapter for API routes.

Thin adapter layer for posting journal entries from API endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.finance.gl.journal import JournalService, JournalInput, JournalLineInput
from app.services.common import coerce_uuid


@dataclass
class GLPostingResult:
    """Result of a GL posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    entry_number: Optional[str] = None
    message: Optional[str] = None


class GLPostingAdapter:
    """
    Adapter for posting to GL from various sources.

    Provides a simplified interface for posting journal entries.
    """

    @staticmethod
    def post_manual_journal(
        db: Session,
        organization_id: UUID,
        journal_entry_id: UUID,
        posting_date: date,
        posted_by_user_id: UUID,
        fiscal_period_id: UUID,
    ) -> GLPostingResult:
        """
        Post a manual journal entry.

        Args:
            db: Database session
            organization_id: Organization scope
            journal_entry_id: Journal to post
            posting_date: Posting date
            posted_by_user_id: User posting
            fiscal_period_id: Target fiscal period

        Returns:
            GLPostingResult
        """
        try:
            journal = JournalService.post_journal(
                db=db,
                organization_id=organization_id,
                journal_entry_id=journal_entry_id,
                posted_by_user_id=posted_by_user_id,
            )

            return GLPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                entry_number=journal.journal_number,
                message="Journal posted successfully",
            )
        except Exception as e:
            return GLPostingResult(
                success=False,
                message=str(e),
            )

    @staticmethod
    def create_and_post_journal(
        db: Session,
        organization_id: UUID,
        input: JournalInput,
        created_by_user_id: UUID,
        auto_post: bool = False,
    ) -> GLPostingResult:
        """
        Create and optionally post a journal entry.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Journal input data
            created_by_user_id: User creating
            auto_post: If True, post immediately

        Returns:
            GLPostingResult
        """
        try:
            # Create journal
            journal = JournalService.create_journal(
                db=db,
                organization_id=organization_id,
                input=input,
                created_by_user_id=created_by_user_id,
            )

            if auto_post:
                # Submit and approve (for system-generated entries)
                journal = JournalService.submit_journal(
                    db=db,
                    organization_id=organization_id,
                    journal_entry_id=journal.journal_entry_id,
                    submitted_by_user_id=created_by_user_id,
                )

                # Post (skip approval for system entries)
                journal = JournalService.post_journal(
                    db=db,
                    organization_id=organization_id,
                    journal_entry_id=journal.journal_entry_id,
                    posted_by_user_id=created_by_user_id,
                )

            return GLPostingResult(
                success=True,
                journal_entry_id=journal.journal_entry_id,
                entry_number=journal.journal_number,
                message="Journal created" + (" and posted" if auto_post else ""),
            )
        except Exception as e:
            return GLPostingResult(
                success=False,
                message=str(e),
            )


# Module-level singleton instance
gl_posting_adapter = GLPostingAdapter()
