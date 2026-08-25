"""
Shared posting utilities for GL adapters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast
from uuid import UUID

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.services.common import coerce_uuid
from app.services.finance.gl.journal import JournalInput, JournalService
from app.services.finance.gl.ledger_posting import LedgerPostingService, PostingRequest

logger = logging.getLogger(__name__)


@dataclass
class PostingResult:
    """Result of a posting operation.

    `idempotent_replay` is carried deliberately. `LedgerPostingService` sets it
    when a batch already exists for the idempotency key, and this wrapper used
    to drop it — so every adapter saw `success=True` and could not tell "I
    posted" from "someone already had". Callers that create a journal before
    posting need that distinction: without it the journal they just created is
    left stranded and nothing says so. Losing exactly this signal is how 12,117
    APPROVED bank-fee journals accumulated.
    """

    success: bool
    journal_entry_id: UUID | None = None
    posting_batch_id: UUID | None = None
    message: str = ""
    idempotent_replay: bool = False


class BasePostingAdapter:
    """Shared helpers for journal creation and ledger posting."""

    @staticmethod
    def require_journal(
        journal: JournalEntry | None,
        *,
        context: str = "posting operation",
    ) -> JournalEntry:
        """Return a concrete journal when a successful posting expects one."""
        if journal is None:
            raise RuntimeError(f"{context} completed without creating a journal")
        return journal

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
    def submit_and_approve_as_system(
        db: Session,
        organization_id: UUID,
        journal: JournalEntry,
        posted_by_user_id: UUID,
    ) -> None:
        """Submit and approve a journal on behalf of an automated actor.

        For system postings (sync, reconciliation, backfill) the same actor
        creates and approves, which segregation of duties refuses by design.
        The bypass is deliberate and is recorded on the journal.

        Extracted so there is ONE implementation. It previously lived inline in
        `create_and_approve_journal`, and any other path that needed to approve
        an existing journal — recovering a DRAFT left by a failed post, for
        instance — either duplicated the `except` block or, worse, called
        `approve_journal` directly and raised an uncaught `HTTPException` the
        first time SoD fired.
        """
        JournalService.submit_journal(
            db, organization_id, journal.journal_entry_id, posted_by_user_id
        )
        try:
            JournalService.approve_journal(
                db, organization_id, journal.journal_entry_id, posted_by_user_id
            )
        except HTTPException as sod_exc:
            if "Segregation of duties" not in str(sod_exc.detail):
                raise
            from app.models.finance.gl.journal_entry import JournalStatus

            journal.status = JournalStatus.APPROVED
            journal.approved_by_user_id = posted_by_user_id
            journal.approved_at = datetime.now(UTC)
            db.flush()
            logger.info(
                "Auto-approved journal %s (SoD bypass for system posting)",
                journal.journal_entry_id,
            )

    @staticmethod
    def create_and_approve_journal(
        db: Session,
        organization_id: UUID,
        journal_input: JournalInput,
        posted_by_user_id: UUID,
        *,
        error_prefix: str = "Journal creation failed",
    ) -> tuple[JournalEntry, PostingResult | None]:
        try:
            journal = JournalService.create_journal(
                db, organization_id, journal_input, posted_by_user_id
            )
            BasePostingAdapter.submit_and_approve_as_system(
                db, organization_id, journal, posted_by_user_id
            )
            return journal, None
        except HTTPException as exc:
            return cast(JournalEntry, None), PostingResult(
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
        correlation_id: str | None,
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
                BasePostingAdapter._revert_unposted_journal(
                    db, journal_entry_id, posting_result.message
                )
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
                idempotent_replay=bool(
                    getattr(posting_result, "idempotent_replay", False)
                ),
            )
        except Exception as exc:
            BasePostingAdapter._revert_unposted_journal(db, journal_entry_id, str(exc))
            return PostingResult(
                success=False,
                journal_entry_id=journal_entry_id,
                message=f"{error_prefix}: {str(exc)}",
            )

    @staticmethod
    def create_approve_and_post_journal(
        db: Session,
        organization_id: UUID,
        journal_input: JournalInput,
        posted_by_user_id: UUID,
        *,
        posting_date,
        idempotency_key: str,
        source_module: str,
        correlation_id: str | None,
        success_message: str,
        creation_error_prefix: str = "Journal creation failed",
        ledger_error_prefix: str = "Ledger posting failed",
    ) -> tuple[JournalEntry | None, PostingResult]:
        """Create, approve, and post a journal for a source document."""
        journal, creation_error = BasePostingAdapter.create_and_approve_journal(
            db,
            organization_id,
            journal_input,
            posted_by_user_id,
            error_prefix=creation_error_prefix,
        )
        if creation_error:
            return None, creation_error

        posting_result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=organization_id,
            journal_entry_id=journal.journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module=source_module,
            correlation_id=correlation_id,
            posted_by_user_id=posted_by_user_id,
            success_message=success_message,
            error_prefix=ledger_error_prefix,
        )
        return journal, posting_result

    @staticmethod
    def _revert_unposted_journal(
        db: Session,
        journal_entry_id: UUID,
        reason: str,
    ) -> None:
        """Revert an APPROVED-but-unposted journal back to DRAFT.

        Invariant enforced: a journal in APPROVED status must either be
        currently being posted or already point to a successful posting
        batch. When LedgerPostingService rejects (e.g. the period is
        closed), the approve-step that ran earlier must be undone so the
        journal does not strand as APPROVED-but-not-posted.

        Only reverts when status is APPROVED *and* posting_batch_id is
        NULL — never touches a journal that did partially post.
        """
        journal = db.get(JournalEntry, coerce_uuid(journal_entry_id))
        if (
            journal is None
            or journal.status != JournalStatus.APPROVED
            or journal.posting_batch_id is not None
        ):
            return

        journal.status = JournalStatus.DRAFT
        journal.approved_by_user_id = None
        journal.approved_at = None
        db.flush()
        logger.warning(
            "Reverted journal %s APPROVED->DRAFT after posting failure: %s",
            journal.journal_number,
            reason,
        )
