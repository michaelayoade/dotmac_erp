"""
JournalService - Journal entry lifecycle management.

Manages creation, editing, submission, approval, and posting of journal entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.core_config.numbering_sequence import SequenceType
from app.services.common import coerce_uuid
from app.services.finance.gl.period_guard import PeriodGuardService
from app.services.finance.gl.ledger_posting import LedgerPostingService, PostingRequest
from app.services.finance.platform.sequence import SequenceService
from app.services.response import ListResponseMixin


@dataclass
class JournalLineInput:
    """Input for a journal entry line."""

    account_id: UUID
    debit_amount: Decimal = Decimal("0")
    credit_amount: Decimal = Decimal("0")
    description: Optional[str] = None
    # Functional amounts (calculated if not provided)
    debit_amount_functional: Optional[Decimal] = None
    credit_amount_functional: Optional[Decimal] = None
    # Multi-currency
    currency_code: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    # Dimensions
    business_unit_id: Optional[UUID] = None
    cost_center_id: Optional[UUID] = None
    project_id: Optional[UUID] = None
    segment_id: Optional[UUID] = None


@dataclass
class JournalInput:
    """Input for creating/updating a journal entry."""

    journal_type: JournalType
    entry_date: date
    posting_date: date
    description: str
    lines: list[JournalLineInput] = field(default_factory=list)
    reference: Optional[str] = None
    currency_code: str = settings.default_functional_currency_code
    exchange_rate: Decimal = Decimal("1.0")
    exchange_rate_type_id: Optional[UUID] = None
    source_module: Optional[str] = None
    source_document_type: Optional[str] = None
    source_document_id: Optional[UUID] = None
    auto_reverse_date: Optional[date] = None
    correlation_id: Optional[str] = None


class JournalService(ListResponseMixin):
    """
    Service for journal entry lifecycle management.

    Manages creation, editing, submission, approval, and posting.
    """

    @staticmethod
    def create_journal(
        db: Session,
        organization_id: UUID,
        input: JournalInput,
        created_by_user_id: UUID,
    ) -> JournalEntry:
        """
        Create a new journal entry in DRAFT status.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Journal input data
            created_by_user_id: User creating the journal

        Returns:
            Created JournalEntry

        Raises:
            HTTPException(400): If validation fails
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)

        # Validate lines
        if not input.lines:
            raise HTTPException(status_code=400, detail="Journal must have at least one line")

        # Validate balance
        total_debit = sum(
            (l.debit_amount or Decimal("0") for l in input.lines),
            Decimal("0"),
        )
        total_credit = sum(
            (l.credit_amount or Decimal("0") for l in input.lines),
            Decimal("0"),
        )

        if abs(total_debit - total_credit) > Decimal("0.000001"):
            raise HTTPException(
                status_code=400,
                detail=f"Journal is unbalanced: debits={total_debit}, credits={total_credit}"
            )

        # Get fiscal period for posting date
        period = PeriodGuardService.get_period_for_date(db, org_id, input.posting_date)
        if not period:
            raise HTTPException(
                status_code=400,
                detail=f"No fiscal period found for posting date {input.posting_date}"
            )

        # Generate journal number
        journal_number = SequenceService.get_next_number(
            db, org_id, SequenceType.JOURNAL, period.fiscal_year_id
        )

        # Calculate functional amounts
        functional_debit = Decimal("0")
        functional_credit = Decimal("0")

        for line_input in input.lines:
            if line_input.debit_amount_functional is None:
                line_input.debit_amount_functional = line_input.debit_amount * input.exchange_rate
            if line_input.credit_amount_functional is None:
                line_input.credit_amount_functional = line_input.credit_amount * input.exchange_rate

            functional_debit += line_input.debit_amount_functional
            functional_credit += line_input.credit_amount_functional

        # Create journal entry
        journal = JournalEntry(
            organization_id=org_id,
            journal_number=journal_number,
            journal_type=input.journal_type,
            entry_date=input.entry_date,
            posting_date=input.posting_date,
            fiscal_period_id=period.fiscal_period_id,
            description=input.description,
            reference=input.reference,
            currency_code=input.currency_code,
            exchange_rate=input.exchange_rate,
            exchange_rate_type_id=coerce_uuid(input.exchange_rate_type_id) if input.exchange_rate_type_id else None,
            total_debit=total_debit,
            total_credit=total_credit,
            total_debit_functional=functional_debit,
            total_credit_functional=functional_credit,
            status=JournalStatus.DRAFT,
            source_module=input.source_module,
            source_document_type=input.source_document_type,
            source_document_id=coerce_uuid(input.source_document_id) if input.source_document_id else None,
            auto_reverse_date=input.auto_reverse_date,
            created_by_user_id=user_id,
            correlation_id=input.correlation_id,
        )

        db.add(journal)
        db.flush()  # Get journal_entry_id

        # Create lines
        for i, line_input in enumerate(input.lines):
            entry_line = JournalEntryLine(
                journal_entry_id=journal.journal_entry_id,
                line_number=i + 1,
                account_id=coerce_uuid(line_input.account_id),
                description=line_input.description,
                debit_amount=line_input.debit_amount,
                credit_amount=line_input.credit_amount,
                debit_amount_functional=line_input.debit_amount_functional,
                credit_amount_functional=line_input.credit_amount_functional,
                currency_code=line_input.currency_code or input.currency_code,
                exchange_rate=line_input.exchange_rate or input.exchange_rate,
                business_unit_id=coerce_uuid(line_input.business_unit_id) if line_input.business_unit_id else None,
                cost_center_id=coerce_uuid(line_input.cost_center_id) if line_input.cost_center_id else None,
                project_id=coerce_uuid(line_input.project_id) if line_input.project_id else None,
                segment_id=coerce_uuid(line_input.segment_id) if line_input.segment_id else None,
            )
            db.add(entry_line)

        db.commit()
        db.refresh(journal)

        return journal

    @staticmethod
    def update_journal(
        db: Session,
        organization_id: UUID,
        journal_entry_id: UUID,
        input: JournalInput,
        updated_by_user_id: UUID,
    ) -> JournalEntry:
        """
        Update an existing DRAFT journal entry.

        Args:
            db: Database session
            organization_id: Organization scope
            journal_entry_id: Journal to update
            input: Updated journal data
            updated_by_user_id: User updating

        Returns:
            Updated JournalEntry

        Raises:
            HTTPException(404): If journal not found
            HTTPException(400): If journal is not in DRAFT status
        """
        org_id = coerce_uuid(organization_id)
        journal_id = coerce_uuid(journal_entry_id)

        journal = db.get(JournalEntry, journal_id)
        if not journal or journal.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        if journal.status != JournalStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot update journal with status '{journal.status.value}'"
            )

        # Validate lines
        if not input.lines:
            raise HTTPException(status_code=400, detail="Journal must have at least one line")

        # Validate balance
        total_debit = sum(
            (l.debit_amount or Decimal("0") for l in input.lines),
            Decimal("0"),
        )
        total_credit = sum(
            (l.credit_amount or Decimal("0") for l in input.lines),
            Decimal("0"),
        )

        if abs(total_debit - total_credit) > Decimal("0.000001"):
            raise HTTPException(
                status_code=400,
                detail=f"Journal is unbalanced: debits={total_debit}, credits={total_credit}"
            )

        # Get fiscal period
        period = PeriodGuardService.get_period_for_date(db, org_id, input.posting_date)
        if not period:
            raise HTTPException(
                status_code=400,
                detail=f"No fiscal period found for posting date {input.posting_date}"
            )

        # Calculate functional amounts
        functional_debit = Decimal("0")
        functional_credit = Decimal("0")

        for line_input in input.lines:
            if line_input.debit_amount_functional is None:
                line_input.debit_amount_functional = line_input.debit_amount * input.exchange_rate
            if line_input.credit_amount_functional is None:
                line_input.credit_amount_functional = line_input.credit_amount * input.exchange_rate

            functional_debit += line_input.debit_amount_functional
            functional_credit += line_input.credit_amount_functional

        # Update journal
        journal.journal_type = input.journal_type
        journal.entry_date = input.entry_date
        journal.posting_date = input.posting_date
        journal.fiscal_period_id = period.fiscal_period_id
        journal.description = input.description
        journal.reference = input.reference
        journal.currency_code = input.currency_code
        journal.exchange_rate = input.exchange_rate
        journal.total_debit = total_debit
        journal.total_credit = total_credit
        journal.total_debit_functional = functional_debit
        journal.total_credit_functional = functional_credit
        journal.auto_reverse_date = input.auto_reverse_date
        journal.correlation_id = input.correlation_id

        # Delete existing lines
        db.query(JournalEntryLine).filter(
            JournalEntryLine.journal_entry_id == journal_id
        ).delete()

        # Create new lines
        for i, line_input in enumerate(input.lines):
            entry_line = JournalEntryLine(
                journal_entry_id=journal.journal_entry_id,
                line_number=i + 1,
                account_id=coerce_uuid(line_input.account_id),
                description=line_input.description,
                debit_amount=line_input.debit_amount,
                credit_amount=line_input.credit_amount,
                debit_amount_functional=line_input.debit_amount_functional,
                credit_amount_functional=line_input.credit_amount_functional,
                currency_code=line_input.currency_code or input.currency_code,
                exchange_rate=line_input.exchange_rate or input.exchange_rate,
                business_unit_id=coerce_uuid(line_input.business_unit_id) if line_input.business_unit_id else None,
                cost_center_id=coerce_uuid(line_input.cost_center_id) if line_input.cost_center_id else None,
                project_id=coerce_uuid(line_input.project_id) if line_input.project_id else None,
                segment_id=coerce_uuid(line_input.segment_id) if line_input.segment_id else None,
            )
            db.add(entry_line)

        db.commit()
        db.refresh(journal)

        return journal

    @staticmethod
    def submit_journal(
        db: Session,
        organization_id: UUID,
        journal_entry_id: UUID,
        submitted_by_user_id: UUID,
    ) -> JournalEntry:
        """
        Submit a DRAFT journal for approval.

        Args:
            db: Database session
            organization_id: Organization scope
            journal_entry_id: Journal to submit
            submitted_by_user_id: User submitting

        Returns:
            Updated JournalEntry

        Raises:
            HTTPException(404): If journal not found
            HTTPException(400): If journal cannot be submitted
        """
        org_id = coerce_uuid(organization_id)
        journal_id = coerce_uuid(journal_entry_id)
        user_id = coerce_uuid(submitted_by_user_id)

        journal = db.get(JournalEntry, journal_id)
        if not journal or journal.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        if journal.status != JournalStatus.DRAFT:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot submit journal with status '{journal.status.value}'"
            )

        journal.status = JournalStatus.SUBMITTED
        journal.submitted_by_user_id = user_id
        journal.submitted_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(journal)

        return journal

    @staticmethod
    def approve_journal(
        db: Session,
        organization_id: UUID,
        journal_entry_id: UUID,
        approved_by_user_id: UUID,
    ) -> JournalEntry:
        """
        Approve a SUBMITTED journal.

        Args:
            db: Database session
            organization_id: Organization scope
            journal_entry_id: Journal to approve
            approved_by_user_id: User approving

        Returns:
            Updated JournalEntry

        Raises:
            HTTPException(404): If journal not found
            HTTPException(400): If journal cannot be approved
            HTTPException(403): If SoD violation (creator cannot approve)
        """
        org_id = coerce_uuid(organization_id)
        journal_id = coerce_uuid(journal_entry_id)
        user_id = coerce_uuid(approved_by_user_id)

        journal = db.get(JournalEntry, journal_id)
        if not journal or journal.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        if journal.status != JournalStatus.SUBMITTED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve journal with status '{journal.status.value}'"
            )

        # SoD check - creator cannot approve
        if journal.created_by_user_id == user_id:
            raise HTTPException(
                status_code=403,
                detail="Segregation of duties: creator cannot approve their own journal"
            )

        journal.status = JournalStatus.APPROVED
        journal.approved_by_user_id = user_id
        journal.approved_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(journal)

        return journal

    @staticmethod
    def post_journal(
        db: Session,
        organization_id: UUID,
        journal_entry_id: UUID,
        posted_by_user_id: UUID,
        idempotency_key: Optional[str] = None,
        allow_adjustment: bool = False,
        reopen_session_id: Optional[UUID] = None,
    ) -> JournalEntry:
        """
        Post an APPROVED journal to the ledger.

        Args:
            db: Database session
            organization_id: Organization scope
            journal_entry_id: Journal to post
            posted_by_user_id: User posting
            idempotency_key: Idempotency key for posting
            allow_adjustment: Allow posting to adjustment periods
            reopen_session_id: Reopen session for reopened periods

        Returns:
            Updated JournalEntry

        Raises:
            HTTPException(404): If journal not found
            HTTPException(400): If posting fails
        """
        org_id = coerce_uuid(organization_id)
        journal_id = coerce_uuid(journal_entry_id)
        user_id = coerce_uuid(posted_by_user_id)

        journal = db.get(JournalEntry, journal_id)
        if not journal or journal.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        if journal.status == JournalStatus.POSTED:
            return journal  # Already posted (idempotent)

        if journal.status not in {JournalStatus.APPROVED, JournalStatus.DRAFT}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot post journal with status '{journal.status.value}'"
            )

        # Generate idempotency key if not provided
        if not idempotency_key:
            idempotency_key = f"{org_id}:GL:{journal_id}:v1"

        # Create posting request
        request = PostingRequest(
            organization_id=org_id,
            journal_entry_id=journal_id,
            posting_date=journal.posting_date,
            idempotency_key=idempotency_key,
            source_module=journal.source_module or "GL",
            correlation_id=journal.correlation_id,
            posted_by_user_id=user_id,
            allow_adjustment_period=allow_adjustment,
            reopen_session_id=reopen_session_id,
        )

        # Post via LedgerPostingService
        result = LedgerPostingService.post_journal_entry(db, request)

        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)

        # Refresh and return
        db.refresh(journal)
        return journal

    @staticmethod
    def void_journal(
        db: Session,
        organization_id: UUID,
        journal_entry_id: UUID,
        voided_by_user_id: UUID,
        reason: str,
    ) -> JournalEntry:
        """
        Void a DRAFT or SUBMITTED journal.

        Args:
            db: Database session
            organization_id: Organization scope
            journal_entry_id: Journal to void
            voided_by_user_id: User voiding
            reason: Reason for voiding

        Returns:
            Updated JournalEntry

        Raises:
            HTTPException(404): If journal not found
            HTTPException(400): If journal cannot be voided
        """
        org_id = coerce_uuid(organization_id)
        journal_id = coerce_uuid(journal_entry_id)

        journal = db.get(JournalEntry, journal_id)
        if not journal or journal.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        if journal.status in {JournalStatus.POSTED, JournalStatus.REVERSED}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot void journal with status '{journal.status.value}'. Use reversal instead."
            )

        journal.status = JournalStatus.VOID

        db.commit()
        db.refresh(journal)

        return journal

    @staticmethod
    def get(
        db: Session,
        journal_entry_id: str,
    ) -> JournalEntry:
        """
        Get a journal entry by ID.

        Args:
            db: Database session
            journal_entry_id: Journal ID

        Returns:
            JournalEntry

        Raises:
            HTTPException(404): If not found
        """
        journal = db.get(JournalEntry, coerce_uuid(journal_entry_id))
        if not journal:
            raise HTTPException(status_code=404, detail="Journal entry not found")
        return journal

    @staticmethod
    def get_lines(
        db: Session,
        journal_entry_id: UUID,
    ) -> list[JournalEntryLine]:
        """
        Get lines for a journal entry.

        Args:
            db: Database session
            journal_entry_id: Journal ID

        Returns:
            List of JournalEntryLine records
        """
        journal_id = coerce_uuid(journal_entry_id)

        return (
            db.query(JournalEntryLine)
            .filter(JournalEntryLine.journal_entry_id == journal_id)
            .order_by(JournalEntryLine.line_number)
            .all()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        status: Optional[JournalStatus] = None,
        journal_type: Optional[JournalType] = None,
        fiscal_period_id: Optional[str] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JournalEntry]:
        """
        List journal entries.

        Args:
            db: Database session
            organization_id: Filter by organization
            status: Filter by status
            journal_type: Filter by type
            fiscal_period_id: Filter by period
            from_date: Filter by start date
            to_date: Filter by end date
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of JournalEntry objects
        """
        query = db.query(JournalEntry)

        if organization_id:
            query = query.filter(
                JournalEntry.organization_id == coerce_uuid(organization_id)
            )

        if status:
            query = query.filter(JournalEntry.status == status)

        if journal_type:
            query = query.filter(JournalEntry.journal_type == journal_type)

        if fiscal_period_id:
            query = query.filter(
                JournalEntry.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if from_date:
            query = query.filter(JournalEntry.posting_date >= from_date)

        if to_date:
            query = query.filter(JournalEntry.posting_date <= to_date)

        query = query.order_by(JournalEntry.created_at.desc())
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def create_entry(
        db: Session,
        organization_id: UUID,
        input: JournalInput,
        created_by_user_id: UUID,
    ) -> JournalEntry:
        """
        Create a new journal entry (alias for create_journal).

        Args:
            db: Database session
            organization_id: Organization scope
            input: Journal input data
            created_by_user_id: User creating the journal

        Returns:
            Created JournalEntry
        """
        return JournalService.create_journal(db, organization_id, input, created_by_user_id)

    @staticmethod
    def reverse_entry(
        db: Session,
        organization_id: UUID,
        entry_id: UUID,
        reversal_date: date,
        reversed_by_user_id: UUID,
    ) -> JournalEntry:
        """
        Reverse a posted journal entry.

        Args:
            db: Database session
            organization_id: Organization scope
            entry_id: Journal to reverse
            reversal_date: Date for reversal entry
            reversed_by_user_id: User performing reversal

        Returns:
            Reversal JournalEntry

        Raises:
            HTTPException(404): If journal not found
            HTTPException(400): If journal cannot be reversed
        """
        org_id = coerce_uuid(organization_id)
        journal_id = coerce_uuid(entry_id)
        user_id = coerce_uuid(reversed_by_user_id)

        journal = db.get(JournalEntry, journal_id)
        if not journal or journal.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Journal entry not found")

        if journal.status != JournalStatus.POSTED:
            raise HTTPException(
                status_code=400,
                detail="Only posted journals can be reversed"
            )

        if journal.reversal_journal_id:
            raise HTTPException(
                status_code=400,
                detail="Journal has already been reversed"
            )

        # Create reversal entry
        reversal = JournalEntry(
            organization_id=org_id,
            fiscal_period_id=journal.fiscal_period_id,
            journal_type=JournalType.REVERSAL,
            journal_number=f"REV-{journal.journal_number}",
            entry_date=reversal_date,
            posting_date=reversal_date,
            description=f"Reversal of {journal.journal_number}: {journal.description}",
            reference=journal.journal_number,
            total_debit=journal.total_credit,
            total_credit=journal.total_debit,
            total_debit_functional=journal.total_credit_functional,
            total_credit_functional=journal.total_debit_functional,
            currency_code=journal.currency_code,
            exchange_rate=journal.exchange_rate,
            status=JournalStatus.POSTED,
            is_reversal=True,
            reversed_journal_id=journal.journal_entry_id,
            source_module=journal.source_module,
            created_by_user_id=user_id,
        )

        # Reverse the lines (swap debits and credits)
        for line in journal.lines:
            reversal_line = JournalEntryLine(
                account_id=line.account_id,
                debit_amount=line.credit_amount,
                credit_amount=line.debit_amount,
                debit_amount_functional=line.credit_amount_functional,
                credit_amount_functional=line.debit_amount_functional,
                currency_code=line.currency_code,
                exchange_rate=line.exchange_rate,
                description=f"Reversal: {line.description or ''}",
                business_unit_id=line.business_unit_id,
                cost_center_id=line.cost_center_id,
                project_id=line.project_id,
                segment_id=line.segment_id,
            )
            reversal.lines.append(reversal_line)

        # Mark original as reversed
        journal.reversal_journal_id = reversal.journal_entry_id
        journal.status = JournalStatus.REVERSED

        db.add(reversal)
        db.commit()
        db.refresh(reversal)

        return reversal


# Module-level singleton instance
journal_service = JournalService()
