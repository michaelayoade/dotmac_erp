"""Helpers for suspicious bank match review queues."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.orm import Session

from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    BankStatementLineMatch,
)
from app.models.finance.banking.reconciliation_match_rule import (
    ReconciliationMatchLog,
)

SUSPICIOUS_PHRASES = (
    "date+amount fallback",
    "amount match,",
    "date offset",
)


@dataclass
class SuspiciousMatch:
    statement_line_id: UUID
    statement_id: UUID
    journal_line_id: UUID
    statement_number: str | None
    account_id: UUID | None
    bank_name: str | None
    account_number: str | None
    transaction_date: object
    amount: Decimal
    description: str | None
    reference: str | None
    match_state: str
    confidence_score: int | None
    explanation: str | None
    matched_at: datetime | None

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence_score is not None and self.confidence_score < 90

    @property
    def is_fallback_reason(self) -> bool:
        explanation = (self.explanation or "").lower()
        return any(phrase in explanation for phrase in SUSPICIOUS_PHRASES)

    @property
    def is_suspicious(self) -> bool:
        return self.is_low_confidence or self.is_fallback_reason

def collect_suspicious_matches(
    db: Session,
    org_id: UUID,
    *,
    account_id: UUID | None = None,
    match_state: str | None = None,
) -> list[SuspiciousMatch]:
    return list_suspicious_matches(
        db,
        org_id,
        account_id=account_id,
        match_state=match_state,
    )


def _base_suspicious_match_stmt(
    org_id: UUID,
    *,
    account_id: UUID | None = None,
    match_state: str | None = None,
):
    latest_log_sq = (
        select(
            ReconciliationMatchLog.statement_line_id.label("statement_line_id"),
            ReconciliationMatchLog.journal_line_id.label("journal_line_id"),
            ReconciliationMatchLog.confidence_score.label("confidence_score"),
            ReconciliationMatchLog.explanation.label("explanation"),
            func.row_number()
            .over(
                partition_by=(
                    ReconciliationMatchLog.statement_line_id,
                    ReconciliationMatchLog.journal_line_id,
                ),
                order_by=ReconciliationMatchLog.matched_at.desc(),
            )
            .label("rn"),
        )
        .where(ReconciliationMatchLog.organization_id == org_id)
        .subquery()
    )

    suspicious_predicates = [latest_log_sq.c.confidence_score < 90]
    for phrase in SUSPICIOUS_PHRASES:
        suspicious_predicates.append(latest_log_sq.c.explanation.ilike(f"%{phrase}%"))

    stmt = (
        select(
            BankStatementLineMatch,
            BankStatementLine,
            BankStatement,
            BankAccount,
            latest_log_sq.c.confidence_score,
            latest_log_sq.c.explanation,
        )
        .join(
            BankStatementLine,
            BankStatementLine.line_id == BankStatementLineMatch.statement_line_id,
        )
        .join(
            BankStatement,
            BankStatement.statement_id == BankStatementLine.statement_id,
        )
        .outerjoin(
            BankAccount,
            BankAccount.bank_account_id == BankStatement.bank_account_id,
        )
        .outerjoin(
            latest_log_sq,
            and_(
                latest_log_sq.c.statement_line_id
                == BankStatementLineMatch.statement_line_id,
                latest_log_sq.c.journal_line_id
                == BankStatementLineMatch.journal_line_id,
                latest_log_sq.c.rn == 1,
            ),
        )
        .where(BankStatement.organization_id == org_id)
        .where(or_(*suspicious_predicates))
        .order_by(BankStatementLineMatch.matched_at.desc())
    )
    if account_id:
        stmt = stmt.where(BankStatement.bank_account_id == account_id)
    if match_state in {"suggested", "confirmed"}:
        stmt = stmt.where(BankStatementLineMatch.match_state == match_state)

    return stmt


def list_suspicious_matches(
    db: Session,
    org_id: UUID,
    *,
    account_id: UUID | None = None,
    match_state: str | None = None,
    page: int | None = None,
    limit: int | None = None,
) -> list[SuspiciousMatch]:
    stmt = _base_suspicious_match_stmt(
        org_id,
        account_id=account_id,
        match_state=match_state,
    )
    if page is not None and limit is not None:
        offset = max(page - 1, 0) * limit
        stmt = stmt.limit(limit).offset(offset)

    rows = db.execute(stmt).all()
    suspicious: list[SuspiciousMatch] = []
    for match, line, statement, account, confidence_score, explanation in rows:
        item = SuspiciousMatch(
            statement_line_id=line.line_id,
            statement_id=statement.statement_id,
            journal_line_id=match.journal_line_id,
            statement_number=statement.statement_number,
            account_id=statement.bank_account_id,
            bank_name=getattr(account, "bank_name", None),
            account_number=getattr(account, "account_number", None),
            transaction_date=line.transaction_date,
            amount=line.amount,
            description=line.description,
            reference=line.reference or line.bank_reference,
            match_state=match.match_state,
            confidence_score=confidence_score,
            explanation=explanation,
            matched_at=match.matched_at,
        )
        suspicious.append(item)
    return suspicious


def summarize_suspicious_matches(
    db: Session,
    org_id: UUID,
    *,
    account_id: UUID | None = None,
    match_state: str | None = None,
) -> dict[str, int]:
    base_sq = (
        _base_suspicious_match_stmt(
            org_id,
            account_id=account_id,
            match_state=match_state,
        )
        .order_by(None)
        .subquery()
    )
    fallback_predicate = or_(
        *[
            base_sq.c.explanation.ilike(f"%{phrase}%")
            for phrase in SUSPICIOUS_PHRASES
        ]
    )
    summary_row = db.execute(
        select(
            func.count().label("total_count"),
            func.count(
                case((base_sq.c.match_state == "suggested", 1))
            ).label("suggested_count"),
            func.count(
                case((base_sq.c.match_state == "confirmed", 1))
            ).label("confirmed_count"),
            func.count(
                case((base_sq.c.confidence_score < 90, 1))
            ).label("low_confidence_count"),
            func.count(case((fallback_predicate, 1))).label("fallback_count"),
        ).select_from(base_sq)
    ).one()
    return {
        "total_count": int(summary_row.total_count or 0),
        "suggested_count": int(summary_row.suggested_count or 0),
        "confirmed_count": int(summary_row.confirmed_count or 0),
        "low_confidence_count": int(summary_row.low_confidence_count or 0),
        "fallback_count": int(summary_row.fallback_count or 0),
    }


def clear_suspicious_suggested_matches(
    db: Session,
    org_id: UUID,
    *,
    account_id: UUID | None = None,
) -> int:
    matches = collect_suspicious_matches(
        db,
        org_id,
        account_id=account_id,
        match_state="suggested",
    )
    cleared = 0
    for match in matches:
        if match.match_state != "suggested":
            continue
        db.execute(
            delete(BankStatementLineMatch).where(
                and_(
                    BankStatementLineMatch.statement_line_id == match.statement_line_id,
                    BankStatementLineMatch.journal_line_id == match.journal_line_id,
                    BankStatementLineMatch.match_state == "suggested",
                )
            )
        )
        cleared += 1
    return cleared
