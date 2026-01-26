"""
LedgerPostingService - Single writer for posted_ledger_line.

CRITICAL: This is the ONLY service that writes to posted_ledger_line.
All ledger postings MUST go through this service.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, List, Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from app.models.finance.gl.posting_batch import BatchStatus, PostingBatch
from app.models.finance.gl.account import Account
from app.services.common import coerce_uuid
from app.services.finance.gl.period_guard import PeriodGuardService
from app.services.finance.platform.outbox_publisher import OutboxPublisher
from app.services.response import ListResponseMixin


@dataclass
class PostingEntry:
    """A single entry to be posted."""

    account_id: UUID
    debit_amount: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    description: Optional[str] = None
    # Functional currency amounts (required)
    debit_amount_functional: Decimal = Decimal("0")
    credit_amount_functional: Decimal = Decimal("0")
    # Optional multi-currency
    original_currency_code: Optional[str] = None
    original_debit_amount: Optional[Decimal] = None
    original_credit_amount: Optional[Decimal] = None
    exchange_rate: Optional[Decimal] = None
    # Dimensions
    business_unit_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    segment_id: Optional[UUID] = None


@dataclass
class PostingRequest:
    """Request to post to the ledger."""

    organization_id: UUID
    journal_entry_id: UUID
    posting_date: date
    idempotency_key: str
    source_module: str
    entries: list[PostingEntry] = field(default_factory=list)
    correlation_id: Optional[str] = None
    posted_by_user_id: Optional[UUID] = None
    allow_adjustment_period: bool = False
    reopen_session_id: Optional[UUID] = None


@dataclass
class PostingResult:
    """Result of a posting operation."""

    success: bool
    batch_id: Optional[UUID] = None
    posted_lines: int = 0
    total_debit: Decimal = Decimal("0")
    total_credit: Decimal = Decimal("0")
    message: str = ""
    correlation_id: Optional[str] = None

    @property
    def posting_batch_id(self) -> Optional[UUID]:
        return self.batch_id


@dataclass
class PostEntryResult:
    """Result of post_entry operation (for API compatibility)."""

    success: bool
    entry_id: Optional[UUID] = None
    entry_number: Optional[str] = None
    message: str = ""


class LedgerPostingService(ListResponseMixin):
    """
    Single writer service for posted_ledger_line.

    CRITICAL RULES:
    1. ONLY this service writes to posted_ledger_line
    2. Functional currency amounts MUST NOT be null/zero
    3. Debit = Credit MUST balance
    4. Idempotency key MUST be provided
    5. Period MUST be open (via PeriodGuardService)
    6. All postings emit events via outbox
    """

    # Tolerance for balance check (accounts for floating point)
    BALANCE_TOLERANCE = Decimal("0.000001")

    @staticmethod
    def post_journal_entry(
        db: Session,
        request: PostingRequest,
    ) -> PostingResult:
        """
        Post a journal entry to the ledger.

        Creates posting_batch and posted_ledger_line records.
        Emits ledger.posting.completed event via outbox.

        Args:
            db: Database session
            request: PostingRequest with entry details

        Returns:
            PostingResult with outcome

        Raises:
            HTTPException(400): If validation fails
            HTTPException(404): If journal entry not found
        """
        org_id = coerce_uuid(request.organization_id)
        journal_id = coerce_uuid(request.journal_entry_id)

        # 1. Validate idempotency key
        if not request.idempotency_key:
            raise HTTPException(
                status_code=400,
                detail="Idempotency key is required for ledger posting"
            )

        # 2. Check for existing batch with same idempotency key
        existing_batch = (
            db.query(PostingBatch)
            .filter(PostingBatch.idempotency_key == request.idempotency_key)
            .first()
        )

        if existing_batch:
            if existing_batch.status == BatchStatus.POSTED:
                # Already posted - return success (idempotent)
                return PostingResult(
                    success=True,
                    batch_id=existing_batch.batch_id,
                    posted_lines=existing_batch.posted_entries,
                    message="Already posted (idempotent replay)",
                    correlation_id=existing_batch.correlation_id,
                )
            elif existing_batch.status == BatchStatus.FAILED:
                # Failed previously - allow retry
                pass
            else:
                raise HTTPException(
                    status_code=409,
                    detail=f"Batch with idempotency key in status '{existing_batch.status.value}'"
                )

        # 3. Validate journal entry exists
        journal = db.get(JournalEntry, journal_id)
        if not journal:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        if journal.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        if journal.status == JournalStatus.POSTED:
            raise HTTPException(status_code=400, detail="Journal already posted")

        if journal.status not in {JournalStatus.APPROVED, JournalStatus.DRAFT}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot post journal with status '{journal.status.value}'"
            )

        # 4. Validate period is open
        fiscal_period_id = PeriodGuardService.require_open_period(
            db,
            org_id,
            request.posting_date,
            request.allow_adjustment_period,
            request.reopen_session_id,
        )

        # 5. Load journal lines if not provided
        entries = request.entries
        if not entries:
            entries = LedgerPostingService._load_journal_lines(db, journal)

        # 6. Validate balance
        LedgerPostingService._validate_balance(entries)

        # 7. Validate functional amounts
        LedgerPostingService._validate_functional_amounts(entries)

        # 8. Create posting batch
        batch = PostingBatch(
            organization_id=org_id,
            fiscal_period_id=fiscal_period_id,
            idempotency_key=request.idempotency_key,
            source_module=request.source_module,
            batch_description=f"Journal {journal.journal_number}",
            total_entries=len(entries),
            status=BatchStatus.PROCESSING,
            submitted_by_user_id=coerce_uuid(request.posted_by_user_id) if request.posted_by_user_id else journal.created_by_user_id,
            correlation_id=request.correlation_id,
        )
        db.add(batch)
        db.flush()  # Get batch_id

        # 9. Get period for year
        period = db.get(FiscalPeriod, fiscal_period_id)
        posting_year = request.posting_date.year

        # 10. Create posted_ledger_line records
        posted_lines = []
        total_debit = Decimal("0")
        total_credit = Decimal("0")

        # Get account codes for denormalization
        account_ids = [e.account_id for e in entries]
        accounts = db.query(Account).filter(Account.account_id.in_(account_ids)).all()
        account_map = {a.account_id: a.account_code for a in accounts}

        for i, entry in enumerate(entries):
            ledger_line = PostedLedgerLine(
                posting_year=posting_year,
                organization_id=org_id,
                journal_entry_id=journal_id,
                journal_line_id=uuid_lib.uuid4(),  # Generate new ID for ledger line
                posting_batch_id=batch.batch_id,
                fiscal_period_id=fiscal_period_id,
                account_id=coerce_uuid(entry.account_id),
                account_code=account_map.get(coerce_uuid(entry.account_id), ""),
                entry_date=journal.entry_date,
                posting_date=request.posting_date,
                description=entry.description,
                journal_reference=journal.reference,
                debit_amount=entry.debit_amount_functional,
                credit_amount=entry.credit_amount_functional,
                original_currency_code=entry.original_currency_code,
                original_debit_amount=entry.original_debit_amount,
                original_credit_amount=entry.original_credit_amount,
                exchange_rate=entry.exchange_rate,
                business_unit_id=coerce_uuid(entry.business_unit_id) if entry.business_unit_id else None,
                cost_center_id=coerce_uuid(entry.cost_center_id) if entry.cost_center_id else None,
                project_id=coerce_uuid(entry.project_id) if entry.project_id else None,
                segment_id=coerce_uuid(entry.segment_id) if entry.segment_id else None,
                source_module=request.source_module,
                source_document_type=journal.source_document_type,
                source_document_id=journal.source_document_id,
                posted_by_user_id=coerce_uuid(request.posted_by_user_id) if request.posted_by_user_id else journal.created_by_user_id,
                correlation_id=request.correlation_id,
            )
            db.add(ledger_line)
            posted_lines.append(ledger_line)

            total_debit += entry.debit_amount_functional
            total_credit += entry.credit_amount_functional

        # 11. Update batch status
        batch.posted_entries = len(posted_lines)
        batch.status = BatchStatus.POSTED
        batch.completed_at = datetime.now(timezone.utc)

        # 12. Update journal status
        journal.status = JournalStatus.POSTED
        journal.posting_batch_id = batch.batch_id
        journal.posted_at = datetime.now(timezone.utc)
        journal.posted_by_user_id = coerce_uuid(request.posted_by_user_id) if request.posted_by_user_id else journal.created_by_user_id

        # 13. Commit the transaction
        db.commit()
        db.refresh(batch)

        # 14. Publish event via outbox
        LedgerPostingService._publish_posting_event(
            db,
            org_id,
            batch.batch_id,
            journal_id,
            fiscal_period_id,
            len(posted_lines),
            total_debit,
            total_credit,
            request.correlation_id,
        )

        return PostingResult(
            success=True,
            batch_id=batch.batch_id,
            posted_lines=len(posted_lines),
            total_debit=total_debit,
            total_credit=total_credit,
            message="Journal posted successfully",
            correlation_id=request.correlation_id,
        )

    @staticmethod
    def _load_journal_lines(
        db: Session,
        journal: JournalEntry,
    ) -> list[PostingEntry]:
        """Load journal lines and convert to posting entries."""
        lines = (
            db.query(JournalEntryLine)
            .filter(JournalEntryLine.journal_entry_id == journal.journal_entry_id)
            .order_by(JournalEntryLine.line_number)
            .all()
        )

        entries = []
        for line in lines:
            entry = PostingEntry(
                account_id=line.account_id,
                debit_amount=line.debit_amount,
                credit_amount=line.credit_amount,
                description=line.description,
                debit_amount_functional=line.debit_amount_functional,
                credit_amount_functional=line.credit_amount_functional,
                original_currency_code=line.currency_code,
                original_debit_amount=line.debit_amount if line.currency_code else None,
                original_credit_amount=line.credit_amount if line.currency_code else None,
                exchange_rate=line.exchange_rate,
                business_unit_id=line.business_unit_id,
                cost_center_id=line.cost_center_id,
                project_id=line.project_id,
                segment_id=line.segment_id,
            )
            entries.append(entry)

        return entries

    @staticmethod
    def _validate_balance(entries: list[PostingEntry]) -> None:
        """Validate that debits equal credits."""
        total_debit = sum((e.debit_amount_functional for e in entries), Decimal("0"))
        total_credit = sum((e.credit_amount_functional for e in entries), Decimal("0"))

        if abs(total_debit - total_credit) > LedgerPostingService.BALANCE_TOLERANCE:
            raise HTTPException(
                status_code=400,
                detail=f"Journal is unbalanced: debits={total_debit}, credits={total_credit}"
            )

    @staticmethod
    def _validate_functional_amounts(entries: list[PostingEntry]) -> None:
        """Validate that functional currency amounts are provided."""
        for i, entry in enumerate(entries):
            if entry.debit_amount_functional == 0 and entry.credit_amount_functional == 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Line {i+1}: functional currency amounts cannot both be zero"
                )

    @staticmethod
    def _publish_posting_event(
        db: Session,
        organization_id: UUID,
        batch_id: UUID,
        journal_entry_id: UUID,
        fiscal_period_id: UUID,
        line_count: int,
        total_debit: Decimal,
        total_credit: Decimal,
        correlation_id: Optional[str],
    ) -> None:
        """Publish ledger.posting.completed event."""
        OutboxPublisher.publish_event(
            db,
            event_name="ledger.posting.completed",
            aggregate_type="PostingBatch",
            aggregate_id=str(batch_id),
            payload={
                "organization_id": str(organization_id),
                "batch_id": str(batch_id),
                "journal_entry_id": str(journal_entry_id),
                "fiscal_period_id": str(fiscal_period_id),
                "line_count": line_count,
                "total_debit": str(total_debit),
                "total_credit": str(total_credit),
            },
            headers={
                "organization_id": str(organization_id),
            },
            producer_module="GL",
            correlation_id=correlation_id or str(uuid_lib.uuid4()),
            idempotency_key=f"posting:{batch_id}",
        )

    @staticmethod
    def get_batch(
        db: Session,
        batch_id: str,
    ) -> PostingBatch:
        """
        Get a posting batch by ID.

        Args:
            db: Database session
            batch_id: Batch ID

        Returns:
            PostingBatch

        Raises:
            HTTPException(404): If not found
        """
        batch = db.get(PostingBatch, coerce_uuid(batch_id))
        if not batch:
            raise HTTPException(status_code=404, detail="Posting batch not found")
        return batch

    @staticmethod
    def get_ledger_lines(
        db: Session,
        organization_id: UUID,
        journal_entry_id: Optional[UUID] = None,
        posting_batch_id: Optional[UUID] = None,
        account_id: Optional[UUID] = None,
        fiscal_period_id: Optional[UUID] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[PostedLedgerLine]:
        """
        Get posted ledger lines.

        Args:
            db: Database session
            organization_id: Organization scope
            journal_entry_id: Filter by journal
            posting_batch_id: Filter by batch
            account_id: Filter by account
            fiscal_period_id: Filter by period
            from_date: Filter by start date
            to_date: Filter by end date
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of PostedLedgerLine records
        """
        org_id = coerce_uuid(organization_id)

        query = db.query(PostedLedgerLine).filter(
            PostedLedgerLine.organization_id == org_id
        )

        if journal_entry_id:
            query = query.filter(PostedLedgerLine.journal_entry_id == coerce_uuid(journal_entry_id))

        if posting_batch_id:
            query = query.filter(PostedLedgerLine.posting_batch_id == coerce_uuid(posting_batch_id))

        if account_id:
            query = query.filter(PostedLedgerLine.account_id == coerce_uuid(account_id))

        if fiscal_period_id:
            query = query.filter(PostedLedgerLine.fiscal_period_id == coerce_uuid(fiscal_period_id))

        if from_date:
            query = query.filter(PostedLedgerLine.posting_date >= from_date)

        if to_date:
            query = query.filter(PostedLedgerLine.posting_date <= to_date)

        query = query.order_by(PostedLedgerLine.posted_at.desc())
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        status: Optional[BatchStatus] = None,
        source_module: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[PostingBatch]:
        """
        List posting batches.

        Args:
            db: Database session
            organization_id: Filter by organization
            status: Filter by status
            source_module: Filter by source module
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of PostingBatch objects
        """
        query = db.query(PostingBatch)

        if organization_id:
            query = query.filter(
                PostingBatch.organization_id == coerce_uuid(organization_id)
            )

        if status:
            query = query.filter(PostingBatch.status == status)

        if source_module:
            query = query.filter(PostingBatch.source_module == source_module)

        query = query.order_by(PostingBatch.submitted_at.desc())
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def post_entry(
        db: Session,
        organization_id: UUID,
        entry_id: UUID,
        posted_by_user_id: UUID,
    ) -> PostEntryResult:
        """
        Post a journal entry to the ledger (simplified API).

        Args:
            db: Database session
            organization_id: Organization scope
            entry_id: Journal entry to post
            posted_by_user_id: User performing the posting

        Returns:
            PostEntryResult with outcome
        """
        from app.services.finance.gl.journal import journal_service

        org_id = coerce_uuid(organization_id)
        journal_id = coerce_uuid(entry_id)

        # Get journal entry
        journal = db.get(JournalEntry, journal_id)
        if not journal or journal.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        # Post journal through journal service
        try:
            posted = journal_service.post_journal(
                db=db,
                organization_id=org_id,
                journal_entry_id=journal_id,
                posted_by_user_id=posted_by_user_id,
            )
            return PostEntryResult(
                success=True,
                entry_id=posted.journal_entry_id,
                entry_number=posted.journal_number,
                message="Posted successfully",
            )
        except HTTPException as e:
            return PostEntryResult(
                success=False,
                message=e.detail,
            )


# Module-level singleton instance
ledger_posting_service = LedgerPostingService()
