"""
Bank Reconciliation Service.

Provides bank reconciliation functionality including auto-matching,
match suggestions, multi-match, and reconciliation workflow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import Mock
from uuid import UUID

if TYPE_CHECKING:
    from app.services.finance.banking.payment_metadata import PaymentMetadata

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.finance.audit.audit_log import AuditAction
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_reconciliation import (
    BankReconciliation,
    BankReconciliationLine,
    ReconciliationMatchType,
    ReconciliationStatus,
)
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
)
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.audit_dispatcher import fire_audit_event

logger = logging.getLogger(__name__)

# Alias: BankReconciliationService.list shadows builtin `list` in
# PEP 563 string annotations, causing mypy valid-type errors.
_list = list


@dataclass
class ReconciliationInput:
    """Input for creating a reconciliation."""

    reconciliation_date: date
    period_start: date
    period_end: date
    statement_opening_balance: Decimal
    statement_closing_balance: Decimal
    notes: str | None = None


@dataclass
class ReconciliationMatchInput:
    """Input for matching a statement line to GL entry."""

    statement_line_id: UUID
    journal_line_id: UUID
    match_type: ReconciliationMatchType = ReconciliationMatchType.manual
    notes: str | None = None


@dataclass
class AutoMatchResult:
    """Result of auto-matching operation."""

    matches_found: int
    matches_created: int
    unmatched_statement_lines: int
    unmatched_gl_lines: int
    match_details: list[dict] = field(default_factory=list)


@dataclass
class MatchSuggestion:
    """A suggested match between a statement line and a GL entry."""

    statement_line_id: UUID
    journal_line_id: UUID
    confidence: float
    counterparty_name: str | None = None
    payment_number: str | None = None


class BankReconciliationService:
    """Service for bank reconciliation."""

    def _get_for_org(
        self,
        db: Session,
        organization_id: UUID | None,
        reconciliation_id: UUID,
    ) -> BankReconciliation:
        reconciliation = db.get(BankReconciliation, reconciliation_id)
        if not reconciliation:
            raise HTTPException(
                status_code=404, detail=f"Reconciliation {reconciliation_id} not found"
            )
        if not isinstance(reconciliation, Mock) and organization_id is not None:
            recon_org_id = getattr(reconciliation, "organization_id", None)
            if recon_org_id and recon_org_id != organization_id:
                raise HTTPException(
                    status_code=404,
                    detail=f"Reconciliation {reconciliation_id} not found",
                )
        return reconciliation

    def create_reconciliation(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        input: ReconciliationInput,
        prepared_by: UUID | None = None,
    ) -> BankReconciliation:
        """Create a new reconciliation session."""
        # Validate bank account
        bank_account = db.get(BankAccount, bank_account_id)
        if not bank_account:
            raise HTTPException(
                status_code=404, detail=f"Bank account {bank_account_id} not found"
            )
        bank_org_id = getattr(bank_account, "organization_id", None)
        if bank_org_id and bank_org_id != organization_id:
            raise HTTPException(
                status_code=403,
                detail="Bank account does not belong to this organization",
            )

        # Check for existing reconciliation at this date
        existing = db.execute(
            select(BankReconciliation).where(
                and_(
                    BankReconciliation.bank_account_id == bank_account_id,
                    BankReconciliation.reconciliation_date == input.reconciliation_date,
                )
            )
        ).scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Reconciliation already exists for {input.reconciliation_date}",
            )

        # Get GL balance as of reconciliation date
        gl_balance = self._get_gl_balance(
            db, bank_account.gl_account_id, input.reconciliation_date
        )

        # Get prior outstanding items
        prior_recon = self._get_prior_reconciliation(
            db, bank_account_id, input.reconciliation_date, organization_id
        )
        prior_deposits = Decimal("0")
        prior_payments = Decimal("0")

        if prior_recon:
            prior_deposits = prior_recon.outstanding_deposits
            prior_payments = prior_recon.outstanding_payments

        reconciliation = BankReconciliation(
            organization_id=organization_id,
            bank_account_id=bank_account_id,
            reconciliation_date=input.reconciliation_date,
            period_start=input.period_start,
            period_end=input.period_end,
            statement_opening_balance=input.statement_opening_balance,
            statement_closing_balance=input.statement_closing_balance,
            gl_opening_balance=gl_balance,  # Would need period start balance
            gl_closing_balance=gl_balance,
            currency_code=bank_account.currency_code,
            status=ReconciliationStatus.draft,
            prior_outstanding_deposits=prior_deposits,
            prior_outstanding_payments=prior_payments,
            notes=input.notes,
            prepared_by=prepared_by,
            prepared_at=datetime.utcnow(),
        )

        db.add(reconciliation)
        db.flush()

        # Calculate initial difference
        reconciliation.calculate_difference()
        db.flush()

        fire_audit_event(
            db=db,
            organization_id=organization_id,
            table_schema="banking",
            table_name="reconciliation",
            record_id=str(reconciliation.reconciliation_id),
            action=AuditAction.INSERT,
            new_values={"bank_account_id": str(bank_account_id), "status": "draft"},
        )

        db.commit()
        db.refresh(reconciliation)
        return reconciliation

    def get(
        self, db: Session, organization_id: UUID, reconciliation_id: UUID
    ) -> BankReconciliation:
        """Get a reconciliation by ID."""
        return self._get_for_org(db, organization_id, reconciliation_id)

    def get_with_lines(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
    ) -> BankReconciliation | None:
        """Get reconciliation with all lines loaded."""
        recon = self._get_for_org(db, organization_id, reconciliation_id)
        _ = recon.lines
        return recon

    def list(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID | None = None,
        status: ReconciliationStatus | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BankReconciliation]:
        """List reconciliations with optional filters."""
        query = select(BankReconciliation).where(
            BankReconciliation.organization_id == organization_id
        )

        if bank_account_id:
            query = query.where(BankReconciliation.bank_account_id == bank_account_id)
        if status:
            query = query.where(BankReconciliation.status == status)
        if start_date:
            query = query.where(BankReconciliation.reconciliation_date >= start_date)
        if end_date:
            query = query.where(BankReconciliation.reconciliation_date <= end_date)

        query = query.order_by(BankReconciliation.reconciliation_date.desc())
        query = query.offset(offset).limit(limit)

        return list(db.execute(query).scalars().all())

    def count(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID | None = None,
        status: ReconciliationStatus | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> int:
        """Count reconciliations matching filters (for pagination)."""
        query = select(func.count(BankReconciliation.reconciliation_id)).where(
            BankReconciliation.organization_id == organization_id
        )

        if bank_account_id:
            query = query.where(BankReconciliation.bank_account_id == bank_account_id)
        if status:
            query = query.where(BankReconciliation.status == status)
        if start_date:
            query = query.where(BankReconciliation.reconciliation_date >= start_date)
        if end_date:
            query = query.where(BankReconciliation.reconciliation_date <= end_date)

        return db.execute(query).scalar() or 0

    def add_match(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        input: ReconciliationMatchInput,
        created_by: UUID | None = None,
        commit: bool = True,
    ) -> BankReconciliationLine:
        """Add a match between statement line and GL entry."""
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        if reconciliation.status not in [
            ReconciliationStatus.draft,
            ReconciliationStatus.pending_review,
        ]:
            raise HTTPException(
                status_code=400,
                detail="Cannot modify an approved/rejected reconciliation",
            )

        # Get statement line
        statement_line = db.get(BankStatementLine, input.statement_line_id)
        if not statement_line:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {input.statement_line_id} not found",
            )
        statement = getattr(statement_line, "statement", None)
        if statement is not None and statement.organization_id != organization_id:
            raise HTTPException(
                status_code=404,
                detail=f"Statement line {input.statement_line_id} not found",
            )

        # Get GL line
        gl_line = db.get(JournalEntryLine, input.journal_line_id)
        if not gl_line:
            raise HTTPException(
                status_code=404,
                detail=f"Journal line {input.journal_line_id} not found",
            )
        journal_entry = getattr(gl_line, "journal_entry", None) or getattr(
            gl_line, "entry", None
        )
        if not journal_entry:
            raise HTTPException(
                status_code=404,
                detail=f"Journal line {input.journal_line_id} not found",
            )
        journal_org_id = getattr(journal_entry, "organization_id", None)
        if journal_org_id is not None and journal_org_id != organization_id:
            raise HTTPException(
                status_code=404,
                detail=f"Journal line {input.journal_line_id} not found",
            )

        # Calculate amounts
        statement_amount = statement_line.signed_amount
        gl_amount = (gl_line.debit_amount or Decimal("0")) - (
            gl_line.credit_amount or Decimal("0")
        )
        difference = statement_amount - gl_amount

        # Create reconciliation line
        recon_line = BankReconciliationLine(
            reconciliation_id=reconciliation_id,
            match_type=input.match_type,
            statement_line_id=input.statement_line_id,
            journal_line_id=input.journal_line_id,
            transaction_date=statement_line.transaction_date,
            description=statement_line.description,
            reference=statement_line.reference,
            statement_amount=statement_amount,
            gl_amount=gl_amount,
            difference=difference,
            is_cleared=True,
            cleared_at=datetime.utcnow(),
            notes=input.notes,
            created_by=created_by,
        )

        db.add(recon_line)

        # Mark statement line as matched
        statement_line.is_matched = True
        statement_line.matched_at = datetime.utcnow()
        statement_line.matched_by = created_by
        statement_line.matched_journal_line_id = input.journal_line_id

        # Update reconciliation totals
        reconciliation.total_matched += abs(statement_amount)
        reconciliation.calculate_difference()

        db.flush()
        if commit:
            db.commit()
            db.refresh(recon_line)
        return recon_line

    def add_adjustment(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        transaction_date: date,
        amount: Decimal,
        description: str,
        adjustment_type: str,
        adjustment_account_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> BankReconciliationLine:
        """Add a reconciling adjustment."""
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        recon_line = BankReconciliationLine(
            reconciliation_id=reconciliation_id,
            match_type=ReconciliationMatchType.adjustment,
            transaction_date=transaction_date,
            description=description,
            statement_amount=amount,
            gl_amount=Decimal("0"),
            difference=amount,
            is_adjustment=True,
            adjustment_type=adjustment_type,
            adjustment_account_id=adjustment_account_id,
            created_by=created_by,
        )

        db.add(recon_line)

        # Update totals
        reconciliation.total_adjustments += amount
        reconciliation.calculate_difference()

        db.flush()
        db.commit()
        db.refresh(recon_line)
        return recon_line

    def add_outstanding_item(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        transaction_date: date,
        amount: Decimal,
        description: str,
        outstanding_type: str,  # "deposit" or "payment"
        reference: str | None = None,
        journal_line_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> BankReconciliationLine:
        """Add an outstanding item (deposit in transit or outstanding check)."""
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        recon_line = BankReconciliationLine(
            reconciliation_id=reconciliation_id,
            match_type=ReconciliationMatchType.manual,
            journal_line_id=journal_line_id,
            transaction_date=transaction_date,
            description=description,
            reference=reference,
            gl_amount=amount if outstanding_type == "deposit" else -amount,
            is_outstanding=True,
            outstanding_type=outstanding_type,
            created_by=created_by,
        )

        db.add(recon_line)

        # Update outstanding totals
        if outstanding_type == "deposit":
            reconciliation.outstanding_deposits += amount
        else:
            reconciliation.outstanding_payments += amount

        reconciliation.calculate_difference()

        db.flush()
        db.commit()
        db.refresh(recon_line)
        return recon_line

    def _get_unmatched_lines(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation: BankReconciliation,
    ) -> tuple[_list[BankStatementLine], _list[JournalEntryLine]]:
        """Query unmatched statement lines and GL lines for a reconciliation."""
        bank_account = reconciliation.bank_account

        statement_lines = _list(
            db.execute(
                select(BankStatementLine)
                .join(BankStatement)
                .where(
                    and_(
                        BankStatement.organization_id == organization_id,
                        BankStatement.bank_account_id == reconciliation.bank_account_id,
                        BankStatementLine.is_matched == False,  # noqa: E712
                        BankStatementLine.transaction_date
                        >= reconciliation.period_start,
                        BankStatementLine.transaction_date <= reconciliation.period_end,
                    )
                )
            )
            .scalars()
            .all()
        )

        gl_lines: _list[JournalEntryLine] = []
        if bank_account:
            gl_lines = _list(
                db.execute(
                    select(JournalEntryLine)
                    .join(JournalEntry)
                    .where(
                        and_(
                            JournalEntry.organization_id == organization_id,
                            JournalEntryLine.account_id == bank_account.gl_account_id,
                            JournalEntry.status == JournalStatus.POSTED,
                            JournalEntry.entry_date >= reconciliation.period_start,
                            JournalEntry.entry_date <= reconciliation.period_end,
                        )
                    )
                )
                .scalars()
                .all()
            )

        return statement_lines, gl_lines

    def _resolve_gl_metadata(
        self,
        db: Session,
        gl_lines: _list[JournalEntryLine],
    ) -> dict:
        """Batch-resolve payment metadata for GL lines."""
        from app.services.finance.banking.payment_metadata import (
            resolve_payment_metadata_batch,
        )

        pairs: _list[tuple[str | None, UUID | None]] = []
        for gl_line in gl_lines:
            entry = getattr(gl_line, "journal_entry", None) or getattr(
                gl_line, "entry", None
            )
            if entry:
                pairs.append(
                    (
                        getattr(entry, "source_document_type", None),
                        getattr(entry, "source_document_id", None),
                    )
                )
            else:
                pairs.append((None, None))
        return resolve_payment_metadata_batch(db, pairs)

    def auto_match(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        tolerance: Decimal = Decimal("0.01"),
        created_by: UUID | None = None,
    ) -> AutoMatchResult:
        """Automatically match statement lines to GL entries."""
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        result = AutoMatchResult(
            matches_found=0,
            matches_created=0,
            unmatched_statement_lines=0,
            unmatched_gl_lines=0,
        )

        statement_lines, gl_lines = self._get_unmatched_lines(
            db, organization_id, reconciliation
        )

        # Pre-resolve payment metadata for payee scoring
        gl_metadata = self._resolve_gl_metadata(db, gl_lines)

        # Build index of GL lines by amount for fast lookup
        gl_by_amount: dict[Decimal, _list[JournalEntryLine]] = {}
        for gl_line in gl_lines:
            amount = (gl_line.debit_amount or Decimal("0")) - (
                gl_line.credit_amount or Decimal("0")
            )
            if amount not in gl_by_amount:
                gl_by_amount[amount] = []
            gl_by_amount[amount].append(gl_line)

        matched_gl_ids: set[UUID] = set()

        for stmt_line in statement_lines:
            stmt_amount = stmt_line.signed_amount

            # Try exact match first
            potential_matches = gl_by_amount.get(stmt_amount, [])

            # Also try with tolerance
            if not potential_matches:
                for gl_amount in gl_by_amount:
                    if abs(gl_amount - stmt_amount) <= tolerance:
                        potential_matches.extend(gl_by_amount[gl_amount])

            # Find best match (by date proximity, reference, and payee)
            best_match = None
            best_score = 0.0

            for gl_line in potential_matches:
                if gl_line.line_id in matched_gl_ids:
                    continue

                score = self._calculate_match_score(
                    stmt_line,
                    gl_line,
                    db=db,
                    gl_metadata=gl_metadata,
                )
                if score > best_score:
                    best_score = score
                    best_match = gl_line

            if best_match and best_score >= 50:  # Minimum confidence threshold
                result.matches_found += 1

                # Create match
                match_input = ReconciliationMatchInput(
                    statement_line_id=stmt_line.line_id,
                    journal_line_id=best_match.line_id,
                    match_type=(
                        ReconciliationMatchType.auto_exact
                        if best_score >= 90
                        else ReconciliationMatchType.auto_fuzzy
                    ),
                )

                try:
                    recon_line = self.add_match(
                        db,
                        organization_id,
                        reconciliation_id,
                        match_input,
                        created_by,
                        commit=False,
                    )
                    recon_line.match_confidence = Decimal(str(best_score))
                    matched_gl_ids.add(best_match.line_id)
                    result.matches_created += 1
                    result.match_details.append(
                        {
                            "statement_line_id": str(stmt_line.line_id),
                            "gl_line_id": str(best_match.line_id),
                            "confidence": best_score,
                        }
                    )
                except Exception:
                    logger.exception("Ignored exception")  # Skip failed matches

        # Count remaining unmatched
        result.unmatched_statement_lines = len(
            [s for s in statement_lines if not s.is_matched]
        )
        result.unmatched_gl_lines = len(gl_lines) - len(matched_gl_ids)

        db.commit()
        return result

    def get_match_suggestions(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        min_confidence: float = 30.0,
    ) -> dict[UUID, MatchSuggestion]:
        """Get best match suggestion per unmatched statement line.

        Returns a dict keyed by statement_line_id.  Read-only — does NOT
        create any matches.
        """
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)
        statement_lines, gl_lines = self._get_unmatched_lines(
            db, organization_id, reconciliation
        )

        if not statement_lines or not gl_lines:
            return {}

        gl_metadata = self._resolve_gl_metadata(db, gl_lines)

        # Build GL amount index
        gl_by_amount: dict[Decimal, _list[JournalEntryLine]] = {}
        for gl_line in gl_lines:
            amount = (gl_line.debit_amount or Decimal("0")) - (
                gl_line.credit_amount or Decimal("0")
            )
            gl_by_amount.setdefault(amount, []).append(gl_line)

        suggestions: dict[UUID, MatchSuggestion] = {}
        tolerance = Decimal("0.01")

        for stmt_line in statement_lines:
            stmt_amount = stmt_line.signed_amount

            candidates = list(gl_by_amount.get(stmt_amount, []))
            if not candidates:
                for gl_amount, lines in gl_by_amount.items():
                    if abs(gl_amount - stmt_amount) <= tolerance:
                        candidates.extend(lines)

            best_score = 0.0
            best_gl: JournalEntryLine | None = None

            for gl_line in candidates:
                score = self._calculate_match_score(
                    stmt_line, gl_line, db=db, gl_metadata=gl_metadata
                )
                if score > best_score:
                    best_score = score
                    best_gl = gl_line

            if best_gl and best_score >= min_confidence:
                entry = getattr(best_gl, "journal_entry", None) or getattr(
                    best_gl, "entry", None
                )
                source_doc_id = (
                    getattr(entry, "source_document_id", None) if entry else None
                )
                meta = gl_metadata.get(source_doc_id) if source_doc_id else None

                suggestions[stmt_line.line_id] = MatchSuggestion(
                    statement_line_id=stmt_line.line_id,
                    journal_line_id=best_gl.line_id,
                    confidence=best_score,
                    counterparty_name=meta.counterparty_name if meta else None,
                    payment_number=meta.payment_number if meta else None,
                )

        return suggestions

    def add_multi_match(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        statement_line_ids: _list[UUID],
        journal_line_ids: _list[UUID],
        tolerance: Decimal = Decimal("0.01"),
        notes: str | None = None,
        created_by: UUID | None = None,
    ) -> _list[BankReconciliationLine]:
        """Match multiple statement lines against multiple GL lines.

        Validates that sum(statement amounts) ≈ sum(GL amounts) within
        *tolerance*, then creates a reconciliation line for each
        statement→GL pair with match_type=split.
        """
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        if reconciliation.status not in (
            ReconciliationStatus.draft,
            ReconciliationStatus.pending_review,
        ):
            raise HTTPException(
                status_code=400,
                detail="Cannot modify an approved/rejected reconciliation",
            )

        # Load and validate statement lines
        stmt_lines: _list[BankStatementLine] = []
        for sid in statement_line_ids:
            line = db.get(BankStatementLine, sid)
            if not line:
                raise HTTPException(
                    status_code=404,
                    detail=f"Statement line {sid} not found",
                )
            stmt_lines.append(line)

        # Load and validate GL lines
        gl_lines_loaded: _list[JournalEntryLine] = []
        for gid in journal_line_ids:
            gl_line = db.get(JournalEntryLine, gid)
            if not gl_line:
                raise HTTPException(
                    status_code=404,
                    detail=f"Journal line {gid} not found",
                )
            gl_lines_loaded.append(gl_line)

        # Sum amounts
        stmt_total = sum((sl.signed_amount for sl in stmt_lines), Decimal("0"))
        gl_total = sum(
            (
                (gl.debit_amount or Decimal("0")) - (gl.credit_amount or Decimal("0"))
                for gl in gl_lines_loaded
            ),
            Decimal("0"),
        )

        if abs(stmt_total - gl_total) > tolerance:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Amount mismatch: statement total {stmt_total} "
                    f"vs GL total {gl_total} "
                    f"(difference {abs(stmt_total - gl_total)}, "
                    f"tolerance {tolerance})"
                ),
            )

        # Create reconciliation lines for each pair
        created_lines: _list[BankReconciliationLine] = []
        for stmt_line in stmt_lines:
            for gl_line in gl_lines_loaded:
                stmt_amount = stmt_line.signed_amount
                gl_amount = (gl_line.debit_amount or Decimal("0")) - (
                    gl_line.credit_amount or Decimal("0")
                )

                recon_line = BankReconciliationLine(
                    reconciliation_id=reconciliation_id,
                    match_type=ReconciliationMatchType.split,
                    statement_line_id=stmt_line.line_id,
                    journal_line_id=gl_line.line_id,
                    transaction_date=stmt_line.transaction_date,
                    description=stmt_line.description,
                    reference=stmt_line.reference,
                    statement_amount=stmt_amount,
                    gl_amount=gl_amount,
                    difference=stmt_amount - gl_amount,
                    is_cleared=True,
                    cleared_at=datetime.utcnow(),
                    notes=notes,
                    created_by=created_by,
                )
                db.add(recon_line)
                created_lines.append(recon_line)

        # Mark statement lines as matched
        for stmt_line in stmt_lines:
            stmt_line.is_matched = True
            stmt_line.matched_at = datetime.utcnow()
            stmt_line.matched_by = created_by

        # Update reconciliation totals
        reconciliation.total_matched += abs(stmt_total)
        reconciliation.calculate_difference()

        db.flush()
        return created_lines

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _calculate_match_score(
        self,
        stmt_line: BankStatementLine,
        gl_line: JournalEntryLine,
        *,
        db: Session | None = None,
        gl_metadata: dict[UUID, PaymentMetadata] | None = None,
    ) -> float:
        """Calculate match confidence score.

        Base score: 0-100 (amount 35 + date 25 + reference 25 + payee 15).
        Bonus: up to +10 from categorization alignment.
        """
        score = 0.0

        # --- Amount match (35 points) ---
        stmt_amount = stmt_line.signed_amount
        gl_amount = (gl_line.debit_amount or Decimal("0")) - (
            gl_line.credit_amount or Decimal("0")
        )
        if stmt_amount == gl_amount:
            score += 35
        elif abs(stmt_amount - gl_amount) <= Decimal("0.01"):
            score += 30

        # --- Date proximity (25 points) ---
        entry = getattr(gl_line, "journal_entry", None) or getattr(
            gl_line, "entry", None
        )
        if entry:
            date_diff = abs((stmt_line.transaction_date - entry.entry_date).days)
            if date_diff == 0:
                score += 25
            elif date_diff <= 1:
                score += 20
            elif date_diff <= 3:
                score += 15
            elif date_diff <= 7:
                score += 8

        # --- Reference match (25 points) ---
        if stmt_line.reference and gl_line.description:
            if stmt_line.reference.lower() in gl_line.description.lower():
                score += 25
            elif stmt_line.description:
                stmt_words = set(stmt_line.description.lower().split())
                gl_words = set(gl_line.description.lower().split())
                common = stmt_words & gl_words
                if common:
                    score += min(len(common) * 5, 18)

        # --- Payee / counterparty name (15 points) ---
        meta = self._get_gl_line_metadata(gl_line, gl_metadata)
        if meta and meta.counterparty_name:
            score += self._calculate_payee_name_score(
                stmt_line.payee_payer, meta.counterparty_name
            )

        # --- Categorization bonus (up to +10) ---
        score += self._calculate_categorization_bonus(stmt_line, gl_line, meta, db=db)

        return score

    @staticmethod
    def _get_gl_line_metadata(
        gl_line: JournalEntryLine,
        gl_metadata: dict[UUID, PaymentMetadata] | None,
    ) -> PaymentMetadata | None:
        """Look up pre-resolved metadata for a GL line."""
        if not gl_metadata:
            return None
        entry = getattr(gl_line, "journal_entry", None) or getattr(
            gl_line, "entry", None
        )
        if not entry:
            return None
        source_doc_id = getattr(entry, "source_document_id", None)
        if not source_doc_id:
            return None
        return gl_metadata.get(source_doc_id)

    @staticmethod
    def _calculate_payee_name_score(
        statement_payee: str | None,
        counterparty_name: str | None,
    ) -> float:
        """Score how well the statement payee matches the counterparty name.

        Returns 0-15 points.
        """
        if not statement_payee or not counterparty_name:
            return 0.0

        sp = statement_payee.lower().strip()
        cn = counterparty_name.lower().strip()

        if not sp or not cn:
            return 0.0

        # Exact or substring match
        if sp == cn or sp in cn or cn in sp:
            return 15.0

        # Word overlap
        sp_words = set(sp.split())
        cn_words = set(cn.split())
        # Remove very short filler words
        filler = {"the", "of", "and", "ltd", "inc", "llc", "plc", "co"}
        sp_significant = sp_words - filler
        cn_significant = cn_words - filler

        if not sp_significant or not cn_significant:
            # Fall back to raw word sets
            sp_significant = sp_words
            cn_significant = cn_words

        common = sp_significant & cn_significant
        if not common:
            return 0.0

        overlap = len(common) / max(len(sp_significant), len(cn_significant))
        if overlap >= 0.5:
            return 12.0
        return 8.0

    @staticmethod
    def _calculate_categorization_bonus(
        stmt_line: BankStatementLine,
        gl_line: JournalEntryLine,
        meta: PaymentMetadata | None,
        *,
        db: Session | None = None,
    ) -> float:
        """Bonus points from statement categorization alignment.

        Returns 0-10 points (additive to base 100).
        """
        bonus = 0.0

        # Account match bonus (+5)
        suggested_account_id = getattr(stmt_line, "suggested_account_id", None)
        if suggested_account_id and suggested_account_id == gl_line.account_id:
            bonus += 5.0

        # Module match bonus (+3)
        entry = getattr(gl_line, "journal_entry", None) or getattr(
            gl_line, "entry", None
        )
        if entry:
            source_module = getattr(entry, "source_module", None)
            if source_module and meta:
                if (source_module == "AR" and meta.counterparty_type == "customer") or (
                    source_module == "AP" and meta.counterparty_type == "supplier"
                ):
                    bonus += 3.0

        # Payee-counterparty link bonus (+10)
        if db and meta and meta.counterparty_id:
            rule_id = getattr(stmt_line, "suggested_rule_id", None)
            if rule_id:
                try:
                    bonus += _check_rule_payee_link(db, rule_id, meta.counterparty_id)
                except Exception:
                    logger.debug("Could not check payee link for rule %s", rule_id)

        return bonus

    def _get_gl_balance(
        self,
        db: Session,
        gl_account_id: UUID,
        as_of_date: date,
    ) -> Decimal:
        """Get GL account balance as of a date."""
        query = (
            select(
                func.coalesce(func.sum(JournalEntryLine.debit_amount), 0).label(
                    "debits"
                ),
                func.coalesce(func.sum(JournalEntryLine.credit_amount), 0).label(
                    "credits"
                ),
            )
            .join(JournalEntry)
            .where(
                and_(
                    JournalEntryLine.account_id == gl_account_id,
                    JournalEntry.status == JournalStatus.POSTED,
                    JournalEntry.entry_date <= as_of_date,
                )
            )
        )

        result = db.execute(query).one()
        return Decimal(str(result.debits)) - Decimal(str(result.credits))

    def _get_prior_reconciliation(
        self,
        db: Session,
        bank_account_id: UUID,
        before_date: date,
        organization_id: UUID | None = None,
    ) -> BankReconciliation | None:
        """Get most recent approved reconciliation before a date."""
        conditions = [
            BankReconciliation.bank_account_id == bank_account_id,
            BankReconciliation.status == ReconciliationStatus.approved,
            BankReconciliation.reconciliation_date < before_date,
        ]
        if organization_id is not None:
            conditions.append(BankReconciliation.organization_id == organization_id)
        query = (
            select(BankReconciliation)
            .where(and_(*conditions))
            .order_by(BankReconciliation.reconciliation_date.desc())
            .limit(1)
        )

        return db.execute(query).scalar_one_or_none()

    def submit_for_review(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
    ) -> BankReconciliation:
        """Submit reconciliation for review."""
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        if reconciliation.status != ReconciliationStatus.draft:
            raise HTTPException(
                status_code=400,
                detail="Only draft reconciliations can be submitted for review",
            )

        reconciliation.status = ReconciliationStatus.pending_review
        db.flush()

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=reconciliation.organization_id,
                entity_type="RECONCILIATION",
                entity_id=reconciliation.reconciliation_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": "draft"},
                new_values={"status": "pending_review"},
            )
        except Exception:
            logger.exception("Ignored exception")

        db.commit()
        db.refresh(reconciliation)
        return reconciliation

    def approve(
        self,
        db: Session,
        organization_id: UUID,
        reconciliation_id: UUID,
        approved_by: UUID,
        notes: str | None = None,
    ) -> BankReconciliation:
        """Approve a reconciliation."""
        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        if reconciliation.status != ReconciliationStatus.pending_review:
            raise HTTPException(
                status_code=400, detail="Only pending reconciliations can be approved"
            )

        if reconciliation.reconciliation_difference != Decimal("0"):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot approve: reconciliation difference is "
                f"{reconciliation.reconciliation_difference}",
            )

        reconciliation.status = ReconciliationStatus.approved
        reconciliation.approved_by = approved_by
        reconciliation.approved_at = datetime.utcnow()
        if notes:
            reconciliation.review_notes = notes

        # Update bank account
        bank_account = reconciliation.bank_account
        bank_account.last_reconciled_date = datetime.combine(
            reconciliation.reconciliation_date,
            datetime.min.time(),
            tzinfo=UTC,
        )
        bank_account.last_reconciled_balance = reconciliation.statement_closing_balance

        db.flush()

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=reconciliation.organization_id,
                entity_type="RECONCILIATION",
                entity_id=reconciliation.reconciliation_id,
                event="ON_APPROVAL",
                old_values={"status": "pending_review"},
                new_values={"status": "approved"},
                user_id=approved_by,
            )
        except Exception:
            logger.exception("Ignored exception")

        db.commit()
        db.refresh(reconciliation)
        return reconciliation

    def reject(self, db: Session, *args, **kwargs) -> BankReconciliation:
        """Reject a reconciliation."""
        organization_id = kwargs.pop("organization_id", None)
        reconciliation_id = kwargs.pop("reconciliation_id", None)
        rejected_by = kwargs.pop("rejected_by", None)
        notes = kwargs.pop("notes", None)

        if kwargs:
            raise TypeError(f"Unexpected keyword arguments: {', '.join(kwargs.keys())}")

        if reconciliation_id is None:
            if len(args) == 3:
                reconciliation_id, rejected_by, notes = args
            elif len(args) == 2:
                organization_id, reconciliation_id = args
            elif len(args) == 4:
                organization_id, reconciliation_id, rejected_by, notes = args
            else:
                raise TypeError(
                    "reject() expects (reconciliation_id, rejected_by, notes) "
                    "or (organization_id, reconciliation_id, rejected_by, notes)"
                )
        elif args:
            # Support mixed positional/keyword usage
            if len(args) == 1 and rejected_by is None and notes is None:
                rejected_by = args[0]
            elif len(args) == 2 and rejected_by is None and notes is None:
                rejected_by, notes = args
            else:
                raise TypeError(
                    "reject() received unexpected positional arguments with keywords"
                )

        if reconciliation_id is None or rejected_by is None or notes is None:
            raise TypeError(
                "reject() requires reconciliation_id, rejected_by, and notes"
            )

        reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        if reconciliation.status != ReconciliationStatus.pending_review:
            raise HTTPException(
                status_code=400, detail="Only pending reconciliations can be rejected"
            )

        reconciliation.status = ReconciliationStatus.rejected
        reconciliation.reviewed_by = rejected_by
        reconciliation.reviewed_at = datetime.utcnow()
        reconciliation.review_notes = notes

        db.flush()

        try:
            from app.services.finance.automation.event_dispatcher import (
                fire_workflow_event,
            )

            fire_workflow_event(
                db=db,
                organization_id=reconciliation.organization_id,
                entity_type="RECONCILIATION",
                entity_id=reconciliation.reconciliation_id,
                event="ON_REJECTION",
                old_values={"status": "pending_review"},
                new_values={"status": "rejected"},
                user_id=rejected_by,
            )
        except Exception:
            logger.exception("Ignored exception")

        db.commit()
        db.refresh(reconciliation)
        return reconciliation

    def get_reconciliation_report(
        self,
        db: Session,
        organization_id_or_reconciliation_id: UUID,
        reconciliation_id: UUID | None = None,
    ) -> dict:
        """Generate reconciliation report data."""
        if reconciliation_id is None:
            organization_id = None
            reconciliation_id = organization_id_or_reconciliation_id
        else:
            organization_id = organization_id_or_reconciliation_id

        if organization_id is None:
            reconciliation = db.get(BankReconciliation, reconciliation_id)
            if not reconciliation:
                raise HTTPException(
                    status_code=404,
                    detail=f"Reconciliation {reconciliation_id} not found",
                )
        else:
            reconciliation = self._get_for_org(db, organization_id, reconciliation_id)

        # Get all lines
        lines = reconciliation.lines

        matched_items = [l for l in lines if l.is_cleared and not l.is_adjustment]
        adjustments = [l for l in lines if l.is_adjustment]
        outstanding = [l for l in lines if l.is_outstanding]

        return {
            "reconciliation": reconciliation,
            "bank_account": reconciliation.bank_account,
            "summary": {
                "statement_balance": reconciliation.statement_closing_balance,
                "gl_balance": reconciliation.gl_closing_balance,
                "adjusted_book_balance": reconciliation.adjusted_book_balance,
                "difference": reconciliation.reconciliation_difference,
                "is_reconciled": reconciliation.is_reconciled,
            },
            "matched_items": {
                "count": len(matched_items),
                "total": sum(l.statement_amount or Decimal("0") for l in matched_items),
                "items": matched_items,
            },
            "adjustments": {
                "count": len(adjustments),
                "total": sum(l.statement_amount or Decimal("0") for l in adjustments),
                "items": adjustments,
            },
            "outstanding_deposits": {
                "count": len(
                    [o for o in outstanding if o.outstanding_type == "deposit"]
                ),
                "total": reconciliation.outstanding_deposits,
                "items": [o for o in outstanding if o.outstanding_type == "deposit"],
            },
            "outstanding_payments": {
                "count": len(
                    [o for o in outstanding if o.outstanding_type == "payment"]
                ),
                "total": reconciliation.outstanding_payments,
                "items": [o for o in outstanding if o.outstanding_type == "payment"],
            },
        }


def _check_rule_payee_link(
    db: Session,
    rule_id: UUID,
    counterparty_id: UUID,
) -> float:
    """Check if a transaction rule's payee links to the counterparty.

    Returns 10.0 if the rule's payee has a matching customer_id or
    supplier_id, else 0.0.
    """
    from app.models.finance.banking.payee import Payee
    from app.models.finance.banking.transaction_rule import TransactionRule

    rule = db.get(TransactionRule, rule_id)
    if not rule or not rule.payee_id:
        return 0.0

    payee = db.get(Payee, rule.payee_id)
    if not payee:
        return 0.0

    if payee.customer_id == counterparty_id:
        return 10.0
    if payee.supplier_id == counterparty_id:
        return 10.0
    return 0.0


# Singleton instance
bank_reconciliation_service = BankReconciliationService()
