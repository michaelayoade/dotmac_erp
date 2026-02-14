"""
Auto-Reconciliation Service.

Deterministic matching of bank statement lines to internal payment records.

Five matching strategies run in sequence:
1. **PaymentIntent** — matches DotMac-initiated Paystack transfers using
   ``paystack_reference`` as a join key.
2. **Splynx CustomerPayment by reference** — extracts Paystack transaction IDs
   from ``CustomerPayment.description`` (regex ``[0-9a-f]{12,14}``) and matches
   against statement line references.  Also falls back to the Splynx receipt
   number in ``CustomerPayment.reference``.
3. **Date + amount fallback** — for remaining unmatched payments, matches when
   exactly one payment and one statement line share the same date and amount.
4. **Bank fees** — identifies Paystack fee lines (``Paystack Fee:`` in
   description), creates a GL journal (debit Finance Cost, credit bank GL),
   and auto-matches the statement line to the new journal.
5. **Settlements** — matches Paystack settlement debits to corresponding
   deposits on receiving bank accounts (UBA, Zenith) within a 0–10 day
   date window.  Creates inter-bank transfer journals and matches both
   the outflow and inflow sides.

Strategies 1–3 share the same GL journal lookup
(``JournalEntry.correlation_id``) and delegate the actual match to
``BankReconciliationService.match_statement_line()``.
Strategies 4 and 5 create their own journals before matching.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

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

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

# Tolerance for amount matching (handles rounding in bank CSV imports)
AMOUNT_TOLERANCE = Decimal("0.01")

# Paystack transaction IDs are 12-14 hex characters
_PAYSTACK_REF_RE = re.compile(r"[0-9a-f]{12,14}", re.IGNORECASE)

# Pass 4: Bank fee detection (Paystack processing fees)
_BANK_FEE_RE = re.compile(r"Paystack Fee:", re.IGNORECASE)
FINANCE_COST_ACCOUNT_CODE = "6080"
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")

# Pass 5: Settlement detection (inter-bank transfers)
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
class AutoMatchResult:
    """Result of an auto-match operation."""

    matched: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    contra_suggestions: list[dict[str, object]] = field(default_factory=list)


class AutoReconciliationService:
    """Deterministic matching of internal payments to bank statement lines.

    After a bank statement is imported, this service scans unmatched lines and
    attempts to match them using five strategies:

    1. **PaymentIntent pass** — for DotMac-initiated Paystack transfers.
       The ``paystack_reference`` is a deterministic join key.
    2. **Splynx payment pass** — for payments collected via Splynx and synced
       as ``CustomerPayment`` records.  Extracts the Paystack transaction ID
       from the payment description using regex, and also tries the Splynx
       receipt number in ``reference``.
    3. **Date + amount fallback** — for remaining unmatched payments, matches
       when exactly one payment and one statement line share the same date
       and amount on the same bank account.
    4. **Bank fee pass** — for Paystack processing fee lines.  Creates a GL
       journal (debit Finance Cost 6080, credit bank GL) and auto-matches
       the statement line to the new journal entry.
    5. **Settlement pass** — for Paystack settlement transfers.  Finds the
       matching deposit on receiving banks (UBA, Zenith) within 0–5 days,
       creates an inter-bank transfer journal, and matches both sides.

    Strategies 1–3 verify amount (within tolerance) and find the GL journal
    line via ``correlation_id`` before delegating the match to
    ``BankReconciliationService``.  Strategies 4–5 create their own journals.
    """

    # ── Public API ──────────────────────────────────────────────────

    def auto_match_statement(
        self,
        db: Session,
        organization_id: UUID,
        statement_id: UUID,
        *,
        include_contra_suggestions: bool = False,
    ) -> AutoMatchResult:
        """Match unmatched statement lines against known internal payments.

        Runs five passes in sequence:
        1. PaymentIntent (Paystack-initiated transfers)
        2. Splynx CustomerPayment by reference (Paystack ref from description)
        3. Date + amount greedy matching (fallback)
        4. Bank fees (creates GL journals for Paystack fee lines)
        5. Settlements (cross-bank transfer matching, 0–5 day window)

        Lines matched in earlier passes are excluded from later passes.

        Args:
            db: Database session.
            organization_id: Tenant scope.
            statement_id: The statement to process.

        Returns:
            AutoMatchResult with match/skip/error counts.
        """
        result = AutoMatchResult()

        # 1. Load statement + bank account
        statement = db.get(BankStatement, statement_id)
        if not statement or statement.organization_id != organization_id:
            result.errors.append(f"Statement {statement_id} not found")
            return result

        bank_account = db.get(BankAccount, statement.bank_account_id)
        if not bank_account or not bank_account.gl_account_id:
            result.errors.append("Bank account or GL account not configured")
            return result

        # Build fallback GL accounts: all OTHER bank accounts' GL IDs.
        # This handles payments whose GL journals still reference a
        # previous bank account's GL after a bank_account_id reassignment.
        all_bank_gl_ids = set(
            db.scalars(
                select(BankAccount.gl_account_id).where(
                    BankAccount.organization_id == organization_id,
                    BankAccount.gl_account_id.isnot(None),
                    BankAccount.gl_account_id != bank_account.gl_account_id,
                )
            ).all()
        )
        extra_gl: set[UUID] | None = all_bank_gl_ids or None

        # 2. Load unmatched lines
        unmatched_lines = list(
            db.scalars(
                select(BankStatementLine).where(
                    BankStatementLine.statement_id == statement_id,
                    BankStatementLine.is_matched.is_(False),
                )
            ).all()
        )

        if not unmatched_lines:
            return result

        # 3. Load all eligible Splynx payments once (shared by passes 2 & 3)
        splynx_payments = self._load_splynx_payments(db, organization_id, statement)

        # 4. Pass 1: PaymentIntent matching
        matched_line_ids: set[UUID] = set()
        matched_payment_ids: set[UUID] = set()
        self._match_payment_intents(
            db,
            organization_id,
            statement,
            bank_account,
            unmatched_lines,
            matched_line_ids,
            result,
            extra_gl_account_ids=extra_gl,
        )

        # 5. Pass 2: Splynx CustomerPayment by reference
        still_unmatched = [
            line for line in unmatched_lines if line.line_id not in matched_line_ids
        ]
        if still_unmatched and splynx_payments:
            self._match_splynx_payments(
                db,
                organization_id,
                bank_account,
                splynx_payments,
                still_unmatched,
                matched_line_ids,
                matched_payment_ids,
                result,
                extra_gl_account_ids=extra_gl,
            )

        # 6. Pass 3: Date + amount unique matching (fallback)
        still_unmatched = [
            line for line in unmatched_lines if line.line_id not in matched_line_ids
        ]
        remaining_payments = [
            p for p in splynx_payments if p.payment_id not in matched_payment_ids
        ]
        if still_unmatched and remaining_payments:
            self._match_by_date_amount(
                db,
                organization_id,
                bank_account,
                remaining_payments,
                still_unmatched,
                matched_line_ids,
                matched_payment_ids,
                result,
                extra_gl_account_ids=extra_gl,
            )

        # 7. Pass 4: Bank fee matching (creates journals for fee lines)
        still_unmatched = [
            line for line in unmatched_lines if line.line_id not in matched_line_ids
        ]
        if still_unmatched:
            self._match_bank_fees(
                db,
                organization_id,
                bank_account,
                still_unmatched,
                matched_line_ids,
                result,
            )

        # 8. Pass 5: Settlement matching (cross-bank transfer)
        still_unmatched = [
            line for line in unmatched_lines if line.line_id not in matched_line_ids
        ]
        if still_unmatched:
            self._match_settlements(
                db,
                organization_id,
                bank_account,
                still_unmatched,
                matched_line_ids,
                result,
            )

        # Recalculate skipped (lines not matched by any pass)
        result.skipped = len(unmatched_lines) - result.matched - len(result.errors)

        # 9. Optional dry-run pass: contra transfer suggestions (no posting)
        if include_contra_suggestions:
            still_unmatched = [
                line for line in unmatched_lines if line.line_id not in matched_line_ids
            ]
            self._suggest_contra_transfers(
                db,
                organization_id,
                bank_account,
                still_unmatched,
                matched_line_ids,
                result,
            )

        return result

    def _suggest_contra_transfers(
        self,
        db: Session,
        organization_id: UUID,
        source_bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        result: AutoMatchResult,
    ) -> None:
        """Suggest contra transfer pairs in dry-run mode (no side effects)."""
        from datetime import timedelta

        from app.services.finance.banking.contra_matching import (
            ContraLineCandidate,
            choose_best_contra_matches,
        )

        source_lines = [
            line
            for line in unmatched_lines
            if line.line_id not in matched_line_ids
            and line.transaction_type == StatementLineType.debit
            and (
                (line.description and _CONTRA_TRANSFER_RE.search(line.description))
                or (line.reference and _CONTRA_TRANSFER_RE.search(line.reference))
            )
        ]
        if not source_lines:
            return

        other_bank_ids = list(
            db.scalars(
                select(BankAccount.bank_account_id).where(
                    BankAccount.organization_id == organization_id,
                    BankAccount.bank_account_id != source_bank_account.bank_account_id,
                    BankAccount.gl_account_id.isnot(None),
                )
            ).all()
        )
        if not other_bank_ids:
            return

        date_window = timedelta(days=CONTRA_DATE_WINDOW_DAYS)
        min_date = min(line.transaction_date for line in source_lines) - date_window
        max_date = max(line.transaction_date for line in source_lines) + date_window

        destination_lines = list(
            db.scalars(
                select(BankStatementLine)
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .where(
                    BankStatement.organization_id == organization_id,
                    BankStatement.bank_account_id.in_(other_bank_ids),
                    BankStatementLine.is_matched.is_(False),
                    BankStatementLine.transaction_type == StatementLineType.credit,
                    BankStatementLine.transaction_date.between(min_date, max_date),
                )
            ).all()
        )
        if not destination_lines:
            return

        source_candidates = [
            ContraLineCandidate(
                line_id=line.line_id,
                bank_account_id=source_bank_account.bank_account_id,
                transaction_date=line.transaction_date,
                amount=line.amount,
                reference=line.reference,
                description=line.description,
            )
            for line in source_lines
        ]
        destination_candidates = []
        for line in destination_lines:
            statement = db.get(BankStatement, line.statement_id)
            if not statement:
                continue
            destination_candidates.append(
                ContraLineCandidate(
                    line_id=line.line_id,
                    bank_account_id=statement.bank_account_id,
                    transaction_date=line.transaction_date,
                    amount=line.amount,
                    reference=line.reference,
                    description=line.description,
                )
            )
        if not destination_candidates:
            return

        matches = choose_best_contra_matches(
            source_candidates,
            destination_candidates,
            amount_tolerance=AMOUNT_TOLERANCE,
            date_window_days=CONTRA_DATE_WINDOW_DAYS,
            min_score=CONTRA_MIN_SCORE,
        )
        for match in matches:
            payload: dict[str, object] = {
                "source_line_id": str(match.source_line_id),
                "destination_line_id": str(match.destination_line_id),
                "score": match.score,
                "date_diff_days": match.date_diff_days,
                "amount_diff": str(match.amount_diff),
                "reasons": match.reasons,
            }
            result.contra_suggestions.append(payload)

    # ── Pass 1: PaymentIntent matching ──────────────────────────────

    def _match_payment_intents(
        self,
        db: Session,
        organization_id: UUID,
        statement: BankStatement,
        bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
    ) -> None:
        """Match lines against COMPLETED PaymentIntent records."""
        from datetime import timedelta

        date_buffer = timedelta(days=7)
        intent_query = select(PaymentIntent).where(
            PaymentIntent.organization_id == organization_id,
            PaymentIntent.bank_account_id == statement.bank_account_id,
            PaymentIntent.status == PaymentIntentStatus.COMPLETED,
        )
        if statement.period_start and statement.period_end:
            intent_query = intent_query.where(
                PaymentIntent.paid_at >= statement.period_start - date_buffer,
                PaymentIntent.paid_at
                < statement.period_end + date_buffer + timedelta(days=1),
            )
        intents = list(db.scalars(intent_query).all())

        if not intents:
            return

        # Build lookup: paystack_reference -> intent
        ref_to_intent: dict[str, PaymentIntent] = {
            intent.paystack_reference: intent for intent in intents
        }
        matched_intent_ids: set[UUID] = set()

        for line in unmatched_lines:
            try:
                intent = self._find_ref_in_line(line, ref_to_intent)
                if not intent:
                    continue

                if not self._amounts_match(line.amount, intent.amount):
                    logger.debug(
                        "PaymentIntent ref %s in line %s but amount mismatch: "
                        "line=%s, intent=%s",
                        intent.paystack_reference,
                        line.line_id,
                        line.amount,
                        intent.amount,
                    )
                    continue

                journal_line = self._find_journal_line(
                    db,
                    organization_id,
                    str(intent.intent_id),
                    bank_account.gl_account_id,
                    extra_gl_account_ids=extra_gl_account_ids,
                )
                if not journal_line:
                    logger.debug(
                        "No GL journal for intent %s (ref: %s)",
                        intent.intent_id,
                        intent.paystack_reference,
                    )
                    continue

                self._perform_match(db, organization_id, line, journal_line)
                matched_line_ids.add(line.line_id)
                matched_intent_ids.add(intent.intent_id)
                result.matched += 1
                logger.info(
                    "Auto-matched line %s to GL %s via PaymentIntent %s",
                    line.line_id,
                    journal_line.line_id,
                    intent.paystack_reference,
                )
            except Exception as e:
                logger.exception(
                    "Error matching line %s via PaymentIntent: %s",
                    line.line_id,
                    e,
                )
                result.errors.append(f"Line {line.line_number}: {e}")

        # Paystack OPEX fallback: match expense transfers by date+amount when
        # statement references are missing.
        self._match_expense_intents_by_date_amount(
            db,
            organization_id,
            bank_account,
            intents,
            unmatched_lines,
            matched_line_ids,
            matched_intent_ids,
            result,
            extra_gl_account_ids=extra_gl_account_ids,
        )

    def _match_expense_intents_by_date_amount(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        intents: list[PaymentIntent],
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        matched_intent_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
    ) -> None:
        """Fallback match for Paystack OPEX expense transfers by date+amount."""
        from datetime import date

        if not self._is_paystack_opex_account(bank_account):
            return

        _DateAmountKey = tuple[date, int]

        eligible_intents = [
            intent
            for intent in intents
            if intent.intent_id not in matched_intent_ids
            and intent.source_type == "EXPENSE_CLAIM"
            and intent.direction == PaymentDirection.OUTBOUND
            and intent.paid_at is not None
        ]
        if not eligible_intents:
            return

        intent_index: dict[_DateAmountKey, list[PaymentIntent]] = {}
        for intent in eligible_intents:
            key: _DateAmountKey = (
                intent.paid_at.date(),
                int(abs(intent.amount) * 100),
            )
            intent_index.setdefault(key, []).append(intent)

        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            if line.transaction_type != StatementLineType.debit:
                continue
            key = (line.transaction_date, int(abs(line.amount) * 100))
            line_index.setdefault(key, []).append(line)

        for key, key_intents in intent_index.items():
            key_lines = line_index.get(key, [])
            available_lines = [
                ln for ln in key_lines if ln.line_id not in matched_line_ids
            ]
            if not available_lines:
                continue

            pairs = min(len(key_intents), len(available_lines))
            for i in range(pairs):
                intent = key_intents[i]
                line = available_lines[i]
                try:
                    journal_line = self._find_journal_line(
                        db,
                        organization_id,
                        str(intent.intent_id),
                        bank_account.gl_account_id,
                        extra_gl_account_ids=extra_gl_account_ids,
                    )
                    if not journal_line:
                        continue

                    self._perform_match(db, organization_id, line, journal_line)
                    matched_line_ids.add(line.line_id)
                    matched_intent_ids.add(intent.intent_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via expense PaymentIntent %s "
                        "(date+amount, Paystack OPEX)",
                        line.line_id,
                        journal_line.line_id,
                        intent.paystack_reference,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via expense date+amount: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

    # ── Splynx payment loader (shared by passes 2 & 3) ─────────────

    def _load_splynx_payments(
        self,
        db: Session,
        organization_id: UUID,
        statement: BankStatement,
    ) -> list[CustomerPayment]:
        """Load eligible Splynx payments for the statement's bank account.

        Filters: ``splynx_id IS NOT NULL``, status CLEARED, has GL journal,
        has correlation_id, and matching bank_account_id + date range.
        """
        from datetime import timedelta

        date_buffer = timedelta(days=7)
        pmt_query = select(CustomerPayment).where(
            CustomerPayment.organization_id == organization_id,
            CustomerPayment.bank_account_id == statement.bank_account_id,
            CustomerPayment.splynx_id.isnot(None),
            CustomerPayment.status == PaymentStatus.CLEARED,
            CustomerPayment.journal_entry_id.isnot(None),
            CustomerPayment.correlation_id.isnot(None),
        )
        if statement.period_start and statement.period_end:
            pmt_query = pmt_query.where(
                CustomerPayment.payment_date >= statement.period_start - date_buffer,
                CustomerPayment.payment_date <= statement.period_end + date_buffer,
            )
        return list(db.scalars(pmt_query).all())

    # ── Pass 2: Splynx CustomerPayment by reference ──────────────

    @staticmethod
    def _extract_paystack_ref(description: str | None) -> str | None:
        """Extract Paystack transaction ID from a payment description.

        Paystack refs are 12-14 lowercase hex characters, e.g.
        ``69871fd7d9178``.  Returns lowercase for case-insensitive matching.
        """
        if not description:
            return None
        match = _PAYSTACK_REF_RE.search(description)
        return match.group(0).lower() if match else None

    def _match_splynx_payments(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        payments: list[CustomerPayment],
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        matched_payment_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
    ) -> None:
        """Match lines against Splynx-originated CustomerPayments by reference.

        Builds two lookup dicts:
        1. Paystack ref extracted from ``description`` (primary)
        2. Splynx receipt number from ``reference`` (fallback)
        """
        # Build lookup: paystack_ref_from_description -> payment
        ref_to_payment: dict[str, CustomerPayment] = {}
        for pmt in payments:
            paystack_ref = self._extract_paystack_ref(pmt.description)
            if paystack_ref:
                ref_to_payment[paystack_ref] = pmt

        # Also add Splynx receipt numbers as fallback keys
        for pmt in payments:
            if pmt.reference and pmt.reference not in ref_to_payment:
                ref_to_payment[pmt.reference] = pmt

        if not ref_to_payment:
            return

        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            try:
                payment = self._find_ref_in_line(line, ref_to_payment)
                if not payment:
                    continue

                if not self._amounts_match(line.amount, payment.amount):
                    logger.debug(
                        "Splynx ref in line %s but amount mismatch: "
                        "line=%s, payment=%s",
                        line.line_id,
                        line.amount,
                        payment.amount,
                    )
                    continue

                # Query already filters correlation_id IS NOT NULL,
                # but guard for mypy:
                if not payment.correlation_id:
                    continue

                journal_line = self._find_journal_line(
                    db,
                    organization_id,
                    payment.correlation_id,
                    bank_account.gl_account_id,
                    extra_gl_account_ids=extra_gl_account_ids,
                )
                if not journal_line:
                    logger.debug(
                        "No GL journal for Splynx payment %s (ref: %s)",
                        payment.splynx_id,
                        payment.reference,
                    )
                    continue

                self._perform_match(db, organization_id, line, journal_line)
                matched_line_ids.add(line.line_id)
                matched_payment_ids.add(payment.payment_id)
                result.matched += 1
                logger.info(
                    "Auto-matched line %s to GL %s via Splynx payment %s (ref)",
                    line.line_id,
                    journal_line.line_id,
                    payment.splynx_id,
                )
            except Exception as e:
                logger.exception(
                    "Error matching line %s via Splynx payment: %s",
                    line.line_id,
                    e,
                )
                result.errors.append(f"Line {line.line_number}: {e}")

    # ── Pass 3: Date + amount fallback matching ──────────────────

    def _match_by_date_amount(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        payments: list[CustomerPayment],
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        matched_payment_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
    ) -> None:
        """Match remaining lines by date + amount (greedy pairing).

        Groups both payments and statement lines by ``(date, amount_cents)``.
        For each group, pairs as many as possible — ``min(N_payments, N_lines)``
        — because every pair shares the same date and exact amount, so any
        pairing within the group is equally valid.
        """
        from datetime import date

        # Index payments by (date, amount_cents)
        _DateAmountKey = tuple[date, int]
        pmt_index: dict[_DateAmountKey, list[CustomerPayment]] = {}
        for pmt in payments:
            if pmt.payment_id in matched_payment_ids:
                continue
            if not pmt.correlation_id:
                continue
            amount_cents = int(pmt.amount * 100)
            key: _DateAmountKey = (pmt.payment_date, amount_cents)
            pmt_index.setdefault(key, []).append(pmt)

        # Index lines by (date, amount_cents)
        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            amount_cents = int(line.amount * 100)
            key = (line.transaction_date, amount_cents)
            line_index.setdefault(key, []).append(line)

        # Greedy pairing: match min(payments, lines) for each (date, amount)
        for key, pmts in pmt_index.items():
            lines = line_index.get(key, [])
            available_lines = [ln for ln in lines if ln.line_id not in matched_line_ids]
            if not available_lines:
                continue

            pairs = min(len(pmts), len(available_lines))
            for i in range(pairs):
                pmt = pmts[i]
                line = available_lines[i]

                if pmt.payment_id in matched_payment_ids:
                    continue
                if line.line_id in matched_line_ids:
                    continue

                try:
                    journal_line = self._find_journal_line(
                        db,
                        organization_id,
                        pmt.correlation_id,  # type: ignore[arg-type]
                        bank_account.gl_account_id,
                        extra_gl_account_ids=extra_gl_account_ids,
                    )
                    if not journal_line:
                        logger.debug(
                            "No GL journal for Splynx payment %s (date+amount)",
                            pmt.splynx_id,
                        )
                        continue

                    self._perform_match(db, organization_id, line, journal_line)
                    matched_line_ids.add(line.line_id)
                    matched_payment_ids.add(pmt.payment_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via Splynx payment %s "
                        "(date+amount)",
                        line.line_id,
                        journal_line.line_id,
                        pmt.splynx_id,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via date+amount: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

    # ── Pass 4: Bank fee matching ─────────────────────────────────

    def _match_bank_fees(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        result: AutoMatchResult,
    ) -> None:
        """Create GL journals for Paystack fee lines and auto-match them.

        For each unmatched line whose description matches ``Paystack Fee:``:
        1. Creates a balanced journal: Debit Finance Cost (6080),
           Credit bank GL account.
        2. Auto-posts via ``BasePostingAdapter``
           (DRAFT → SUBMITTED → APPROVED → POSTED, with SoD bypass).
        3. Matches the statement line to the credit journal line.
        """
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import JournalInput, JournalLineInput
        from app.services.finance.posting.base import BasePostingAdapter

        # Look up Finance Cost GL account (6080) once
        finance_cost_account = db.scalar(
            select(Account).where(
                Account.organization_id == organization_id,
                Account.account_code == FINANCE_COST_ACCOUNT_CODE,
            )
        )
        if not finance_cost_account:
            logger.warning(
                "Finance Cost account (%s) not found for org %s — skipping fee pass",
                FINANCE_COST_ACCOUNT_CODE,
                organization_id,
            )
            return

        # Filter to fee lines only
        fee_lines = [
            line
            for line in unmatched_lines
            if line.line_id not in matched_line_ids
            and line.description
            and _BANK_FEE_RE.search(line.description)
        ]

        if not fee_lines:
            return

        logger.info(
            "Pass 4: Processing %d Paystack fee lines for statement on bank %s",
            len(fee_lines),
            bank_account.bank_account_id,
        )

        for line in fee_lines:
            try:
                amount = abs(line.amount)
                correlation_id = f"bank-fee-{line.line_id}"

                journal_input = JournalInput(
                    journal_type=JournalType.STANDARD,
                    entry_date=line.transaction_date,
                    posting_date=line.transaction_date,
                    description=f"Bank charge - {line.description}",
                    reference=line.reference,
                    source_module="BANKING",
                    source_document_type="BANK_FEE",
                    correlation_id=correlation_id,
                    lines=[
                        JournalLineInput(
                            account_id=finance_cost_account.account_id,
                            debit_amount=amount,
                            description=line.description,
                        ),
                        JournalLineInput(
                            account_id=bank_account.gl_account_id,
                            credit_amount=amount,
                            description=line.description,
                        ),
                    ],
                )

                # Step 1: Create, submit, approve (with SoD bypass)
                journal, create_error = BasePostingAdapter.create_and_approve_journal(
                    db,
                    organization_id,
                    journal_input,
                    SYSTEM_USER_ID,
                    error_prefix="Fee journal creation failed",
                )

                if create_error:
                    logger.warning(
                        "Failed to create fee journal for line %s: %s",
                        line.line_id,
                        create_error.message,
                    )
                    result.errors.append(
                        f"Line {line.line_number}: {create_error.message}"
                    )
                    continue

                # Step 2: Post to ledger
                idempotency_key = BasePostingAdapter.make_idempotency_key(
                    organization_id, "BANKING", line.line_id, action="bank-fee"
                )
                posting_result = BasePostingAdapter.post_to_ledger(
                    db,
                    organization_id=organization_id,
                    journal_entry_id=journal.journal_entry_id,
                    posting_date=line.transaction_date,
                    idempotency_key=idempotency_key,
                    source_module="BANKING",
                    correlation_id=correlation_id,
                    posted_by_user_id=SYSTEM_USER_ID,
                    success_message="Bank fee posted",
                    error_prefix="Fee journal posting failed",
                )

                if not posting_result.success:
                    logger.warning(
                        "Failed to post fee journal for line %s: %s",
                        line.line_id,
                        posting_result.message,
                    )
                    result.errors.append(
                        f"Line {line.line_number}: {posting_result.message}"
                    )
                    continue

                # Find the credit line on the bank GL account
                journal_line = self._find_journal_line(
                    db,
                    organization_id,
                    correlation_id,
                    bank_account.gl_account_id,
                )
                if not journal_line:
                    logger.warning(
                        "Created fee journal %s but couldn't find bank GL line",
                        journal.journal_entry_id,
                    )
                    continue

                self._perform_match(db, organization_id, line, journal_line)
                matched_line_ids.add(line.line_id)
                result.matched += 1
                logger.info(
                    "Auto-matched fee line %s to GL journal %s",
                    line.line_id,
                    journal.journal_number,
                )

            except Exception as e:
                logger.exception("Error matching fee line %s: %s", line.line_id, e)
                result.errors.append(f"Line {line.line_number}: {e}")

    # ── Pass 5: Settlement matching (cross-bank transfer) ──────────

    def _match_settlements(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        result: AutoMatchResult,
    ) -> None:
        """Match Paystack settlement debits to deposits on receiving banks.

        For each unmatched line whose description matches ``Settlement``:
        1. Searches UBA, Zenith 523, and Zenith 461 for a Paystack-related
           credit within 0–5 days (closest amount wins).
        2. Creates a balanced transfer journal: Debit destination bank GL,
           Credit source (Paystack) bank GL.
        3. Posts via ``BasePostingAdapter`` (with SoD bypass).
        4. Matches **both** the settlement line and the deposit line.

        Duplicate settlement lines (same date + reference + amount) are all
        matched to the same journal.

        **Idempotent**: if a journal already exists for a settlement line
        (from a previous partial run), it is reused instead of creating a
        duplicate.  Individual match failures (e.g. line already matched)
        are caught and logged without aborting the batch.
        """
        from datetime import timedelta

        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import JournalInput, JournalLineInput
        from app.services.finance.posting.base import BasePostingAdapter

        date_window = timedelta(days=SETTLEMENT_DATE_WINDOW_DAYS)

        # Filter to settlement lines only
        settlement_lines = [
            line
            for line in unmatched_lines
            if line.line_id not in matched_line_ids
            and line.description
            and _SETTLEMENT_RE.search(line.description)
        ]

        if not settlement_lines:
            return

        # Deduplicate: group by (date, reference, amount_cents).
        # Import artefacts can produce identical copies.
        _DedupKey = tuple[object, str | None, int]
        dedup_groups: dict[_DedupKey, list[BankStatementLine]] = {}
        unique_settlements: list[BankStatementLine] = []
        for line in settlement_lines:
            key: _DedupKey = (
                line.transaction_date,
                line.reference,
                int(line.amount * 100),
            )
            group = dedup_groups.setdefault(key, [])
            group.append(line)
            if len(group) == 1:
                # First occurrence — representative for this group
                unique_settlements.append(line)

        logger.info(
            "Pass 5: Processing %d unique settlement lines (%d total incl. dupes) "
            "for bank %s",
            len(unique_settlements),
            len(settlement_lines),
            bank_account.bank_account_id,
        )

        # Determine date range for deposit query
        min_date = min(l.transaction_date for l in unique_settlements)
        max_date = max(l.transaction_date for l in unique_settlements) + date_window

        # Load all Paystack-related deposits from other bank accounts
        other_bank_ids = list(
            db.scalars(
                select(BankAccount.bank_account_id).where(
                    BankAccount.organization_id == organization_id,
                    BankAccount.bank_account_id != bank_account.bank_account_id,
                    BankAccount.gl_account_id.isnot(None),
                )
            ).all()
        )

        if not other_bank_ids:
            logger.info("No other bank accounts configured — skipping settlement pass")
            return

        deposit_lines = list(
            db.scalars(
                select(BankStatementLine)
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .where(
                    BankStatement.bank_account_id.in_(other_bank_ids),
                    BankStatementLine.is_matched.is_(False),
                    BankStatementLine.transaction_date.between(min_date, max_date),
                )
            ).all()
        )

        # Filter to Paystack-related deposits only (in Python for flexibility)
        deposit_lines = [
            dep
            for dep in deposit_lines
            if dep.description and _PAYSTACK_DEPOSIT_RE.search(dep.description)
        ]

        if not deposit_lines:
            logger.info("No Paystack-related deposits found on other banks")
            return

        # Pre-load bank account objects for GL lookup
        target_accounts: dict[UUID, BankAccount] = {
            ba.bank_account_id: ba
            for ba in db.scalars(
                select(BankAccount).where(
                    BankAccount.bank_account_id.in_(other_bank_ids)
                )
            ).all()
        }

        # Index deposits by date for fast window lookup
        deposits_by_date: dict[object, list[BankStatementLine]] = {}
        for dep in deposit_lines:
            deposits_by_date.setdefault(dep.transaction_date, []).append(dep)

        matched_deposit_ids: set[UUID] = set()

        for settlement_line in unique_settlements:
            try:
                # Collect deposit candidates within date window
                candidates: list[BankStatementLine] = []
                for day_offset in range(SETTLEMENT_DATE_WINDOW_DAYS + 1):
                    check_date = settlement_line.transaction_date + timedelta(
                        days=day_offset
                    )
                    for dep in deposits_by_date.get(check_date, []):
                        if dep.line_id not in matched_deposit_ids:
                            candidates.append(dep)

                if not candidates:
                    continue

                # Pick best candidate: closest by amount
                best_deposit = min(
                    candidates,
                    key=lambda d: abs(d.amount - settlement_line.amount),
                )

                # Resolve destination bank account
                dep_statement = db.get(BankStatement, best_deposit.statement_id)
                if not dep_statement:
                    continue
                dest_bank = target_accounts.get(dep_statement.bank_account_id)
                if not dest_bank or not dest_bank.gl_account_id:
                    continue

                correlation_id = f"settlement-{settlement_line.line_id}"

                # ── Idempotent journal lookup / creation ─────────────
                # Check if journal already exists (from a previous partial run).
                # This avoids duplicate journals and idempotency key violations.
                credit_jl = self._find_journal_line(
                    db, organization_id, correlation_id, bank_account.gl_account_id
                )
                debit_jl: JournalEntryLine | None = None

                if credit_jl:
                    # Journal already created and posted — reuse it
                    debit_jl = self._find_journal_line(
                        db, organization_id, correlation_id, dest_bank.gl_account_id
                    )
                    logger.info(
                        "Reusing existing journal for settlement %s (re-run)",
                        settlement_line.reference,
                    )
                else:
                    # Create new inter-bank transfer journal
                    amount = abs(settlement_line.amount)

                    journal_input = JournalInput(
                        journal_type=JournalType.STANDARD,
                        entry_date=settlement_line.transaction_date,
                        posting_date=settlement_line.transaction_date,
                        description=(
                            f"Paystack settlement transfer - "
                            f"{settlement_line.reference}"
                        ),
                        reference=settlement_line.reference,
                        source_module="BANKING",
                        source_document_type="BANK_TRANSFER",
                        correlation_id=correlation_id,
                        lines=[
                            JournalLineInput(
                                account_id=dest_bank.gl_account_id,
                                debit_amount=amount,
                                description=(
                                    f"Settlement deposit from Paystack - "
                                    f"{settlement_line.reference}"
                                ),
                            ),
                            JournalLineInput(
                                account_id=bank_account.gl_account_id,
                                credit_amount=amount,
                                description=(
                                    f"Settlement transfer - {settlement_line.reference}"
                                ),
                            ),
                        ],
                    )

                    # Step 1: Create, submit, approve
                    journal, create_error = (
                        BasePostingAdapter.create_and_approve_journal(
                            db,
                            organization_id,
                            journal_input,
                            SYSTEM_USER_ID,
                            error_prefix="Settlement journal creation failed",
                        )
                    )

                    if create_error:
                        logger.warning(
                            "Failed to create settlement journal for line %s: %s",
                            settlement_line.line_id,
                            create_error.message,
                        )
                        result.errors.append(
                            f"Line {settlement_line.line_number}: "
                            f"{create_error.message}"
                        )
                        continue

                    # Step 2: Post to ledger
                    idempotency_key = BasePostingAdapter.make_idempotency_key(
                        organization_id,
                        "BANKING",
                        settlement_line.line_id,
                        action="settlement",
                    )
                    posting_result = BasePostingAdapter.post_to_ledger(
                        db,
                        organization_id=organization_id,
                        journal_entry_id=journal.journal_entry_id,
                        posting_date=settlement_line.transaction_date,
                        idempotency_key=idempotency_key,
                        source_module="BANKING",
                        correlation_id=correlation_id,
                        posted_by_user_id=SYSTEM_USER_ID,
                        success_message="Settlement transfer posted",
                        error_prefix="Settlement journal posting failed",
                    )

                    if not posting_result.success:
                        logger.warning(
                            "Failed to post settlement journal for line %s: %s",
                            settlement_line.line_id,
                            posting_result.message,
                        )
                        result.errors.append(
                            f"Line {settlement_line.line_number}: "
                            f"{posting_result.message}"
                        )
                        continue

                    # Find journal lines for matching
                    credit_jl = self._find_journal_line(
                        db,
                        organization_id,
                        correlation_id,
                        bank_account.gl_account_id,
                    )
                    debit_jl = self._find_journal_line(
                        db,
                        organization_id,
                        correlation_id,
                        dest_bank.gl_account_id,
                    )

                # ── Match settlement line(s) to credit side ──────────
                dedup_key: _DedupKey = (
                    settlement_line.transaction_date,
                    settlement_line.reference,
                    int(settlement_line.amount * 100),
                )
                if credit_jl:
                    for dup_line in dedup_groups.get(dedup_key, [settlement_line]):
                        if dup_line.line_id not in matched_line_ids:
                            try:
                                self._perform_match(
                                    db, organization_id, dup_line, credit_jl
                                )
                                matched_line_ids.add(dup_line.line_id)
                                result.matched += 1
                            except Exception as e:
                                logger.debug(
                                    "Settlement line %s match skipped "
                                    "(already matched): %s",
                                    dup_line.line_id,
                                    e,
                                )

                # ── Match deposit line to debit side ─────────────────
                if debit_jl and best_deposit.line_id not in matched_deposit_ids:
                    try:
                        self._perform_match(db, organization_id, best_deposit, debit_jl)
                        matched_deposit_ids.add(best_deposit.line_id)
                    except Exception as e:
                        logger.debug(
                            "Deposit line %s match skipped (already matched): %s",
                            best_deposit.line_id,
                            e,
                        )

                days_diff = (
                    best_deposit.transaction_date - settlement_line.transaction_date
                ).days
                logger.info(
                    "Auto-matched settlement %s (%.2f) to deposit on %s (%.2f)"
                    " — %d day(s) diff",
                    settlement_line.reference,
                    abs(settlement_line.amount),
                    dest_bank.account_name,
                    best_deposit.amount,
                    days_diff,
                )

            except Exception as e:
                logger.exception(
                    "Error matching settlement line %s: %s",
                    settlement_line.line_id,
                    e,
                )
                result.errors.append(f"Line {settlement_line.line_number}: {e}")

    # ── Shared helpers ──────────────────────────────────────────────

    @staticmethod
    def _find_ref_in_line(
        line: BankStatementLine,
        ref_lookup: Mapping[str, _T],
    ) -> _T | None:
        """Search statement line text fields for a known reference string.

        Checks ``reference``, ``description``, and ``bank_reference`` for a
        substring match against any key in *ref_lookup*.

        Works for both PaymentIntent (keyed by paystack_reference) and
        CustomerPayment (keyed by reference) lookups.
        """
        search_fields: list[str] = []
        if line.reference:
            search_fields.append(line.reference)
        if line.description:
            search_fields.append(line.description)
        if line.bank_reference:
            search_fields.append(line.bank_reference)

        if not search_fields:
            return None

        for ref, entity in ref_lookup.items():
            ref_lower = ref.lower()
            for text_field in search_fields:
                if ref_lower in text_field.lower():
                    return entity

        return None

    @staticmethod
    def _amounts_match(line_amount: Decimal, expected_amount: Decimal) -> bool:
        """Check if two amounts match within AMOUNT_TOLERANCE."""
        return abs(line_amount - expected_amount) <= AMOUNT_TOLERANCE

    @staticmethod
    def _is_paystack_opex_account(bank_account: BankAccount) -> bool:
        """True when account/bank name denotes Paystack OPEX."""
        account_name = (bank_account.account_name or "").strip()
        bank_name = (bank_account.bank_name or "").strip()
        return bool(
            _PAYSTACK_OPEX_RE.search(account_name)
            or _PAYSTACK_OPEX_RE.search(bank_name)
        )

    def _find_journal_line(
        self,
        db: Session,
        organization_id: UUID,
        correlation_id: str,
        gl_account_id: UUID,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
    ) -> JournalEntryLine | None:
        """Find a GL journal entry line by correlation_id and GL account.

        Looks up the journal entry by ``correlation_id`` and then finds the
        line that hits the specified GL account (typically the bank account).

        If *extra_gl_account_ids* is provided, also checks those accounts as
        fallbacks — this handles cases where payments were reassigned to a
        different bank account but the GL journals still reference the old
        account.

        Works for both PaymentIntent (``correlation_id=str(intent_id)``)
        and Splynx payments (``correlation_id="splynx-pmt-{id}"``).
        """
        # unique() is required because joinedload on a collection produces
        # duplicate parent rows in the SQL JOIN result set.
        stmt = (
            select(JournalEntry)
            .options(joinedload(JournalEntry.lines))
            .where(
                JournalEntry.organization_id == organization_id,
                JournalEntry.correlation_id == correlation_id,
                JournalEntry.status == JournalStatus.POSTED,
            )
        )
        journal = db.execute(stmt).unique().scalar_one_or_none()
        if not journal:
            return None

        # Prefer the primary GL account
        for jl in journal.lines:
            if jl.account_id == gl_account_id:
                return jl

        # Fall back to extra GL accounts (e.g. old bank account GL)
        if extra_gl_account_ids:
            for jl in journal.lines:
                if jl.account_id in extra_gl_account_ids:
                    return jl

        return None

    def _perform_match(
        self,
        db: Session,
        organization_id: UUID,
        line: BankStatementLine,
        journal_line: JournalEntryLine,
    ) -> None:
        """Delegate to BankReconciliationService.match_statement_line().

        Exceptions (e.g. HTTPException for already-matched lines) propagate
        to the caller's per-line try/except.
        """
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        recon_svc = BankReconciliationService()
        recon_svc.match_statement_line(
            db=db,
            organization_id=organization_id,
            statement_line_id=line.line_id,
            journal_line_id=journal_line.line_id,
            matched_by=None,  # System-matched, no user
            force_match=True,  # We've already validated amounts
        )
