# ruff: noqa: F401
"""
Auto-Reconciliation Service.

Deterministic matching of bank statement lines to internal payment records.

Seven matching strategies run in sequence:
1. **PaymentIntent** — matches DotMac-initiated Paystack transfers using
   ``paystack_reference`` as a join key.
2. **Splynx CustomerPayment by reference** — extracts Paystack transaction IDs
   from ``CustomerPayment.description`` (regex ``[0-9a-f]{12,14}``) and matches
   against statement line references.  Also falls back to the Splynx receipt
   number in ``CustomerPayment.reference``.
3. **Date + amount fallback** — for remaining unmatched Splynx payments,
   matches when exactly one payment and one statement line share the same
   date and amount.
4. **AP supplier payments** — matches CLEARED ``SupplierPayment`` records
   by ``payment_number`` / ``reference`` first, then by date + amount.
   Only matches **debit** bank lines (outgoing).
5. **Non-Splynx AR payments** — matches CLEARED ``CustomerPayment`` records
   where ``splynx_id IS NULL`` (app-created receipts) by reference first,
   then by date + amount.  Only matches **credit** bank lines (incoming).
6. **Bank fees** — identifies Paystack fee lines (``Paystack Fee:`` in
   description), creates a GL journal (debit Finance Cost, credit bank GL),
   and auto-matches the statement line to the new journal.
7. **Settlements** — matches Paystack settlement debits to corresponding
   deposits on receiving bank accounts (UBA, Zenith) within a 0–10 day
   date window.  Creates inter-bank transfer journals and matches both
   the outflow and inflow sides.

Strategies 1–5 share the same GL journal lookup
(``JournalEntry.correlation_id``) and delegate the actual match to
``BankReconciliationService.match_statement_line()``.
Strategies 6 and 7 create their own journals before matching.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.orm import Session, joinedload

from app.models.finance.ap.supplier_payment import (
    APPaymentStatus,
    SupplierPayment,
)
from app.models.finance.ar.customer_payment import (
    CustomerPayment,
    PaymentStatus,
)
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    StatementLineType,
)
from app.models.finance.gl.journal_entry import (
    JournalEntry,
    JournalStatus,
)
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.payments.payment_intent import (
    PaymentDirection,
    PaymentIntent,
    PaymentIntentStatus,
)
from app.services.finance.banking.programmatic_reconciliation import (
    ProgrammaticReconciliationEngine,
    build_extra_gl_account_ids,
)
from app.services.finance.banking.reconciliation_policy_service import (
    reconciliation_policy_service,
)
from app.services.finance.banking.reconciliation_runtime import ReconciliationRunContext
from app.services.finance.posting.base import PostingResult

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

# Exact amount matching is required for auto-match.
AMOUNT_TOLERANCE = Decimal("0.00")

# Paystack transaction IDs are 12-14 hex characters
_PAYSTACK_REF_RE = re.compile(r"[0-9a-f]{12,14}", re.IGNORECASE)

# Pass 6: Bank fee detection (Paystack processing fees)
_BANK_FEE_RE = re.compile(r"Paystack Fee:", re.IGNORECASE)
FINANCE_COST_ACCOUNT_CODE = "6080"
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")

# Pass 7: Settlement detection (inter-bank transfers)
_SETTLEMENT_RE = re.compile(r"Settlement( to bank)?:", re.IGNORECASE)
SETTLEMENT_DATE_WINDOW_DAYS = 10
# Paystack-related deposit patterns on receiving banks.
# Matches both "Paystack payout" descriptions and PSST10-prefixed batch codes.
_PAYSTACK_DEPOSIT_RE = re.compile(r"paystack|PSST10", re.IGNORECASE)
_PAYSTACK_OPEX_RE = re.compile(r"paystack.*opex|opex.*paystack", re.IGNORECASE)

# Dry-run contra transfer suggestion pass (no posting/matching yet)
_CONTRA_TRANSFER_RE = re.compile(
    r"transfer|inter.?bank|xfer|trx\s*to|trx\s*from|trf",
    re.IGNORECASE,
)
CONTRA_DATE_WINDOW_DAYS = 2
CONTRA_MIN_SCORE = 90


@dataclass
class AutoMatchDefaults:
    """Runtime configuration loaded from DomainSettings (banking domain)."""

    pass_payment_intents_enabled: bool = True
    # Splynx is retired. These two passes matched CustomerPayments carrying a
    # `splynx_id`; turning them off removes `receivable_payment_synced` from
    # the policy's enabled providers, so both Splynx strategies short-circuit
    # on their own `allows_*` checks and do nothing.
    #
    # Nothing is deleted yet, deliberately: this is the reversible half of the
    # retirement. Set either back to True to restore the old behaviour while
    # the change is being verified. The code goes in a later stage, once it
    # has been confirmed that no Splynx-era payment lost a match it needed.
    #
    # Historical Splynx payments stay matchable — `_load_ar_payments` no
    # longer excludes them (it used to filter `splynx_id IS NULL`), so they
    # fall to the general AR pass instead of becoming invisible.
    pass_splynx_by_ref_enabled: bool = False
    pass_splynx_date_amount_enabled: bool = False
    pass_ap_payments_enabled: bool = True
    pass_ar_payments_enabled: bool = True
    pass_bank_fees_enabled: bool = False
    pass_settlements_enabled: bool = False
    amount_tolerance: Decimal = Decimal("0.00")
    date_buffer_days: int = 3
    settlement_date_window_days: int = 1
    finance_cost_account_code: str = "6080"


@dataclass
class AutoMatchResult:
    """Result of an auto-match operation."""

    matched: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    contra_suggestions: list[dict[str, object]] = field(default_factory=list)


__all__ = [name for name in globals() if not name.startswith("__")]
