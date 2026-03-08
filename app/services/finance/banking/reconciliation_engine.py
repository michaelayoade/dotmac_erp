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

# Default amount tolerance (1 cent)
_DEFAULT_TOLERANCE = Decimal("0.01")

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


class ReconciliationEngine:
    """Rule-driven reconciliation engine for custom match rules.

    Processes non-system rules against remaining unmatched statement
    lines after the built-in system passes have run.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Public API ──────────────────────────────────────────────────

    def run_custom_rules(
        self,
        organization_id: UUID,
        statement: BankStatement,
        bank_account: BankAccount,
        unmatched_lines: list[BankStatementLine],
        matched_line_ids: set[UUID],
        *,
        amount_tolerance: Decimal = _DEFAULT_TOLERANCE,
        date_buffer_days: int = 7,
        extra_gl_account_ids: set[UUID] | None = None,
    ) -> EngineResult:
        """Process custom rules against unmatched statement lines.

        Called by ``AutoReconciliationService.auto_match_statement()``
        after system passes have completed.

        Args:
            organization_id: Tenant scope.
            statement: The statement being reconciled.
            bank_account: Associated bank account.
            unmatched_lines: All lines from the statement.
            matched_line_ids: Lines already matched by system passes
                (mutated in place as new matches are made).
            amount_tolerance: Amount matching tolerance.
            date_buffer_days: Date window for candidate loading.
            extra_gl_account_ids: Fallback GL accounts.

        Returns:
            EngineResult with match/error counts for custom rules only.
        """
        from app.services.finance.banking.reconciliation_rule_service import (
            ReconciliationRuleService,
        )

        rule_service = ReconciliationRuleService(self.db)
        result = EngineResult()

        # Load only custom (non-system) active rules
        all_rules = rule_service.get_active_rules(organization_id)
        custom_rules = [r for r in all_rules if not r.is_system]

        if not custom_rules:
            return result

        ctx = EngineContext(
            db=self.db,
            organization_id=organization_id,
            statement=statement,
            bank_account=bank_account,
            amount_tolerance=amount_tolerance,
            date_buffer_days=date_buffer_days,
            matched_line_ids=matched_line_ids,
            matched_source_ids=set(),
            extra_gl_account_ids=extra_gl_account_ids,
            result=result,
        )

        logger.info(
            "Running %d custom rules for statement %s",
            len(custom_rules),
            statement.statement_id,
        )

        for rule in custom_rules:
            still_unmatched = [
                line
                for line in unmatched_lines
                if line.line_id not in ctx.matched_line_ids
            ]
            if not still_unmatched:
                break

            # Filter lines by rule conditions
            eligible = [
                line
                for line in still_unmatched
                if rule_service.evaluate_conditions(rule, line)
            ]
            if not eligible:
                continue

            handler = self._get_handler(rule.source_doc_type)
            if not handler:
                logger.warning(
                    "No handler for source_doc_type=%s (rule=%s)",
                    rule.source_doc_type,
                    rule.name,
                )
                continue

            try:
                handler(ctx, rule, eligible, rule_service)
            except Exception as e:
                logger.exception("Error processing custom rule '%s': %s", rule.name, e)
                result.errors.append(f"Rule '{rule.name}': {e}")

        result.skipped = len(
            [l for l in unmatched_lines if l.line_id not in ctx.matched_line_ids]
        )

        logger.info(
            "Custom rules: %d matched, %d skipped, %d errors",
            result.matched,
            result.skipped,
            len(result.errors),
        )
        return result

    # ── Handler dispatch ────────────────────────────────────────────

    def _get_handler(self, source_doc_type: str) -> Any | None:
        """Return the handler function for a source document type."""
        handlers: dict[str, Any] = {
            "CUSTOMER_PAYMENT": self._handle_customer_payment,
            "SUPPLIER_PAYMENT": self._handle_supplier_payment,
            "PAYMENT_INTENT": self._handle_payment_intent,
            "BANK_FEE": self._handle_bank_fee,
            "INTER_BANK": self._handle_inter_bank,
        }
        return handlers.get(source_doc_type)

    # ── CUSTOMER_PAYMENT handler ────────────────────────────────────

    def _handle_customer_payment(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        rule_service: Any,
    ) -> None:
        """Match eligible lines to CustomerPayment records."""

        candidates = self._load_customer_payments(ctx)
        if not candidates:
            return

        # Build reference lookup: {ref_string → payment}
        ref_lookup = self._build_payment_ref_lookup(candidates)

        # Phase 1: Reference matching
        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue

            payment = self._find_ref_in_line(line, ref_lookup)
            if not payment:
                continue
            if payment.payment_id in ctx.matched_source_ids:
                continue

            if not self._amounts_match(
                line.amount, payment.amount, ctx.amount_tolerance
            ):
                continue

            correlation_id = self._get_correlation_id(payment, "CUSTOMER_PAYMENT")
            if not correlation_id:
                continue

            journal_line = self._find_journal_line(
                ctx, correlation_id, ctx.bank_account.gl_account_id
            )
            if not journal_line:
                continue

            self._execute_match(
                ctx,
                rule,
                line,
                journal_line,
                source_type="CUSTOMER_PAYMENT",
                source_id=payment.payment_id,
                confidence=100,
                explanation=(
                    f"Reference match: {payment.reference or payment.payment_number}"
                ),
                rule_service=rule_service,
            )

        # Phase 2: Date + amount fallback (unique matches only)
        self._date_amount_fallback(
            ctx,
            rule,
            eligible_lines,
            candidates,
            source_type="CUSTOMER_PAYMENT",
            get_id=lambda p: p.payment_id,
            get_amount=lambda p: p.amount,
            get_date=lambda p: (
                p.payment_date.date()
                if hasattr(p.payment_date, "date")
                else p.payment_date
            ),
            get_correlation_id=lambda p: self._get_correlation_id(
                p, "CUSTOMER_PAYMENT"
            ),
            rule_service=rule_service,
        )

    def _load_customer_payments(self, ctx: EngineContext) -> list[Any]:
        """Load CLEARED customer payments within statement date range."""
        from app.models.finance.ar.customer_payment import (
            CustomerPayment,
            PaymentStatus,
        )

        buffer = timedelta(days=ctx.date_buffer_days)
        stmt = select(CustomerPayment).where(
            CustomerPayment.organization_id == ctx.organization_id,
            CustomerPayment.status == PaymentStatus.CLEARED,
            CustomerPayment.bank_account_id == ctx.statement.bank_account_id,
        )
        if ctx.statement.period_start and ctx.statement.period_end:
            stmt = stmt.where(
                CustomerPayment.payment_date >= ctx.statement.period_start - buffer,
                CustomerPayment.payment_date
                < ctx.statement.period_end + buffer + timedelta(days=1),
            )
        return list(self.db.scalars(stmt).all())

    # ── SUPPLIER_PAYMENT handler ────────────────────────────────────

    def _handle_supplier_payment(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        rule_service: Any,
    ) -> None:
        """Match eligible lines to SupplierPayment records."""

        candidates = self._load_supplier_payments(ctx)
        if not candidates:
            return

        ref_lookup = self._build_supplier_ref_lookup(candidates)

        # Phase 1: Reference matching
        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue

            payment = self._find_ref_in_line(line, ref_lookup)
            if not payment:
                continue
            if payment.payment_id in ctx.matched_source_ids:
                continue

            if not self._amounts_match(
                line.amount, payment.amount, ctx.amount_tolerance
            ):
                continue

            correlation_id = str(payment.payment_id)
            journal_line = self._find_journal_line(
                ctx, correlation_id, ctx.bank_account.gl_account_id
            )
            if not journal_line:
                continue

            self._execute_match(
                ctx,
                rule,
                line,
                journal_line,
                source_type="SUPPLIER_PAYMENT",
                source_id=payment.payment_id,
                confidence=100,
                explanation=(
                    f"Reference match: {payment.payment_number or payment.reference}"
                ),
                rule_service=rule_service,
            )

        # Phase 2: Date + amount fallback
        self._date_amount_fallback(
            ctx,
            rule,
            eligible_lines,
            candidates,
            source_type="SUPPLIER_PAYMENT",
            get_id=lambda p: p.payment_id,
            get_amount=lambda p: p.amount,
            get_date=lambda p: (
                p.payment_date.date()
                if hasattr(p.payment_date, "date")
                else p.payment_date
            ),
            get_correlation_id=lambda p: str(p.payment_id),
            rule_service=rule_service,
        )

    def _load_supplier_payments(self, ctx: EngineContext) -> list[Any]:
        """Load CLEARED supplier payments within statement date range."""
        from app.models.finance.ap.supplier_payment import (
            APPaymentStatus,
            SupplierPayment,
        )

        buffer = timedelta(days=ctx.date_buffer_days)
        stmt = select(SupplierPayment).where(
            SupplierPayment.organization_id == ctx.organization_id,
            SupplierPayment.status == APPaymentStatus.CLEARED,
            SupplierPayment.bank_account_id == ctx.statement.bank_account_id,
        )
        if ctx.statement.period_start and ctx.statement.period_end:
            stmt = stmt.where(
                SupplierPayment.payment_date >= ctx.statement.period_start - buffer,
                SupplierPayment.payment_date
                < ctx.statement.period_end + buffer + timedelta(days=1),
            )
        return list(self.db.scalars(stmt).all())

    # ── PAYMENT_INTENT handler ──────────────────────────────────────

    def _handle_payment_intent(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        rule_service: Any,
    ) -> None:
        """Match eligible lines to PaymentIntent records."""
        from app.models.finance.payments.payment_intent import (
            PaymentIntent,
            PaymentIntentStatus,
        )

        buffer = timedelta(days=ctx.date_buffer_days)
        stmt = select(PaymentIntent).where(
            PaymentIntent.organization_id == ctx.organization_id,
            PaymentIntent.bank_account_id == ctx.statement.bank_account_id,
            PaymentIntent.status == PaymentIntentStatus.COMPLETED,
        )
        if ctx.statement.period_start and ctx.statement.period_end:
            stmt = stmt.where(
                PaymentIntent.paid_at >= ctx.statement.period_start - buffer,
                PaymentIntent.paid_at
                < ctx.statement.period_end + buffer + timedelta(days=1),
            )
        intents = list(self.db.scalars(stmt).all())
        if not intents:
            return

        # Build ref lookup from paystack_reference (or any gateway ref)
        ref_lookup: dict[str, Any] = {}
        for intent in intents:
            if intent.paystack_reference:
                ref_lookup[intent.paystack_reference] = intent

        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue

            matched_intent = self._find_ref_in_line(line, ref_lookup)
            if not matched_intent:
                continue
            if matched_intent.intent_id in ctx.matched_source_ids:
                continue

            if not self._amounts_match(
                line.amount, matched_intent.amount, ctx.amount_tolerance
            ):
                continue

            journal_line = self._find_journal_line(
                ctx,
                str(matched_intent.intent_id),
                ctx.bank_account.gl_account_id,
            )
            if not journal_line:
                continue

            self._execute_match(
                ctx,
                rule,
                line,
                journal_line,
                source_type="PAYMENT_INTENT",
                source_id=matched_intent.intent_id,
                confidence=100,
                explanation=(
                    f"Gateway reference {matched_intent.paystack_reference} (exact match)"
                ),
                rule_service=rule_service,
            )

    # ── BANK_FEE handler ────────────────────────────────────────────

    def _handle_bank_fee(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        rule_service: Any,
    ) -> None:
        """Create GL journals for bank fee lines and auto-match."""
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import (
            JournalInput,
            JournalLineInput,
        )
        from app.services.finance.posting.base import BasePostingAdapter

        # Determine writeoff account from rule or default
        if rule.writeoff_account_id:
            finance_cost_account = self.db.get(Account, rule.writeoff_account_id)
        else:
            finance_cost_account = self.db.scalar(
                select(Account).where(
                    Account.organization_id == ctx.organization_id,
                    Account.account_code == "6080",
                )
            )

        if not finance_cost_account:
            logger.warning(
                "No writeoff account for BANK_FEE rule '%s' — skipping",
                rule.name,
            )
            return

        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue

            try:
                amount = abs(line.amount)
                correlation_id = f"bank-fee-{line.line_id}"

                # Build journal label from template or default
                label = self._render_label(
                    rule.journal_label_template,
                    line,
                    default=f"Bank charge - {line.description}",
                )

                journal_input = JournalInput(
                    journal_type=JournalType.STANDARD,
                    entry_date=line.transaction_date,
                    posting_date=line.transaction_date,
                    description=label,
                    reference=line.reference,
                    source_module="BANKING",
                    source_document_type="BANK_FEE",
                    correlation_id=correlation_id,
                    lines=[
                        JournalLineInput(
                            account_id=finance_cost_account.account_id,
                            debit_amount=amount,
                            description=label,
                        ),
                        JournalLineInput(
                            account_id=ctx.bank_account.gl_account_id,
                            credit_amount=amount,
                            description=label,
                        ),
                    ],
                )

                journal, error = BasePostingAdapter.create_and_approve_journal(
                    self.db,
                    ctx.organization_id,
                    journal_input,
                    _SYSTEM_USER_ID,
                    error_prefix="Fee journal creation failed",
                )
                if error:
                    ctx.result.errors.append(
                        f"Line {line.line_number}: {error.message}"
                    )
                    continue

                idempotency_key = BasePostingAdapter.make_idempotency_key(
                    ctx.organization_id,
                    "BANKING",
                    line.line_id,
                    action="bank-fee",
                )
                posting = BasePostingAdapter.post_to_ledger(
                    self.db,
                    organization_id=ctx.organization_id,
                    journal_entry_id=journal.journal_entry_id,
                    posting_date=line.transaction_date,
                    idempotency_key=idempotency_key,
                    source_module="BANKING",
                    correlation_id=correlation_id,
                    posted_by_user_id=_SYSTEM_USER_ID,
                    success_message="Bank fee posted",
                    error_prefix="Fee journal posting failed",
                )
                if not posting.success:
                    ctx.result.errors.append(
                        f"Line {line.line_number}: {posting.message}"
                    )
                    continue

                journal_line = self._find_journal_line(
                    ctx,
                    correlation_id,
                    ctx.bank_account.gl_account_id,
                )
                if not journal_line:
                    continue

                self._execute_match(
                    ctx,
                    rule,
                    line,
                    journal_line,
                    source_type="BANK_FEE",
                    source_id=None,
                    confidence=95,
                    explanation=f"Bank fee: {line.description}",
                    rule_service=rule_service,
                )

            except Exception as e:
                logger.exception(
                    "Error in BANK_FEE handler for line %s: %s",
                    line.line_id,
                    e,
                )
                ctx.result.errors.append(f"Line {line.line_number}: {e}")

    # ── INTER_BANK handler ──────────────────────────────────────────

    def _handle_inter_bank(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        rule_service: Any,
    ) -> None:
        """Match settlement/transfer lines across bank accounts."""
        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import (
            JournalInput,
            JournalLineInput,
        )
        from app.services.finance.posting.base import BasePostingAdapter

        window_days = rule.date_window_days or 10

        # Load other bank accounts
        other_banks = list(
            self.db.scalars(
                select(BankAccount).where(
                    BankAccount.organization_id == ctx.organization_id,
                    BankAccount.bank_account_id != ctx.bank_account.bank_account_id,
                    BankAccount.gl_account_id.isnot(None),
                )
            ).all()
        )
        if not other_banks:
            return

        other_bank_ids = [b.bank_account_id for b in other_banks]
        bank_by_id = {b.bank_account_id: b for b in other_banks}

        # Load unmatched lines on other bank accounts in date range
        dates = [l.transaction_date for l in eligible_lines]
        if not dates:
            return
        min_date = min(dates)
        max_date = max(dates) + timedelta(days=window_days)

        deposit_lines = list(
            self.db.scalars(
                select(BankStatementLine)
                .join(
                    BankStatement,
                    BankStatementLine.statement_id == BankStatement.statement_id,
                )
                .where(
                    BankStatement.organization_id == ctx.organization_id,
                    BankStatement.bank_account_id.in_(other_bank_ids),
                    BankStatementLine.is_matched.is_(False),
                    BankStatementLine.transaction_date.between(min_date, max_date),
                )
            ).all()
        )
        if not deposit_lines:
            return

        matched_deposit_ids: set[UUID] = set()

        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue

            try:
                # Find best deposit within date window
                candidates = [
                    dep
                    for dep in deposit_lines
                    if dep.line_id not in matched_deposit_ids
                    and 0
                    <= (dep.transaction_date - line.transaction_date).days
                    <= window_days
                ]
                if not candidates:
                    continue

                best = min(
                    candidates,
                    key=lambda d: abs(d.amount - line.amount),
                )

                # Resolve destination bank account
                dep_stmt = self.db.get(BankStatement, best.statement_id)
                if not dep_stmt:
                    continue
                dest_bank = bank_by_id.get(dep_stmt.bank_account_id)
                if not dest_bank or not dest_bank.gl_account_id:
                    continue

                correlation_id = f"interbank-{line.line_id}"
                amount = abs(line.amount)

                # Check for existing journal (idempotent)
                credit_jl = self._find_journal_line(
                    ctx,
                    correlation_id,
                    ctx.bank_account.gl_account_id,
                )
                debit_jl: JournalEntryLine | None = None

                if credit_jl:
                    debit_jl = self._find_journal_line(
                        ctx, correlation_id, dest_bank.gl_account_id
                    )
                else:
                    label = self._render_label(
                        rule.journal_label_template,
                        line,
                        default=(f"Inter-bank transfer - {line.reference}"),
                    )
                    journal_input = JournalInput(
                        journal_type=JournalType.STANDARD,
                        entry_date=line.transaction_date,
                        posting_date=line.transaction_date,
                        description=label,
                        reference=line.reference,
                        source_module="BANKING",
                        source_document_type="BANK_TRANSFER",
                        correlation_id=correlation_id,
                        lines=[
                            JournalLineInput(
                                account_id=dest_bank.gl_account_id,
                                debit_amount=amount,
                                description=f"Deposit from transfer - {line.reference}",
                            ),
                            JournalLineInput(
                                account_id=ctx.bank_account.gl_account_id,
                                credit_amount=amount,
                                description=f"Transfer out - {line.reference}",
                            ),
                        ],
                    )

                    journal, error = BasePostingAdapter.create_and_approve_journal(
                        self.db,
                        ctx.organization_id,
                        journal_input,
                        _SYSTEM_USER_ID,
                        error_prefix=("Inter-bank journal creation failed"),
                    )
                    if error:
                        ctx.result.errors.append(
                            f"Line {line.line_number}: {error.message}"
                        )
                        continue

                    idempotency_key = BasePostingAdapter.make_idempotency_key(
                        ctx.organization_id,
                        "BANKING",
                        line.line_id,
                        action="interbank",
                    )
                    posting = BasePostingAdapter.post_to_ledger(
                        self.db,
                        organization_id=ctx.organization_id,
                        journal_entry_id=journal.journal_entry_id,
                        posting_date=line.transaction_date,
                        idempotency_key=idempotency_key,
                        source_module="BANKING",
                        correlation_id=correlation_id,
                        posted_by_user_id=_SYSTEM_USER_ID,
                        success_message="Inter-bank transfer posted",
                        error_prefix="Inter-bank posting failed",
                    )
                    if not posting.success:
                        ctx.result.errors.append(
                            f"Line {line.line_number}: {posting.message}"
                        )
                        continue

                    credit_jl = self._find_journal_line(
                        ctx,
                        correlation_id,
                        ctx.bank_account.gl_account_id,
                    )
                    debit_jl = self._find_journal_line(
                        ctx, correlation_id, dest_bank.gl_account_id
                    )

                # Match source line
                if credit_jl:
                    self._execute_match(
                        ctx,
                        rule,
                        line,
                        credit_jl,
                        source_type="INTER_BANK",
                        source_id=None,
                        confidence=85,
                        explanation=(
                            f"Inter-bank transfer to {dest_bank.account_name}"
                        ),
                        rule_service=rule_service,
                    )

                # Match deposit line
                if debit_jl and best.line_id not in matched_deposit_ids:
                    self._perform_match_action(ctx, best, debit_jl, "INTER_BANK", None)
                    matched_deposit_ids.add(best.line_id)

            except Exception as e:
                logger.exception(
                    "Error in INTER_BANK handler for line %s: %s",
                    line.line_id,
                    e,
                )
                ctx.result.errors.append(f"Line {line.line_number}: {e}")

    # ── Generic helpers ─────────────────────────────────────────────

    @staticmethod
    def _build_payment_ref_lookup(
        payments: list[Any],
    ) -> dict[str, Any]:
        """Build reference lookup from CustomerPayment records.

        Extracts references from multiple fields:
        - payment.reference (receipt number)
        - payment.payment_number
        - Hex IDs from payment.description (Paystack transaction IDs)
        """
        lookup: dict[str, Any] = {}
        for p in payments:
            if getattr(p, "reference", None):
                lookup[p.reference] = p
            if getattr(p, "payment_number", None):
                lookup[p.payment_number] = p
            # Extract hex IDs from description (gateway transaction refs)
            desc = getattr(p, "description", None)
            if desc:
                for match in _HEX_REF_RE.finditer(desc):
                    lookup[match.group()] = p
        return lookup

    @staticmethod
    def _build_supplier_ref_lookup(
        payments: list[Any],
    ) -> dict[str, Any]:
        """Build reference lookup from SupplierPayment records."""
        lookup: dict[str, Any] = {}
        for p in payments:
            if getattr(p, "payment_number", None):
                lookup[p.payment_number] = p
            if getattr(p, "reference", None):
                lookup[p.reference] = p
        return lookup

    @staticmethod
    def _find_ref_in_line(
        line: BankStatementLine,
        ref_lookup: dict[str, Any],
    ) -> Any | None:
        """Search statement line text fields for a known reference.

        Checks reference, description, and bank_reference for a
        case-insensitive substring match against lookup keys.
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
            for text in search_fields:
                if ref_lower in text.lower():
                    return entity

        return None

    @staticmethod
    def _amounts_match(
        line_amount: Decimal,
        expected: Decimal,
        tolerance: Decimal,
    ) -> bool:
        """Check if two amounts match within tolerance."""
        return abs(line_amount - expected) <= tolerance

    @staticmethod
    def _get_correlation_id(source_doc: Any, source_type: str) -> str | None:
        """Derive the GL correlation_id from a source document.

        Each source document type has a known correlation_id pattern
        used when posting its GL journal entry.
        """
        if source_type == "PAYMENT_INTENT":
            return str(source_doc.intent_id)
        if source_type == "CUSTOMER_PAYMENT":
            # Splynx payments use a special prefix
            if getattr(source_doc, "splynx_id", None):
                return f"splynx-pmt-{source_doc.splynx_id}"
            return str(source_doc.payment_id)
        if source_type == "SUPPLIER_PAYMENT":
            return str(source_doc.payment_id)
        return None

    def _find_journal_line(
        self,
        ctx: EngineContext,
        correlation_id: str,
        gl_account_id: UUID,
    ) -> JournalEntryLine | None:
        """Find GL journal entry line by correlation_id + account."""
        stmt = (
            select(JournalEntry)
            .options(joinedload(JournalEntry.lines))
            .where(
                JournalEntry.organization_id == ctx.organization_id,
                JournalEntry.correlation_id == correlation_id,
                JournalEntry.status == JournalStatus.POSTED,
            )
        )
        journal = self.db.execute(stmt).unique().scalar_one_or_none()
        if not journal:
            return None

        # Prefer primary GL account
        for jl in journal.lines:
            if jl.account_id == gl_account_id:
                return jl

        # Fall back to extra GL accounts
        if ctx.extra_gl_account_ids:
            for jl in journal.lines:
                if jl.account_id in ctx.extra_gl_account_ids:
                    return jl

        return None

    def _date_amount_fallback(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        eligible_lines: list[BankStatementLine],
        candidates: list[Any],
        *,
        source_type: str,
        get_id: Any,
        get_amount: Any,
        get_date: Any,
        get_correlation_id: Any,
        rule_service: Any,
    ) -> None:
        """Fallback matching by date + amount (unique pairs only).

        Only matches when exactly one candidate and one line share
        the same date and amount. Prevents false positives.
        """
        _DateAmountKey = tuple[date, int]

        # Index candidates by (date, amount_cents)
        candidate_index: dict[_DateAmountKey, list[Any]] = {}
        for c in candidates:
            cid = get_id(c)
            if cid in ctx.matched_source_ids:
                continue
            c_date = get_date(c)
            if c_date is None:
                continue
            key: _DateAmountKey = (
                c_date,
                int(round(abs(get_amount(c)) * 100)),
            )
            candidate_index.setdefault(key, []).append(c)

        # Index eligible lines by (date, amount_cents)
        line_index: dict[_DateAmountKey, list[BankStatementLine]] = {}
        for line in eligible_lines:
            if line.line_id in ctx.matched_line_ids:
                continue
            key = (
                line.transaction_date,
                int(round(abs(line.amount) * 100)),
            )
            line_index.setdefault(key, []).append(line)

        # Match unique pairs only
        for key, key_candidates in candidate_index.items():
            key_lines = line_index.get(key, [])
            available_lines = [
                ln for ln in key_lines if ln.line_id not in ctx.matched_line_ids
            ]
            available_candidates = [
                c for c in key_candidates if get_id(c) not in ctx.matched_source_ids
            ]

            if len(available_lines) != 1 or len(available_candidates) != 1:
                continue

            line = available_lines[0]
            candidate = available_candidates[0]

            corr_id = get_correlation_id(candidate)
            if not corr_id:
                continue

            journal_line = self._find_journal_line(
                ctx, corr_id, ctx.bank_account.gl_account_id
            )
            if not journal_line:
                continue

            self._execute_match(
                ctx,
                rule,
                line,
                journal_line,
                source_type=source_type,
                source_id=get_id(candidate),
                confidence=80,
                explanation=(
                    f"Date+amount fallback: {line.transaction_date} / {line.amount}"
                ),
                rule_service=rule_service,
            )

    def _execute_match(
        self,
        ctx: EngineContext,
        rule: ReconciliationMatchRule,
        line: BankStatementLine,
        journal_line: JournalEntryLine,
        *,
        source_type: str,
        source_id: UUID | None,
        confidence: int,
        explanation: str,
        rule_service: Any,
    ) -> None:
        """Perform a match and log it."""
        action = "MATCHED"
        if confidence < rule.min_confidence:
            action = "SUGGESTED"
            # Don't actually match — just log the suggestion
            rule_service.log_match(
                ctx.organization_id,
                rule_id=rule.rule_id,
                line_id=line.line_id,
                source_doc_type=source_type,
                source_doc_id=source_id,
                journal_line_id=journal_line.line_id,
                confidence=confidence,
                explanation=explanation,
                action="SUGGESTED",
            )
            return

        # Perform the actual match
        self._perform_match_action(ctx, line, journal_line, source_type, source_id)

        # Log the match
        rule_service.log_match(
            ctx.organization_id,
            rule_id=rule.rule_id,
            line_id=line.line_id,
            source_doc_type=source_type,
            source_doc_id=source_id,
            journal_line_id=journal_line.line_id,
            confidence=confidence,
            explanation=explanation,
            action=action,
        )

        logger.info(
            "Engine matched line %s via rule '%s' (%s, conf=%d)",
            line.line_id,
            rule.name,
            source_type,
            confidence,
        )

    def _perform_match_action(
        self,
        ctx: EngineContext,
        line: BankStatementLine,
        journal_line: JournalEntryLine,
        source_type: str,
        source_id: UUID | None,
    ) -> None:
        """Delegate match to BankReconciliationService."""
        from app.services.finance.banking.bank_reconciliation import (
            BankReconciliationService,
        )

        recon_svc = BankReconciliationService()
        recon_svc.match_statement_line(
            db=ctx.db,
            organization_id=ctx.organization_id,
            statement_line_id=line.line_id,
            journal_line_id=journal_line.line_id,
            matched_by=None,
            force_match=True,
            source_type=source_type,
            source_id=source_id,
        )
        ctx.matched_line_ids.add(line.line_id)
        if source_id:
            ctx.matched_source_ids.add(source_id)
        ctx.result.matched += 1

    @staticmethod
    def _render_label(
        template: str | None,
        line: BankStatementLine,
        *,
        default: str,
    ) -> str:
        """Render a journal label template with line data."""
        if not template:
            return default
        try:
            return template.format(
                date=line.transaction_date,
                description=line.description or "",
                reference=line.reference or "",
                amount=line.amount,
            )
        except (KeyError, IndexError):
            return default
