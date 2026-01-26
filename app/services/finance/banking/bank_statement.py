"""
Bank Statement Service.

Provides statement import and management functionality.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    BankStatementStatus,
    StatementLineType,
)


@dataclass
class StatementLineInput:
    """Input for a bank statement line."""

    line_number: int
    transaction_date: date
    transaction_type: StatementLineType
    amount: Decimal
    description: Optional[str] = None
    reference: Optional[str] = None
    payee_payer: Optional[str] = None
    bank_reference: Optional[str] = None
    check_number: Optional[str] = None
    bank_category: Optional[str] = None
    bank_code: Optional[str] = None
    value_date: Optional[date] = None
    running_balance: Optional[Decimal] = None
    transaction_id: Optional[str] = None
    raw_data: Optional[Dict] = None


@dataclass
class DuplicateLineInfo:
    """Information about a duplicate line."""
    line_number: int
    transaction_date: date
    amount: Decimal
    description: Optional[str]
    original_statement_id: UUID
    original_line_id: UUID


@dataclass
class StatementImportResult:
    """Result of a statement import operation."""

    statement: BankStatement
    lines_imported: int
    lines_skipped: int
    duplicates_found: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    duplicate_lines: List[DuplicateLineInfo] = field(default_factory=list)


class BankStatementService:
    """Service for managing bank statements."""

    def _check_duplicate_line(
        self,
        db: Session,
        bank_account_id: UUID,
        line: StatementLineInput,
    ) -> Optional[BankStatementLine]:
        """
        Check if a transaction line is a potential duplicate.

        Matches on: same account, date, amount, and transaction type.
        """
        # Find existing lines with same date/amount/type
        existing = (
            db.query(BankStatementLine)
            .join(BankStatement)
            .filter(
                BankStatement.bank_account_id == bank_account_id,
                BankStatementLine.transaction_date == line.transaction_date,
                BankStatementLine.amount == line.amount,
                BankStatementLine.transaction_type == line.transaction_type,
            )
            .first()
        )

        if existing:
            # Additional check: if bank_reference matches, it's definitely a duplicate
            if line.bank_reference and existing.bank_reference:
                if line.bank_reference == existing.bank_reference:
                    return existing

            # If transaction_id matches, it's definitely a duplicate
            if line.transaction_id and existing.transaction_id:
                if line.transaction_id == existing.transaction_id:
                    return existing

            # Check description similarity
            if line.description and existing.description:
                # Simple word overlap check
                words1 = set(line.description.upper().split())
                words2 = set(existing.description.upper().split())
                if words1 and words2:
                    overlap = len(words1 & words2) / len(words1 | words2)
                    if overlap > 0.7:
                        return existing

        return None

    def import_statement(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: UUID,
        statement_number: str,
        statement_date: date,
        period_start: date,
        period_end: date,
        opening_balance: Decimal,
        closing_balance: Decimal,
        lines: List[StatementLineInput],
        import_source: Optional[str] = None,
        import_filename: Optional[str] = None,
        imported_by: Optional[UUID] = None,
        check_duplicates: bool = True,
        skip_duplicates: bool = True,
    ) -> StatementImportResult:
        """Import a bank statement with lines."""
        # Validate bank account
        bank_account = db.get(BankAccount, bank_account_id)
        if not bank_account:
            raise HTTPException(status_code=404, detail=f"Bank account {bank_account_id} not found")

        if bank_account.organization_id != organization_id:
            raise HTTPException(status_code=403, detail="Bank account does not belong to this organization")

        # Check for duplicate statement
        existing = db.execute(
            select(BankStatement).where(
                and_(
                    BankStatement.bank_account_id == bank_account_id,
                    BankStatement.statement_number == statement_number,
                )
            )
        ).scalar_one_or_none()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Statement {statement_number} already exists for this account",
            )

        # Calculate totals
        total_credits = Decimal("0")
        total_debits = Decimal("0")
        for line_input in lines:
            if line_input.transaction_type == StatementLineType.credit:
                total_credits += line_input.amount
            else:
                total_debits += line_input.amount

        # Create statement
        statement = BankStatement(
            organization_id=organization_id,
            bank_account_id=bank_account_id,
            statement_number=statement_number,
            statement_date=statement_date,
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            total_credits=total_credits,
            total_debits=total_debits,
            currency_code=bank_account.currency_code,
            status=BankStatementStatus.imported,
            import_source=import_source,
            import_filename=import_filename,
            imported_by=imported_by,
            total_lines=len(lines),
            unmatched_lines=len(lines),
        )
        db.add(statement)
        db.flush()

        # Import lines
        result = StatementImportResult(
            statement=statement,
            lines_imported=0,
            lines_skipped=0,
        )

        for line_input in lines:
            try:
                # Check for duplicates
                if check_duplicates:
                    duplicate = self._check_duplicate_line(db, bank_account_id, line_input)
                    if duplicate:
                        result.duplicates_found += 1
                        result.duplicate_lines.append(
                            DuplicateLineInfo(
                                line_number=line_input.line_number,
                                transaction_date=line_input.transaction_date,
                                amount=line_input.amount,
                                description=line_input.description,
                                original_statement_id=duplicate.statement_id,
                                original_line_id=duplicate.line_id,
                            )
                        )
                        if skip_duplicates:
                            result.lines_skipped += 1
                            result.warnings.append(
                                f"Line {line_input.line_number}: Skipped as duplicate of existing transaction"
                            )
                            continue

                line = BankStatementLine(
                    statement_id=statement.statement_id,
                    line_number=line_input.line_number,
                    transaction_id=line_input.transaction_id,
                    transaction_date=line_input.transaction_date,
                    value_date=line_input.value_date,
                    transaction_type=line_input.transaction_type,
                    amount=line_input.amount,
                    running_balance=line_input.running_balance,
                    description=line_input.description,
                    reference=line_input.reference,
                    payee_payer=line_input.payee_payer,
                    bank_reference=line_input.bank_reference,
                    check_number=line_input.check_number,
                    bank_category=line_input.bank_category,
                    bank_code=line_input.bank_code,
                    raw_data=line_input.raw_data,
                    is_matched=False,
                )
                db.add(line)
                result.lines_imported += 1
            except Exception as e:
                result.lines_skipped += 1
                result.errors.append(f"Line {line_input.line_number}: {str(e)}")

        # Update bank account with latest statement info
        bank_account.last_statement_date = datetime.combine(
            statement_date,
            datetime.min.time(),
            tzinfo=timezone.utc,
        )
        bank_account.last_statement_balance = closing_balance

        db.flush()

        # Validate statement balance
        if not statement.is_balanced:
            result.warnings.append(
                f"Statement does not balance: "
                f"Opening ({opening_balance}) + Credits ({total_credits}) - "
                f"Debits ({total_debits}) != Closing ({closing_balance})"
            )

        return result

    def get(
        self,
        db: Session,
        organization_id: UUID,
        statement_id: UUID,
    ) -> Optional[BankStatement]:
        """Get a statement by ID within an organization."""
        statement = db.get(BankStatement, statement_id)
        if not statement or statement.organization_id != organization_id:
            return None
        return statement

    def get_with_lines(
        self,
        db: Session,
        organization_id: UUID,
        statement_id: UUID,
    ) -> Optional[BankStatement]:
        """Get a statement with all lines loaded within an organization."""
        statement = self.get(db, organization_id, statement_id)
        if statement:
            _ = statement.lines
        return statement

    def list(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: Optional[UUID] = None,
        status: Optional[BankStatementStatus] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[BankStatement]:
        """List statements with optional filters."""
        query = select(BankStatement).where(
            BankStatement.organization_id == organization_id
        )

        if bank_account_id:
            query = query.where(BankStatement.bank_account_id == bank_account_id)
        if status:
            query = query.where(BankStatement.status == status)
        if start_date:
            query = query.where(BankStatement.statement_date >= start_date)
        if end_date:
            query = query.where(BankStatement.statement_date <= end_date)

        query = query.order_by(BankStatement.statement_date.desc())
        query = query.offset(offset).limit(limit)

        return list(db.execute(query).scalars().all())

    def count(
        self,
        db: Session,
        organization_id: UUID,
        bank_account_id: Optional[UUID] = None,
        status: Optional[BankStatementStatus] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> int:
        """Count statements matching filters (for pagination)."""
        query = select(func.count(BankStatement.statement_id)).where(
            BankStatement.organization_id == organization_id
        )

        if bank_account_id:
            query = query.where(BankStatement.bank_account_id == bank_account_id)
        if status:
            query = query.where(BankStatement.status == status)
        if start_date:
            query = query.where(BankStatement.statement_date >= start_date)
        if end_date:
            query = query.where(BankStatement.statement_date <= end_date)

        return db.execute(query).scalar() or 0

    def get_unmatched_lines(
        self,
        db: Session,
        statement_id: UUID,
    ) -> List[BankStatementLine]:
        """Get all unmatched lines for a statement."""
        query = select(BankStatementLine).where(
            and_(
                BankStatementLine.statement_id == statement_id,
                BankStatementLine.is_matched == False,
            )
        ).order_by(BankStatementLine.transaction_date, BankStatementLine.line_number)

        return list(db.execute(query).scalars().all())

    def mark_line_matched(
        self,
        db: Session,
        line_id: UUID,
        journal_line_id: UUID,
        matched_by: Optional[UUID] = None,
    ) -> BankStatementLine:
        """Mark a statement line as matched to a GL entry."""
        line = db.get(BankStatementLine, line_id)
        if not line:
            raise HTTPException(status_code=404, detail=f"Statement line {line_id} not found")

        line.is_matched = True
        line.matched_at = datetime.utcnow()
        line.matched_by = matched_by
        line.matched_journal_line_id = journal_line_id

        # Update statement counts
        statement = line.statement
        statement.matched_lines += 1
        statement.unmatched_lines -= 1

        # Check if fully reconciled
        if statement.unmatched_lines == 0:
            statement.status = BankStatementStatus.reconciled

        db.flush()
        return line

    def unmatch_line(
        self,
        db: Session,
        line_id: UUID,
    ) -> BankStatementLine:
        """Unmatch a statement line."""
        line = db.get(BankStatementLine, line_id)
        if not line:
            raise HTTPException(status_code=404, detail=f"Statement line {line_id} not found")

        if not line.is_matched:
            return line

        line.is_matched = False
        line.matched_at = None
        line.matched_by = None
        line.matched_journal_line_id = None

        # Update statement counts
        statement = line.statement
        statement.matched_lines -= 1
        statement.unmatched_lines += 1

        if statement.status == BankStatementStatus.reconciled:
            statement.status = BankStatementStatus.processing

        db.flush()
        return line

    def update_status(
        self,
        db: Session,
        statement_id: UUID,
        status: BankStatementStatus,
    ) -> BankStatement:
        """Update statement status."""
        statement = db.get(BankStatement, statement_id)
        if not statement:
            raise HTTPException(status_code=404, detail=f"Statement {statement_id} not found")

        statement.status = status
        db.flush()
        return statement

    def delete(self, db: Session, statement_id: UUID) -> bool:
        """Delete a statement and its lines."""
        statement = db.get(BankStatement, statement_id)
        if not statement:
            return False

        if statement.status in [
            BankStatementStatus.reconciled,
            BankStatementStatus.closed,
        ]:
            raise HTTPException(status_code=400, detail="Cannot delete a reconciled or closed statement")

        db.delete(statement)
        db.flush()
        return True

    def get_statement_summary(
        self,
        db: Session,
        bank_account_id: UUID,
    ) -> Dict:
        """Get summary statistics for statements of an account."""
        query = select(
            func.count(BankStatement.statement_id).label("total_statements"),
            func.sum(BankStatement.total_lines).label("total_lines"),
            func.sum(BankStatement.matched_lines).label("matched_lines"),
            func.sum(BankStatement.unmatched_lines).label("unmatched_lines"),
        ).where(BankStatement.bank_account_id == bank_account_id)

        result = db.execute(query).one()

        return {
            "total_statements": result.total_statements or 0,
            "total_lines": result.total_lines or 0,
            "matched_lines": result.matched_lines or 0,
            "unmatched_lines": result.unmatched_lines or 0,
            "match_rate": (
                (result.matched_lines / result.total_lines * 100)
                if result.total_lines
                else 0
            ),
        }


# Singleton instance
bank_statement_service = BankStatementService()
