# ruff: noqa: F401
"""
Rule-Driven Reconciliation Engine.

Processes custom (non-system) match rules against unmatched bank
statement lines.  Each rule targets a source document type and has
conditions that filter eligible lines.  Generic per-type handlers
load candidates, extract references, and match.

Architecture
────────────
System rules (the 7 built-in passes) are still handled by the
battle-tested ``AutoReconciliationService`` methods.  This engine
handles **custom rules** — user-defined rules for new integrations
or organisation-specific matching patterns.

New integrations = new DB rules, zero code changes.

Handlers per source_doc_type:
  CUSTOMER_PAYMENT  — match credit lines to AR customer payments
  SUPPLIER_PAYMENT  — match debit lines to AP supplier payments
  PAYMENT_INTENT    — match by gateway reference (any payment gateway)
  BANK_FEE          — create GL journal for identified fee lines
  INTER_BANK        — cross-bank transfer matching within date window
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm import Session, joinedload

from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
)
from app.models.finance.banking.reconciliation_match_rule import (
    ReconciliationMatchRule,
)
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine

logger = logging.getLogger(__name__)

# Paystack transaction IDs: 12-14 hex characters
_HEX_REF_RE = re.compile(r"[0-9a-f]{12,14}", re.IGNORECASE)

# Default amount tolerance (exact amount only)
_DEFAULT_TOLERANCE = Decimal("0.00")

# System user for auto-created journals
_SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")


@dataclass
class EngineMatch:
    """A single match result from the engine."""

    line: BankStatementLine
    journal_line: JournalEntryLine
    source_type: str
    source_id: UUID | None
    confidence: int
    explanation: str


@dataclass
class EngineResult:
    """Aggregated result from custom rule processing."""

    matched: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class EngineContext:
    """Shared state for a single engine run."""

    db: Session
    organization_id: UUID
    statement: BankStatement
    bank_account: BankAccount
    amount_tolerance: Decimal
    date_buffer_days: int
    matched_line_ids: set[UUID]
    matched_source_ids: set[UUID]
    extra_gl_account_ids: set[UUID] | None
    result: EngineResult


__all__ = [name for name in globals() if not name.startswith("__")]
