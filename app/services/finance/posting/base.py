"""
Shared posting utilities for GL adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.services.finance.gl.journal import JournalInput, JournalService
from app.services.finance.gl.ledger_posting import LedgerPostingService, PostingRequest


@dataclass
class PostingResult:
    """Result of a posting operation."""

    success: bool
    journal_entry_id: Optional[UUID] = None
    posting_batch_id: Optional[UUID] = None
    message: str = ""


class BasePostingAdapter:
    """Shared helpers for journal creation and ledger posting."""

    @staticmethod
    def make_idempotency_key(
        organization_id: UUID,
        source_module: str,
        source_document_id: UUID,
        action: str = "post",
        version: str = "v1",
    ) -> str:
        return (
            f"{organization_id}:{source_module}:{source_document_id}:{action}:{version}"
        )

    @staticmethod
    def create_and_approve_journal(
        db: Session,
        organization_id: UUID,
        journal_input: JournalInput,
        posted_by_user_id: UUID,
        *,
        error_prefix: str = "Journal creation failed",
    ) -> tuple[Optional[object], Optional[PostingResult]]:
        try:
            journal = JournalService.create_journal(
                db, organization_id, journal_input, posted_by_user_id
            )
            JournalService.submit_journal(
                db, organization_id, journal.journal_entry_id, posted_by_user_id
            )
            JournalService.approve_journal(
                db, organization_id, journal.journal_entry_id, posted_by_user_id
            )
            return journal, None
        except HTTPException as exc:
            return None, PostingResult(
                success=False,
                message=f"{error_prefix}: {exc.detail}",
            )

    @staticmethod
    def post_to_ledger(
        db: Session,
        *,
        organization_id: UUID,
        journal_entry_id: UUID,
        posting_date,
        idempotency_key: str,
        source_module: str,
        correlation_id: Optional[str],
        posted_by_user_id: UUID,
        success_message: str = "Posted successfully",
        error_prefix: str = "Ledger posting failed",
    ) -> PostingResult:
        posting_request = PostingRequest(
            organization_id=organization_id,
            journal_entry_id=journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module=source_module,
            correlation_id=correlation_id,
            posted_by_user_id=posted_by_user_id,
        )

        try:
            posting_result = LedgerPostingService.post_journal_entry(
                db, posting_request
            )

            if not posting_result.success:
                return PostingResult(
                    success=False,
                    journal_entry_id=journal_entry_id,
                    message=f"{error_prefix}: {posting_result.message}",
                )

            return PostingResult(
                success=True,
                journal_entry_id=journal_entry_id,
                posting_batch_id=posting_result.posting_batch_id,
                message=success_message,
            )
        except Exception as exc:
            return PostingResult(
                success=False,
                journal_entry_id=journal_entry_id,
                message=f"{error_prefix}: {str(exc)}",
            )
