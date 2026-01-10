"""
ReversalService - Controlled journal entry reversals.

Creates reversal entries with proper linking and audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.ifrs.gl.journal_entry_line import JournalEntryLine
from app.models.ifrs.core_config.numbering_sequence import SequenceType
from app.services.common import coerce_uuid
from app.services.ifrs.gl.period_guard import PeriodGuardService
from app.services.ifrs.gl.ledger_posting import LedgerPostingService, PostingRequest
from app.services.ifrs.platform.sequence import SequenceService
from app.services.response import ListResponseMixin


@dataclass
class ReversalResult:
    """Result of a reversal operation."""

    success: bool
    reversal_journal_id: Optional[UUID] = None
    reversal_journal_number: Optional[str] = None
    message: str = ""


class ReversalService(ListResponseMixin):
    """
    Service for creating controlled journal reversals.

    Enforces reversal rules, creates linked reversal entries,
    and maintains audit trail.
    """

    @staticmethod
    def create_reversal(
        db: Session,
        organization_id: UUID,
        original_journal_id: UUID,
        reversal_date: date,
        created_by_user_id: UUID,
        reason: str,
        auto_post: bool = False,
        idempotency_key: Optional[str] = None,
        allow_adjustment: bool = False,
        reopen_session_id: Optional[UUID] = None,
    ) -> ReversalResult:
        """
        Create a reversal entry for a posted journal.

        Args:
            db: Database session
            organization_id: Organization scope
            original_journal_id: Journal to reverse
            reversal_date: Date for reversal entry
            created_by_user_id: User creating reversal
            reason: Reason for reversal
            auto_post: Whether to post the reversal immediately
            idempotency_key: Idempotency key for posting
            allow_adjustment: Allow reversal to adjustment periods
            reopen_session_id: Reopen session for reopened periods

        Returns:
            ReversalResult with outcome

        Raises:
            HTTPException(404): If original journal not found
            HTTPException(400): If journal cannot be reversed
        """
        org_id = coerce_uuid(organization_id)
        journal_id = coerce_uuid(original_journal_id)
        user_id = coerce_uuid(created_by_user_id)

        # 1. Load original journal
        original = db.get(JournalEntry, journal_id)
        if not original or original.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Original journal not found")

        # 2. Validate can be reversed
        if original.status != JournalStatus.POSTED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reverse journal with status '{original.status.value}'"
            )

        if original.reversal_journal_id:
            raise HTTPException(
                status_code=400,
                detail="Journal has already been reversed"
            )

        # 3. Get fiscal period for reversal date
        period = PeriodGuardService.get_period_for_date(db, org_id, reversal_date)
        if not period:
            raise HTTPException(
                status_code=400,
                detail=f"No fiscal period found for reversal date {reversal_date}"
            )

        # 4. Check period is open if auto_post
        if auto_post:
            result = PeriodGuardService.can_post_to_date(
                db, org_id, reversal_date, allow_adjustment, reopen_session_id
            )
            if not result.is_allowed:
                raise HTTPException(status_code=400, detail=result.message)

        # 5. Generate reversal journal number
        reversal_number = SequenceService.get_next_number(
            db, org_id, SequenceType.JOURNAL, period.fiscal_year_id
        )

        # 6. Load original lines
        original_lines = (
            db.query(JournalEntryLine)
            .filter(JournalEntryLine.journal_entry_id == journal_id)
            .order_by(JournalEntryLine.line_number)
            .all()
        )

        # 7. Create reversal journal
        reversal = JournalEntry(
            organization_id=org_id,
            journal_number=reversal_number,
            journal_type=JournalType.REVERSAL,
            entry_date=reversal_date,
            posting_date=reversal_date,
            fiscal_period_id=period.fiscal_period_id,
            description=f"Reversal of {original.journal_number}: {reason}",
            reference=f"REV:{original.journal_number}",
            currency_code=original.currency_code,
            exchange_rate=original.exchange_rate,
            exchange_rate_type_id=original.exchange_rate_type_id,
            total_debit=original.total_credit,  # Swapped
            total_credit=original.total_debit,  # Swapped
            total_debit_functional=original.total_credit_functional,
            total_credit_functional=original.total_debit_functional,
            status=JournalStatus.DRAFT,
            is_reversal=True,
            reversed_journal_id=journal_id,
            source_module=original.source_module,
            source_document_type=original.source_document_type,
            source_document_id=original.source_document_id,
            created_by_user_id=user_id,
            correlation_id=original.correlation_id,
        )

        db.add(reversal)
        db.flush()  # Get reversal ID

        # 8. Create reversed lines (swap debits/credits)
        for original_line in original_lines:
            reversal_line = JournalEntryLine(
                journal_entry_id=reversal.journal_entry_id,
                line_number=original_line.line_number,
                account_id=original_line.account_id,
                description=f"Reversal: {original_line.description or ''}",
                debit_amount=original_line.credit_amount,  # Swapped
                credit_amount=original_line.debit_amount,  # Swapped
                debit_amount_functional=original_line.credit_amount_functional,
                credit_amount_functional=original_line.debit_amount_functional,
                currency_code=original_line.currency_code,
                exchange_rate=original_line.exchange_rate,
                business_unit_id=original_line.business_unit_id,
                cost_center_id=original_line.cost_center_id,
                project_id=original_line.project_id,
                segment_id=original_line.segment_id,
            )
            db.add(reversal_line)

        # 9. Link original to reversal
        original.reversal_journal_id = reversal.journal_entry_id
        original.status = JournalStatus.REVERSED

        db.commit()
        db.refresh(reversal)

        # 10. Auto-post if requested
        if auto_post:
            if not idempotency_key:
                idempotency_key = f"{org_id}:GL:{reversal.journal_entry_id}:reversal:v1"

            request = PostingRequest(
                organization_id=org_id,
                journal_entry_id=reversal.journal_entry_id,
                posting_date=reversal_date,
                idempotency_key=idempotency_key,
                source_module=original.source_module or "GL",
                correlation_id=original.correlation_id,
                posted_by_user_id=user_id,
                allow_adjustment_period=allow_adjustment,
                reopen_session_id=reopen_session_id,
            )

            post_result = LedgerPostingService.post_journal_entry(db, request)

            if not post_result.success:
                return ReversalResult(
                    success=False,
                    reversal_journal_id=reversal.journal_entry_id,
                    reversal_journal_number=reversal.journal_number,
                    message=f"Reversal created but posting failed: {post_result.message}",
                )

            db.refresh(reversal)

        return ReversalResult(
            success=True,
            reversal_journal_id=reversal.journal_entry_id,
            reversal_journal_number=reversal.journal_number,
            message="Reversal created successfully" + (" and posted" if auto_post else ""),
        )

    @staticmethod
    def get_reversal_for_journal(
        db: Session,
        organization_id: UUID,
        original_journal_id: UUID,
    ) -> Optional[JournalEntry]:
        """
        Get the reversal journal for an original journal.

        Args:
            db: Database session
            organization_id: Organization scope
            original_journal_id: Original journal ID

        Returns:
            Reversal JournalEntry or None
        """
        org_id = coerce_uuid(organization_id)
        journal_id = coerce_uuid(original_journal_id)

        original = db.get(JournalEntry, journal_id)
        if not original or original.organization_id != org_id:
            return None

        if not original.reversal_journal_id:
            return None

        return db.get(JournalEntry, original.reversal_journal_id)

    @staticmethod
    def can_reverse(
        db: Session,
        organization_id: UUID,
        journal_entry_id: UUID,
    ) -> tuple[bool, str]:
        """
        Check if a journal can be reversed.

        Args:
            db: Database session
            organization_id: Organization scope
            journal_entry_id: Journal to check

        Returns:
            Tuple of (can_reverse, reason)
        """
        org_id = coerce_uuid(organization_id)
        journal_id = coerce_uuid(journal_entry_id)

        journal = db.get(JournalEntry, journal_id)
        if not journal or journal.organization_id != org_id:
            return (False, "Journal not found")

        if journal.status != JournalStatus.POSTED:
            return (False, f"Cannot reverse journal with status '{journal.status.value}'")

        if journal.reversal_journal_id:
            return (False, "Journal has already been reversed")

        return (True, "Journal can be reversed")

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        original_journal_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JournalEntry]:
        """
        List reversal journals.

        Args:
            db: Database session
            organization_id: Filter by organization
            original_journal_id: Filter by original journal
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of reversal JournalEntry objects
        """
        query = db.query(JournalEntry).filter(JournalEntry.is_reversal == True)  # noqa: E712

        if organization_id:
            query = query.filter(
                JournalEntry.organization_id == coerce_uuid(organization_id)
            )

        if original_journal_id:
            query = query.filter(
                JournalEntry.reversed_journal_id == coerce_uuid(original_journal_id)
            )

        query = query.order_by(JournalEntry.created_at.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
reversal_service = ReversalService()
