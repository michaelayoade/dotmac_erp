# ruff: noqa: F401
"""
Bank Reconciliation Service.

Provides bank reconciliation functionality including auto-matching,
match suggestions, multi-match, and reconciliation workflow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, select
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
    BankStatementLineMatch,
)
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.audit_dispatcher import fire_audit_event
from app.services.finance.banking.payment_metadata import PaymentMetadata

logger = logging.getLogger(__name__)

# Alias: BankReconciliationService.list shadows builtin `list` in
# PEP 563 string annotations, causing mypy valid-type errors.
_list = list

# Map source_document_type → URL pattern for linking to source documents.
# The placeholder {} is replaced with the source_document_id (UUID).
SOURCE_URL_MAP: dict[str, str] = {
    "CUSTOMER_PAYMENT": "/finance/ar/receipts/{}",
    "SUPPLIER_PAYMENT": "/finance/ap/payments/{}",
    "INVOICE": "/finance/ar/invoices/{}",
    "AR_INVOICE": "/finance/ar/invoices/{}",
    "CUSTOMER_INVOICE": "/finance/ar/invoices/{}",
    "SUPPLIER_INVOICE": "/finance/ap/invoices/{}",
    "AP_INVOICE": "/finance/ap/invoices/{}",
    "EXPENSE": "/finance/expenses/{}",
    "EXPENSE_CLAIM": "/finance/expenses/{}",
    "JOURNAL_ENTRY": "/finance/gl/journals/{}",
}

AMOUNT_MISMATCH_RELATIVE_THRESHOLD = Decimal("0.01")
AMOUNT_MISMATCH_ABSOLUTE_TOLERANCE = Decimal("0.01")


def _build_source_url(
    source_type: str | None,
    source_id: UUID | None,
    entry_id: UUID | None = None,
) -> str:
    """Build a URL to the source document for a GL journal entry.

    Falls back to the journal entry URL if no specific mapping exists.
    Returns empty string if nothing is resolvable.
    """
    if source_type and source_id:
        pattern = SOURCE_URL_MAP.get(source_type)
        if pattern:
            return pattern.format(source_id)
    # Fallback: link to the journal entry itself
    if entry_id:
        return f"/finance/gl/journals/{entry_id}"
    return ""


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
    source_url: str = ""
    amount_matched: bool = False
    # Set when the suggestion was persisted by the Celery auto-engine
    # (match_state='suggested') rather than recomputed live in the workspace.
    from_auto_engine: bool = False
    match_reason: str | None = None


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


__all__ = [name for name in globals() if not name.startswith("__")]
