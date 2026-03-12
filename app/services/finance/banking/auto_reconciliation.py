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
from decimal import Decimal
from typing import TYPE_CHECKING, TypeVar
from uuid import UUID

if TYPE_CHECKING:
    from datetime import date

    from app.services.finance.posting.base import PostingResult

from sqlalchemy import select
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

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

# Tolerance for amount matching (handles rounding in bank CSV imports)
AMOUNT_TOLERANCE = Decimal("0.01")

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
class AutoMatchConfig:
    """Runtime configuration loaded from DomainSettings (banking domain)."""

    pass_payment_intents_enabled: bool = True
    pass_splynx_by_ref_enabled: bool = True
    pass_splynx_date_amount_enabled: bool = True
    pass_ap_payments_enabled: bool = True
    pass_ar_payments_enabled: bool = True
    pass_bank_fees_enabled: bool = True
    pass_settlements_enabled: bool = True
    amount_tolerance: Decimal = Decimal("0.01")
    date_buffer_days: int = 7
    settlement_date_window_days: int = 10
    finance_cost_account_code: str = "6080"


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
    attempts to match them using seven strategies:

    1. **PaymentIntent pass** — for DotMac-initiated Paystack transfers.
       The ``paystack_reference`` is a deterministic join key.
    2. **Splynx payment pass** — for payments collected via Splynx and synced
       as ``CustomerPayment`` records.  Extracts the Paystack transaction ID
       from the payment description using regex, and also tries the Splynx
       receipt number in ``reference``.
    3. **Date + amount fallback** — for remaining unmatched Splynx payments,
       matches when exactly one payment and one statement line share the same
       date and amount on the same bank account.
    4. **AP supplier payment pass** — for CLEARED supplier payments created
       in the app.  Matches by ``payment_number`` / ``reference`` first, then
       by date + amount.  Only matches debit (outgoing) bank lines.
    5. **Non-Splynx AR payment pass** — for CLEARED customer payments created
       directly in the app (``splynx_id IS NULL``).  Matches by reference
       first, then by date + amount.  Only matches credit (incoming) lines.
    6. **Bank fee pass** — for Paystack processing fee lines.  Creates a GL
       journal (debit Finance Cost 6080, credit bank GL) and auto-matches
       the statement line to the new journal entry.
    7. **Settlement pass** — for Paystack settlement transfers.  Finds the
       matching deposit on receiving banks (UBA, Zenith) within 0–5 days,
       creates an inter-bank transfer journal, and matches both sides.

    Strategies 1–5 verify amount (within tolerance) and find the GL journal
    line via ``correlation_id`` before delegating the match to
    ``BankReconciliationService``.  Strategies 6–7 create their own journals.
    """

    logger = logger
    SYSTEM_USER_ID = SYSTEM_USER_ID

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _post_with_period_fallback(
        db: Session,
        *,
        organization_id: UUID,
        journal_entry_id: UUID,
        posting_date: date,
        idempotency_key: str,
        source_module: str,
        correlation_id: str | None,
        posted_by_user_id: UUID,
        success_message: str = "Posted successfully",
        error_prefix: str = "Ledger posting failed",
    ) -> PostingResult:
        """Post to ledger, retrying with today's date on closed-period failure.

        Bank fee and settlement journals may reference transaction dates in
        already-closed fiscal periods.  When the first attempt fails with a
        period-related error we retry using ``date.today()`` which should fall
        in the current open period.
        """
        from datetime import date as _date

        from app.services.finance.posting.base import BasePostingAdapter

        result = BasePostingAdapter.post_to_ledger(
            db,
            organization_id=organization_id,
            journal_entry_id=journal_entry_id,
            posting_date=posting_date,
            idempotency_key=idempotency_key,
            source_module=source_module,
            correlation_id=correlation_id,
            posted_by_user_id=posted_by_user_id,
            success_message=success_message,
            error_prefix=error_prefix,
        )
        if result.success:
            return result

        # Detect closed-period failures and retry with today
        msg_lower = result.message.lower()
        if "period" in msg_lower or "closed" in msg_lower:
            today = _date.today()
            if today != posting_date:
                logger.info(
                    "Posting failed for date %s (period closed); retrying with %s",
                    posting_date,
                    today,
                )
                return BasePostingAdapter.post_to_ledger(
                    db,
                    organization_id=organization_id,
                    journal_entry_id=journal_entry_id,
                    posting_date=today,
                    idempotency_key=idempotency_key,
                    source_module=source_module,
                    correlation_id=correlation_id,
                    posted_by_user_id=posted_by_user_id,
                    success_message=success_message,
                    error_prefix=error_prefix,
                )

        return result

    # ── Configuration ──────────────────────────────────────────────

    @staticmethod
    def _load_config(db: Session, organization_id: UUID) -> AutoMatchConfig:
        """Load auto-match configuration from DomainSettings with fallbacks."""
        from app.models.domain_settings import SettingDomain
        from app.services.settings_spec import resolve_value

        def _bool(key: str, default: bool) -> bool:
            val = resolve_value(db, SettingDomain.banking, key)
            if val is None:
                return default
            if isinstance(val, bool):
                return val
            return str(val).strip().lower() in {"1", "true", "yes", "on"}

        def _int(key: str, default: int) -> int:
            val = resolve_value(db, SettingDomain.banking, key)
            if val is None:
                return default
            try:
                return int(str(val))
            except (TypeError, ValueError):
                return default

        def _str(key: str, default: str) -> str:
            val = resolve_value(db, SettingDomain.banking, key)
            if val is None:
                return default
            return str(val)

        cents = _int("automatch_amount_tolerance_cents", 1)
        tolerance = Decimal(cents) / Decimal(100)

        return AutoMatchConfig(
            pass_payment_intents_enabled=_bool(
                "automatch_pass_payment_intents_enabled", True
            ),
            pass_splynx_by_ref_enabled=_bool(
                "automatch_pass_splynx_by_ref_enabled", True
            ),
            pass_splynx_date_amount_enabled=_bool(
                "automatch_pass_splynx_date_amount_enabled", True
            ),
            pass_ap_payments_enabled=_bool("automatch_pass_ap_payments_enabled", True),
            pass_ar_payments_enabled=_bool("automatch_pass_ar_payments_enabled", True),
            pass_bank_fees_enabled=_bool("automatch_pass_bank_fees_enabled", True),
            pass_settlements_enabled=_bool("automatch_pass_settlements_enabled", True),
            amount_tolerance=tolerance,
            date_buffer_days=_int("automatch_date_buffer_days", 7),
            settlement_date_window_days=_int(
                "automatch_settlement_date_window_days", 10
            ),
            finance_cost_account_code=_str(
                "automatch_finance_cost_account_code", "6080"
            ),
        )

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

        Runs seven passes in sequence:
        1. PaymentIntent (Paystack-initiated transfers)
        2. Splynx CustomerPayment by reference (Paystack ref from description)
        3. Date + amount greedy matching (Splynx fallback)
        4. AP supplier payments (by reference, then date + amount)
        5. Non-Splynx AR customer payments (by reference, then date + amount)
        6. Bank fees (creates GL journals for Paystack fee lines)
        7. Settlements (cross-bank transfer matching, 0–5 day window)

        Lines matched in earlier passes are excluded from later passes.

        Args:
            db: Database session.
            organization_id: Tenant scope.
            statement_id: The statement to process.

        Returns:
            AutoMatchResult with match/skip/error counts.
        """
        result = AutoMatchResult()

        # Load runtime configuration from DomainSettings
        config = self._load_config(db, organization_id)
        policy = reconciliation_policy_service.resolve(
            db,
            organization_id,
            legacy_config=config,
        )

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
        extra_gl = build_extra_gl_account_ids(db, organization_id, bank_account)

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

        # 2b. Ensure system rules are seeded (idempotent, needed for audit trail)
        try:
            from app.services.finance.banking.reconciliation_rule_service import (
                ReconciliationRuleService,
            )

            ReconciliationRuleService.seed_system_rules(db, organization_id)
        except Exception:
            logger.warning("Failed to seed system match rules", exc_info=True)

        matched_line_ids: set[UUID] = set()
        engine_ctx = ReconciliationRunContext(
            db=db,
            organization_id=organization_id,
            statement=statement,
            bank_account=bank_account,
            unmatched_lines=unmatched_lines,
            matched_line_ids=matched_line_ids,
            extra_gl_account_ids=extra_gl,
            config=config,
            policy=policy,
            result=result,
        )
        ProgrammaticReconciliationEngine().run(self, engine_ctx)

        # Recalculate skipped (lines not matched by any pass)
        result.skipped = len(unmatched_lines) - result.matched - len(result.errors)

        # 12. Optional dry-run pass: contra transfer suggestions (no posting)
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
        config: AutoMatchConfig | None = None,
    ) -> None:
        """Match lines against COMPLETED PaymentIntent records."""
        from datetime import timedelta

        buffer_days = config.date_buffer_days if config else 7
        date_buffer = timedelta(days=buffer_days)
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

                tolerance = config.amount_tolerance if config else None
                if not self._amounts_match(
                    line.amount, intent.amount, tolerance=tolerance
                ):
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

                self._perform_match(
                    db,
                    organization_id,
                    line,
                    journal_line,
                    source_type="PAYMENT_INTENT",
                    source_id=intent.intent_id,
                )
                self._log_match(
                    db,
                    organization_id,
                    line=line,
                    source_type="PAYMENT_INTENT",
                    source_id=intent.intent_id,
                    journal_line_id=journal_line.line_id,
                    confidence=100,
                    explanation=f"Paystack reference {intent.paystack_reference} (exact match)",
                )
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
                int(round(abs(intent.amount) * 100)),
            )
            intent_index.setdefault(key, []).append(intent)

        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            if line.transaction_type != StatementLineType.debit:
                continue
            key = (line.transaction_date, int(round(abs(line.amount) * 100)))
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

                    self._perform_match(
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="PAYMENT_INTENT",
                        source_id=intent.intent_id,
                    )
                    self._log_match(
                        db,
                        organization_id,
                        line=line,
                        source_type="PAYMENT_INTENT",
                        source_id=intent.intent_id,
                        journal_line_id=journal_line.line_id,
                        confidence=85,
                        explanation=f"Expense intent {intent.paystack_reference} (date+amount fallback)",
                    )
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
        *,
        config: AutoMatchConfig | None = None,
    ) -> list[CustomerPayment]:
        """Load eligible Splynx payments for the statement's bank account.

        Filters: ``splynx_id IS NOT NULL``, status CLEARED, has GL journal,
        has correlation_id, and matching bank_account_id + date range.
        """
        from datetime import timedelta

        buffer_days = config.date_buffer_days if config else 7
        date_buffer = timedelta(days=buffer_days)
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

    # ── AP / non-Splynx AR payment loaders (passes 4 & 5) ───────

    def _load_ap_payments(
        self,
        db: Session,
        organization_id: UUID,
        statement: BankStatement,
        *,
        config: AutoMatchConfig | None = None,
    ) -> list[SupplierPayment]:
        """Load eligible AP supplier payments for the statement's bank account.

        Filters: status CLEARED, has GL journal, has correlation_id, and
        matching bank_account_id + date range.
        """
        from datetime import timedelta

        buffer_days = config.date_buffer_days if config else 7
        date_buffer = timedelta(days=buffer_days)
        pmt_query = select(SupplierPayment).where(
            SupplierPayment.organization_id == organization_id,
            SupplierPayment.bank_account_id == statement.bank_account_id,
            SupplierPayment.status == APPaymentStatus.CLEARED,
            SupplierPayment.journal_entry_id.isnot(None),
            SupplierPayment.correlation_id.isnot(None),
        )
        if statement.period_start and statement.period_end:
            pmt_query = pmt_query.where(
                SupplierPayment.payment_date >= statement.period_start - date_buffer,
                SupplierPayment.payment_date <= statement.period_end + date_buffer,
            )
        return list(db.scalars(pmt_query).all())

    def _load_non_splynx_ar_payments(
        self,
        db: Session,
        organization_id: UUID,
        statement: BankStatement,
        *,
        config: AutoMatchConfig | None = None,
    ) -> list[CustomerPayment]:
        """Load eligible non-Splynx AR payments for the statement's bank account.

        Filters: ``splynx_id IS NULL``, status CLEARED, has GL journal,
        has correlation_id, and matching bank_account_id + date range.
        This catches AR receipts recorded directly in the app (not via
        Paystack or Splynx).
        """
        from datetime import timedelta

        buffer_days = config.date_buffer_days if config else 7
        date_buffer = timedelta(days=buffer_days)
        pmt_query = select(CustomerPayment).where(
            CustomerPayment.organization_id == organization_id,
            CustomerPayment.bank_account_id == statement.bank_account_id,
            CustomerPayment.splynx_id.is_(None),
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
        config: AutoMatchConfig | None = None,
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

                tolerance = config.amount_tolerance if config else None
                if not self._amounts_match(
                    line.amount, payment.amount, tolerance=tolerance
                ):
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

                self._perform_match(
                    db,
                    organization_id,
                    line,
                    journal_line,
                    source_type="CUSTOMER_PAYMENT",
                    source_id=payment.payment_id,
                )
                self._log_match(
                    db,
                    organization_id,
                    line=line,
                    source_type="CUSTOMER_PAYMENT",
                    source_id=payment.payment_id,
                    journal_line_id=journal_line.line_id,
                    confidence=95,
                    explanation=f"Splynx payment {payment.splynx_id} (reference match)",
                )
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
            amount_cents = int(round(pmt.amount * 100))
            key: _DateAmountKey = (pmt.payment_date, amount_cents)
            pmt_index.setdefault(key, []).append(pmt)

        # Index lines by (date, amount_cents)
        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            amount_cents = int(round(line.amount * 100))
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

                    self._perform_match(
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=pmt.payment_id,
                    )
                    self._log_match(
                        db,
                        organization_id,
                        line=line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=pmt.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=80,
                        explanation=f"Splynx payment {pmt.splynx_id} (date+amount fallback)",
                    )
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

    # ── Pass 4: AP supplier payment matching ──────────────────────

    def _match_ap_payments(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        payments: list[SupplierPayment],
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        matched_payment_ids: set[UUID],
        result: AutoMatchResult,
        *,
        extra_gl_account_ids: set[UUID] | None = None,
        config: AutoMatchConfig | None = None,
    ) -> None:
        """Match debit bank lines against CLEARED AP supplier payments.

        Two-phase matching:
        A. **Reference** — builds lookup from ``payment_number`` and
           ``reference``, searches bank line text fields.
        B. **Date + amount** — greedy pairing fallback for remaining
           unmatched payments.

        Only considers **debit** (outgoing) bank lines, since AP payments
        are money going out.
        """
        from datetime import date

        # Phase A: Reference matching
        ref_to_payment: dict[str, SupplierPayment] = {}
        for pmt in payments:
            if pmt.payment_number:
                ref_to_payment[pmt.payment_number] = pmt
            if pmt.reference and pmt.reference not in ref_to_payment:
                ref_to_payment[pmt.reference] = pmt

        if ref_to_payment:
            debit_lines = [
                line
                for line in unmatched_lines
                if line.line_id not in matched_line_ids
                and line.transaction_type == StatementLineType.debit
            ]
            for line in debit_lines:
                if line.line_id in matched_line_ids:
                    continue
                try:
                    payment = self._find_ref_in_line(line, ref_to_payment)
                    if not payment:
                        continue

                    tolerance = config.amount_tolerance if config else None
                    if not self._amounts_match(
                        line.amount, payment.amount, tolerance=tolerance
                    ):
                        logger.debug(
                            "AP ref in line %s but amount mismatch: "
                            "line=%s, payment=%s",
                            line.line_id,
                            line.amount,
                            payment.amount,
                        )
                        continue

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
                            "No GL journal for AP payment %s (ref: %s)",
                            payment.payment_id,
                            payment.payment_number,
                        )
                        continue

                    self._perform_match(
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="SUPPLIER_PAYMENT",
                        source_id=payment.payment_id,
                    )
                    self._log_match(
                        db,
                        organization_id,
                        line=line,
                        source_type="SUPPLIER_PAYMENT",
                        source_id=payment.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=100,
                        explanation=f"AP payment {payment.payment_number} (reference match)",
                    )
                    matched_line_ids.add(line.line_id)
                    matched_payment_ids.add(payment.payment_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via AP payment %s (ref)",
                        line.line_id,
                        journal_line.line_id,
                        payment.payment_number,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via AP payment ref: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

        # Phase B: Date + amount fallback
        remaining = [
            p
            for p in payments
            if p.payment_id not in matched_payment_ids and p.correlation_id
        ]
        if not remaining:
            return

        _DateAmountKey = tuple[date, int]
        pmt_index: dict[_DateAmountKey, list[SupplierPayment]] = {}
        for pmt in remaining:
            key: _DateAmountKey = (pmt.payment_date, int(round(pmt.amount * 100)))
            pmt_index.setdefault(key, []).append(pmt)

        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            if line.transaction_type != StatementLineType.debit:
                continue
            key = (line.transaction_date, int(round(line.amount * 100)))
            line_index.setdefault(key, []).append(line)

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
                            "No GL journal for AP payment %s (date+amount)",
                            pmt.payment_number,
                        )
                        continue

                    self._perform_match(
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="SUPPLIER_PAYMENT",
                        source_id=pmt.payment_id,
                    )
                    self._log_match(
                        db,
                        organization_id,
                        line=line,
                        source_type="SUPPLIER_PAYMENT",
                        source_id=pmt.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=80,
                        explanation=f"AP payment {pmt.payment_number} (date+amount fallback)",
                    )
                    matched_line_ids.add(line.line_id)
                    matched_payment_ids.add(pmt.payment_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via AP payment %s (date+amount)",
                        line.line_id,
                        journal_line.line_id,
                        pmt.payment_number,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via AP date+amount: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

    # ── Pass 5: Non-Splynx AR customer payment matching ─────────

    def _match_ar_payments(
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
        config: AutoMatchConfig | None = None,
    ) -> None:
        """Match credit bank lines against non-Splynx AR customer payments.

        Two-phase matching:
        A. **Reference** — builds lookup from ``payment_number`` and
           ``reference``, searches bank line text fields.
        B. **Date + amount** — greedy pairing fallback for remaining
           unmatched payments.

        Only considers **credit** (incoming) bank lines, since AR payments
        are money coming in.
        """
        from datetime import date

        # Phase A: Reference matching
        ref_to_payment: dict[str, CustomerPayment] = {}
        for pmt in payments:
            if pmt.payment_number:
                ref_to_payment[pmt.payment_number] = pmt
            if pmt.reference and pmt.reference not in ref_to_payment:
                ref_to_payment[pmt.reference] = pmt

        if ref_to_payment:
            credit_lines = [
                line
                for line in unmatched_lines
                if line.line_id not in matched_line_ids
                and line.transaction_type == StatementLineType.credit
            ]
            for line in credit_lines:
                if line.line_id in matched_line_ids:
                    continue
                try:
                    payment = self._find_ref_in_line(line, ref_to_payment)
                    if not payment:
                        continue

                    tolerance = config.amount_tolerance if config else None
                    if not self._amounts_match(
                        line.amount, payment.amount, tolerance=tolerance
                    ):
                        logger.debug(
                            "AR ref in line %s but amount mismatch: "
                            "line=%s, payment=%s",
                            line.line_id,
                            line.amount,
                            payment.amount,
                        )
                        continue

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
                            "No GL journal for AR payment %s (ref: %s)",
                            payment.payment_id,
                            payment.payment_number,
                        )
                        continue

                    self._perform_match(
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=payment.payment_id,
                    )
                    self._log_match(
                        db,
                        organization_id,
                        line=line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=payment.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=100,
                        explanation=f"AR payment {payment.payment_number} (reference match)",
                    )
                    matched_line_ids.add(line.line_id)
                    matched_payment_ids.add(payment.payment_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via AR payment %s (ref)",
                        line.line_id,
                        journal_line.line_id,
                        payment.payment_number,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via AR payment ref: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

        # Phase B: Date + amount fallback
        remaining = [
            p
            for p in payments
            if p.payment_id not in matched_payment_ids and p.correlation_id
        ]
        if not remaining:
            return

        _DateAmountKey = tuple[date, int]
        pmt_index: dict[_DateAmountKey, list[CustomerPayment]] = {}
        for pmt in remaining:
            key: _DateAmountKey = (pmt.payment_date, int(round(pmt.amount * 100)))
            pmt_index.setdefault(key, []).append(pmt)

        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in unmatched_lines:
            if line.line_id in matched_line_ids:
                continue
            if line.transaction_type != StatementLineType.credit:
                continue
            key = (line.transaction_date, int(round(line.amount * 100)))
            line_index.setdefault(key, []).append(line)

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
                            "No GL journal for AR payment %s (date+amount)",
                            pmt.payment_number,
                        )
                        continue

                    self._perform_match(
                        db,
                        organization_id,
                        line,
                        journal_line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=pmt.payment_id,
                    )
                    self._log_match(
                        db,
                        organization_id,
                        line=line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=pmt.payment_id,
                        journal_line_id=journal_line.line_id,
                        confidence=80,
                        explanation=f"AR payment {pmt.payment_number} (date+amount fallback)",
                    )
                    matched_line_ids.add(line.line_id)
                    matched_payment_ids.add(pmt.payment_id)
                    result.matched += 1
                    logger.info(
                        "Auto-matched line %s to GL %s via AR payment %s (date+amount)",
                        line.line_id,
                        journal_line.line_id,
                        pmt.payment_number,
                    )
                except Exception as e:
                    logger.exception(
                        "Error matching line %s via AR date+amount: %s",
                        line.line_id,
                        e,
                    )
                    result.errors.append(f"Line {line.line_number}: {e}")

    # ── Pass 6: Bank fee matching ─────────────────────────────────

    def _match_bank_fees(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        result: AutoMatchResult,
        *,
        config: AutoMatchConfig | None = None,
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

        # Look up Finance Cost GL account (configurable, default 6080) once
        account_code = (
            config.finance_cost_account_code if config else FINANCE_COST_ACCOUNT_CODE
        )
        finance_cost_account = db.scalar(
            select(Account).where(
                Account.organization_id == organization_id,
                Account.account_code == account_code,
            )
        )
        if not finance_cost_account:
            logger.warning(
                "Finance Cost account (%s) not found for org %s — skipping fee pass",
                account_code,
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
            "Pass 6: Processing %d Paystack fee lines for statement on bank %s",
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
                posting_result = self._post_with_period_fallback(
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

                self._perform_match(
                    db,
                    organization_id,
                    line,
                    journal_line,
                    source_type="BANK_FEE",
                    source_id=None,
                )
                self._log_match(
                    db,
                    organization_id,
                    line=line,
                    source_type="BANK_FEE",
                    source_id=None,
                    journal_line_id=journal_line.line_id,
                    confidence=95,
                    explanation=f"Bank fee: {line.description}",
                )
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

    # ── Pass 7: Settlement matching (cross-bank transfer) ──────────

    def _match_settlements(
        self,
        db: Session,
        organization_id: UUID,
        bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        result: AutoMatchResult,
        *,
        config: AutoMatchConfig | None = None,
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

        window_days = (
            config.settlement_date_window_days
            if config
            else SETTLEMENT_DATE_WINDOW_DAYS
        )
        date_window = timedelta(days=window_days)

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
                int(round(line.amount * 100)),
            )
            group = dedup_groups.setdefault(key, [])
            group.append(line)
            if len(group) == 1:
                # First occurrence — representative for this group
                unique_settlements.append(line)

        logger.info(
            "Pass 7: Processing %d unique settlement lines (%d total incl. dupes) "
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
                    BankStatement.organization_id == organization_id,
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
                    posting_result = self._post_with_period_fallback(
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
                    int(round(settlement_line.amount * 100)),
                )
                if credit_jl:
                    for dup_line in dedup_groups.get(dedup_key, [settlement_line]):
                        if dup_line.line_id not in matched_line_ids:
                            try:
                                self._perform_match(
                                    db,
                                    organization_id,
                                    dup_line,
                                    credit_jl,
                                    source_type="INTER_BANK",
                                    source_id=None,
                                )
                                self._log_match(
                                    db,
                                    organization_id,
                                    line=dup_line,
                                    source_type="INTER_BANK",
                                    source_id=None,
                                    journal_line_id=credit_jl.line_id,
                                    confidence=85,
                                    explanation=f"Settlement transfer: {settlement_line.reference}",
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
                        self._perform_match(
                            db,
                            organization_id,
                            best_deposit,
                            debit_jl,
                            source_type="INTER_BANK",
                            source_id=None,
                        )
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
    def _amounts_match(
        line_amount: Decimal,
        expected_amount: Decimal,
        tolerance: Decimal | None = None,
    ) -> bool:
        """Check if two amounts match within tolerance (default AMOUNT_TOLERANCE)."""
        return abs(line_amount - expected_amount) <= (
            tolerance if tolerance is not None else AMOUNT_TOLERANCE
        )

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
        *,
        source_type: str | None = None,
        source_id: UUID | None = None,
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
            source_type=source_type,
            source_id=source_id,
        )

    @staticmethod
    def _log_match(
        db: Session,
        organization_id: UUID,
        *,
        line: BankStatementLine,
        source_type: str,
        source_id: UUID | None,
        journal_line_id: UUID | None,
        confidence: int,
        explanation: str,
        action: str = "MATCHED",
    ) -> None:
        """Record a match in the reconciliation match log."""
        from app.services.finance.banking.reconciliation_rule_service import (
            ReconciliationRuleService,
        )

        rule_svc = ReconciliationRuleService(db)
        rule_svc.log_match(
            organization_id,
            rule_id=None,  # System passes don't use rule_id
            line_id=line.line_id,
            source_doc_type=source_type,
            source_doc_id=source_id,
            journal_line_id=journal_line_id,
            confidence=confidence,
            explanation=explanation,
            action=action,
        )
