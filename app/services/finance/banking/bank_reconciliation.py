"""
Bank Reconciliation Service.

Provides bank reconciliation functionality including auto-matching
and reconciliation workflow.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

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
    StatementLineType,
)
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine


@dataclass
class ReconciliationInput:
    """Input for creating a reconciliation."""

    reconciliation_date: date
    period_start: date
    period_end: date
    statement_opening_balance: Decimal
    statement_closing_balance: Decimal
    notes: Optional[str] = None


@dataclass
class ReconciliationMatchInput:
    """Input for matching a statement line to GL entry."""

    statement_line_id: UUID
    journal_line_id: UUID
    match_type: ReconciliationMatchType = ReconciliationMatchType.manual
    notes: Optional[str] = None


@dataclass
class AutoMatchResult:
    """Result of auto-matching operation."""

    matches_found: int
    matches_created: int
    unmatched_statement_lines: int
    unmatched_gl_lines: int
    match_details: List[Dict] = field(default_factory=list)


class BankReconciliationService:
    """Service for bank reconciliation."""

    def create_reconciliation(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        input: ReconciliationInput,
        prepared_by: Optional[UUID] = None,
    ) -> BankReconciliation:
        """Create a new reconciliation session."""
        # Validate bank account
        bank_account = db.get(BankAccount, bank_account_id)
        if not bank_account:
            raise HTTPException(status_code=404, detail=f"Bank account {bank_account_id} not found")

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
        prior_recon = self._get_prior_reconciliation(db, bank_account_id, input.reconciliation_date)
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

        return reconciliation

    def get(self, db: Session, reconciliation_id: UUID) -> Optional[BankReconciliation]:
        """Get a reconciliation by ID."""
        return db.get(BankReconciliation, reconciliation_id)

    def get_with_lines(
        self,
        db: Session,
        reconciliation_id: UUID,
    ) -> Optional[BankReconciliation]:
        """Get reconciliation with all lines loaded."""
        recon = db.get(BankReconciliation, reconciliation_id)
        if recon:
            _ = recon.lines
        return recon

    def list(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: Optional[UUID] = None,
        status: Optional[ReconciliationStatus] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[BankReconciliation]:
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
        bank_account_id: Optional[UUID] = None,
        status: Optional[ReconciliationStatus] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
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
        reconciliation_id: UUID,
        input: ReconciliationMatchInput,
        created_by: Optional[UUID] = None,
    ) -> BankReconciliationLine:
        """Add a match between statement line and GL entry."""
        reconciliation = db.get(BankReconciliation, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail=f"Reconciliation {reconciliation_id} not found")

        if reconciliation.status not in [
            ReconciliationStatus.draft,
            ReconciliationStatus.pending_review,
        ]:
            raise HTTPException(status_code=400, detail="Cannot modify an approved/rejected reconciliation")

        # Get statement line
        statement_line = db.get(BankStatementLine, input.statement_line_id)
        if not statement_line:
            raise HTTPException(status_code=404, detail=f"Statement line {input.statement_line_id} not found")

        # Get GL line
        gl_line = db.get(JournalEntryLine, input.journal_line_id)
        if not gl_line:
            raise HTTPException(status_code=404, detail=f"Journal line {input.journal_line_id} not found")

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
        return recon_line

    def add_adjustment(
        self,
        db: Session,
        reconciliation_id: UUID,
        transaction_date: date,
        amount: Decimal,
        description: str,
        adjustment_type: str,
        adjustment_account_id: Optional[UUID] = None,
        created_by: Optional[UUID] = None,
    ) -> BankReconciliationLine:
        """Add a reconciling adjustment."""
        reconciliation = db.get(BankReconciliation, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail=f"Reconciliation {reconciliation_id} not found")

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
        return recon_line

    def add_outstanding_item(
        self,
        db: Session,
        reconciliation_id: UUID,
        transaction_date: date,
        amount: Decimal,
        description: str,
        outstanding_type: str,  # "deposit" or "payment"
        reference: Optional[str] = None,
        journal_line_id: Optional[UUID] = None,
        created_by: Optional[UUID] = None,
    ) -> BankReconciliationLine:
        """Add an outstanding item (deposit in transit or outstanding check)."""
        reconciliation = db.get(BankReconciliation, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail=f"Reconciliation {reconciliation_id} not found")

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
        return recon_line

    def auto_match(
        self,
        db: Session,
        reconciliation_id: UUID,
        tolerance: Decimal = Decimal("0.01"),
        created_by: Optional[UUID] = None,
    ) -> AutoMatchResult:
        """Automatically match statement lines to GL entries."""
        reconciliation = db.get(BankReconciliation, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail=f"Reconciliation {reconciliation_id} not found")

        bank_account = reconciliation.bank_account
        result = AutoMatchResult(
            matches_found=0,
            matches_created=0,
            unmatched_statement_lines=0,
            unmatched_gl_lines=0,
        )

        # Get unmatched statement lines
        statement_lines = db.execute(
            select(BankStatementLine)
            .join(BankStatement)
            .where(
                and_(
                    BankStatement.bank_account_id == reconciliation.bank_account_id,
                    BankStatementLine.is_matched == False,
                    BankStatementLine.transaction_date >= reconciliation.period_start,
                    BankStatementLine.transaction_date <= reconciliation.period_end,
                )
            )
        ).scalars().all()

        # Get unmatched GL lines for this account
        gl_lines = db.execute(
            select(JournalEntryLine)
            .join(JournalEntry)
            .where(
                and_(
                    JournalEntryLine.account_id == bank_account.gl_account_id,
                    JournalEntry.status == JournalStatus.POSTED,
                    JournalEntry.entry_date >= reconciliation.period_start,
                    JournalEntry.entry_date <= reconciliation.period_end,
                )
            )
        ).scalars().all()

        # Build index of GL lines by amount for fast lookup
        gl_by_amount: Dict[Decimal, List[JournalEntryLine]] = {}
        for gl_line in gl_lines:
            amount = (gl_line.debit_amount or Decimal("0")) - (
                gl_line.credit_amount or Decimal("0")
            )
            if amount not in gl_by_amount:
                gl_by_amount[amount] = []
            gl_by_amount[amount].append(gl_line)

        matched_gl_ids = set()

        for stmt_line in statement_lines:
            stmt_amount = stmt_line.signed_amount

            # Try exact match first
            potential_matches = gl_by_amount.get(stmt_amount, [])

            # Also try with tolerance
            if not potential_matches:
                for gl_amount in gl_by_amount.keys():
                    if abs(gl_amount - stmt_amount) <= tolerance:
                        potential_matches.extend(gl_by_amount[gl_amount])

            # Find best match (by date proximity and reference)
            best_match = None
            best_score = 0.0

            for gl_line in potential_matches:
                if gl_line.line_id in matched_gl_ids:
                    continue

                score = self._calculate_match_score(stmt_line, gl_line)
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
                        db, reconciliation_id, match_input, created_by
                    )
                    recon_line.match_confidence = Decimal(str(best_score))
                    matched_gl_ids.add(best_match.line_id)
                    result.matches_created += 1
                    result.match_details.append({
                        "statement_line_id": str(stmt_line.line_id),
                        "gl_line_id": str(best_match.line_id),
                        "confidence": best_score,
                    })
                except Exception as e:
                    pass  # Skip failed matches

        # Count remaining unmatched
        result.unmatched_statement_lines = len(
            [s for s in statement_lines if not s.is_matched]
        )
        result.unmatched_gl_lines = len(gl_lines) - len(matched_gl_ids)

        db.flush()
        return result

    def _calculate_match_score(
        self,
        stmt_line: BankStatementLine,
        gl_line: JournalEntryLine,
    ) -> float:
        """Calculate match confidence score (0-100)."""
        score = 0.0

        # Amount match (40 points)
        stmt_amount = stmt_line.signed_amount
        gl_amount = (gl_line.debit_amount or Decimal("0")) - (
            gl_line.credit_amount or Decimal("0")
        )
        if stmt_amount == gl_amount:
            score += 40
        elif abs(stmt_amount - gl_amount) <= Decimal("0.01"):
            score += 35

        # Date proximity (30 points)
        date_diff = abs((stmt_line.transaction_date - gl_line.journal_entry.entry_date).days)
        if date_diff == 0:
            score += 30
        elif date_diff <= 1:
            score += 25
        elif date_diff <= 3:
            score += 20
        elif date_diff <= 7:
            score += 10

        # Reference match (30 points)
        if stmt_line.reference and gl_line.description:
            if stmt_line.reference.lower() in gl_line.description.lower():
                score += 30
            elif gl_line.description and stmt_line.description:
                # Check for common words
                stmt_words = set(stmt_line.description.lower().split())
                gl_words = set(gl_line.description.lower().split())
                common = stmt_words & gl_words
                if common:
                    score += min(len(common) * 5, 20)

        return score

    def _get_gl_balance(
        self,
        db: Session,
        gl_account_id: UUID,
        as_of_date: date,
    ) -> Decimal:
        """Get GL account balance as of a date."""
        query = select(
            func.coalesce(func.sum(JournalEntryLine.debit_amount), 0).label("debits"),
            func.coalesce(func.sum(JournalEntryLine.credit_amount), 0).label("credits"),
        ).join(
            JournalEntry
        ).where(
            and_(
                JournalEntryLine.account_id == gl_account_id,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.entry_date <= as_of_date,
            )
        )

        result = db.execute(query).one()
        return Decimal(str(result.debits)) - Decimal(str(result.credits))

    def _get_prior_reconciliation(
        self,
        db: Session,
        bank_account_id: UUID,
        before_date: date,
    ) -> Optional[BankReconciliation]:
        """Get most recent approved reconciliation before a date."""
        query = select(BankReconciliation).where(
            and_(
                BankReconciliation.bank_account_id == bank_account_id,
                BankReconciliation.status == ReconciliationStatus.approved,
                BankReconciliation.reconciliation_date < before_date,
            )
        ).order_by(BankReconciliation.reconciliation_date.desc()).limit(1)

        return db.execute(query).scalar_one_or_none()

    def submit_for_review(
        self,
        db: Session,
        reconciliation_id: UUID,
    ) -> BankReconciliation:
        """Submit reconciliation for review."""
        reconciliation = db.get(BankReconciliation, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail=f"Reconciliation {reconciliation_id} not found")

        if reconciliation.status != ReconciliationStatus.draft:
            raise HTTPException(status_code=400, detail="Only draft reconciliations can be submitted for review")

        reconciliation.status = ReconciliationStatus.pending_review
        db.flush()

        try:
            from app.services.finance.automation.event_dispatcher import fire_workflow_event
            fire_workflow_event(
                db=db, organization_id=reconciliation.organization_id,
                entity_type="RECONCILIATION",
                entity_id=reconciliation.reconciliation_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": "draft"},
                new_values={"status": "pending_review"},
            )
        except Exception:
            pass

        return reconciliation

    def approve(
        self,
        db: Session,
        reconciliation_id: UUID,
        approved_by: UUID,
        notes: Optional[str] = None,
    ) -> BankReconciliation:
        """Approve a reconciliation."""
        reconciliation = db.get(BankReconciliation, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail=f"Reconciliation {reconciliation_id} not found")

        if reconciliation.status != ReconciliationStatus.pending_review:
            raise HTTPException(status_code=400, detail="Only pending reconciliations can be approved")

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
            tzinfo=timezone.utc,
        )
        bank_account.last_reconciled_balance = reconciliation.statement_closing_balance

        db.flush()

        try:
            from app.services.finance.automation.event_dispatcher import fire_workflow_event
            fire_workflow_event(
                db=db, organization_id=reconciliation.organization_id,
                entity_type="RECONCILIATION",
                entity_id=reconciliation.reconciliation_id,
                event="ON_APPROVAL",
                old_values={"status": "pending_review"},
                new_values={"status": "approved"},
                user_id=approved_by,
            )
        except Exception:
            pass

        return reconciliation

    def reject(
        self,
        db: Session,
        reconciliation_id: UUID,
        rejected_by: UUID,
        notes: str,
    ) -> BankReconciliation:
        """Reject a reconciliation."""
        reconciliation = db.get(BankReconciliation, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail=f"Reconciliation {reconciliation_id} not found")

        if reconciliation.status != ReconciliationStatus.pending_review:
            raise HTTPException(status_code=400, detail="Only pending reconciliations can be rejected")

        reconciliation.status = ReconciliationStatus.rejected
        reconciliation.reviewed_by = rejected_by
        reconciliation.reviewed_at = datetime.utcnow()
        reconciliation.review_notes = notes

        db.flush()

        try:
            from app.services.finance.automation.event_dispatcher import fire_workflow_event
            fire_workflow_event(
                db=db, organization_id=reconciliation.organization_id,
                entity_type="RECONCILIATION",
                entity_id=reconciliation.reconciliation_id,
                event="ON_REJECTION",
                old_values={"status": "pending_review"},
                new_values={"status": "rejected"},
                user_id=rejected_by,
            )
        except Exception:
            pass

        return reconciliation

    def get_reconciliation_report(
        self,
        db: Session,
        reconciliation_id: UUID,
    ) -> Dict:
        """Generate reconciliation report data."""
        reconciliation = db.get(BankReconciliation, reconciliation_id)
        if not reconciliation:
            raise HTTPException(status_code=404, detail=f"Reconciliation {reconciliation_id} not found")

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
                "count": len([o for o in outstanding if o.outstanding_type == "deposit"]),
                "total": reconciliation.outstanding_deposits,
                "items": [o for o in outstanding if o.outstanding_type == "deposit"],
            },
            "outstanding_payments": {
                "count": len([o for o in outstanding if o.outstanding_type == "payment"]),
                "total": reconciliation.outstanding_payments,
                "items": [o for o in outstanding if o.outstanding_type == "payment"],
            },
        }


# Singleton instance
bank_reconciliation_service = BankReconciliationService()
