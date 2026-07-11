"""Shared idempotency helpers for document-to-GL posting."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExistingPosting:
    """Existing GL posting found for a source document."""

    journal_entry_id: UUID
    message: str


class PostingIdempotencyService:
    """Find and backfill existing journal postings for source documents."""

    @staticmethod
    def source_journal_exists(
        db: Session,
        *,
        source_module: str,
        source_document_type: str,
        source_document_id: UUID,
        exclude_reversal_journals: bool = False,
    ) -> bool:
        """Return whether an active journal exists for a source document."""
        conditions = [
            JournalEntry.source_module == source_module,
            JournalEntry.source_document_type == source_document_type,
            JournalEntry.source_document_id == source_document_id,
            JournalEntry.status.notin_([JournalStatus.VOID, JournalStatus.REVERSED]),
        ]
        if exclude_reversal_journals:
            conditions.append(JournalEntry.journal_type != JournalType.REVERSAL)

        return (
            db.scalar(
                select(JournalEntry.journal_entry_id)
                .where(*conditions)
                .limit(1)
            )
            is not None
        )

    @staticmethod
    def resolve_existing_journal(
        db: Session,
        *,
        document: object,
        document_id: UUID,
        source_module: str,
        source_document_type: str,
        direct_message: str,
        backfill_message: str,
        log_label: str,
        journal_entry_attr: str = "journal_entry_id",
    ) -> ExistingPosting | None:
        """Return an existing posting if the source document was already posted.

        The primary guard checks the document's journal reference. The secondary
        guard finds a non-void/non-reversed journal by source document and
        backfills the document reference, protecting imports and retries where
        the journal exists but the source document was not updated.
        """
        current_journal_id = getattr(document, journal_entry_attr, None)
        if current_journal_id is not None:
            return ExistingPosting(
                journal_entry_id=current_journal_id,
                message=direct_message,
            )

        existing_journal = db.scalar(
            select(JournalEntry).where(
                JournalEntry.source_module == source_module,
                JournalEntry.source_document_type == source_document_type,
                JournalEntry.source_document_id == document_id,
                JournalEntry.status.notin_([JournalStatus.VOID, JournalStatus.REVERSED]),
                JournalEntry.journal_type != JournalType.REVERSAL,
            )
        )
        if not existing_journal:
            return None

        setattr(document, journal_entry_attr, existing_journal.journal_entry_id)
        db.flush()
        logger.info(
            "%s %s already has journal %s - backfilled reference",
            log_label,
            document_id,
            existing_journal.journal_number,
        )
        return ExistingPosting(
            journal_entry_id=existing_journal.journal_entry_id,
            message=backfill_message,
        )
