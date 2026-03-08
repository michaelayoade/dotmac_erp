from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select

from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    StatementLineType,
)
from app.models.finance.payments.payment_intent import (
    PaymentIntent,
    PaymentIntentStatus,
)
from app.services.finance.banking.reconciliation_runtime import (
    CandidateProvider,
    MatchStrategy,
    ReconciliationRunContext,
    extract_line_signals,
    normalize_statement_line,
)


def _find_entity_for_line(
    ctx: ReconciliationRunContext,
    line: BankStatementLine,
    ref_lookup: dict[str, Any],
) -> Any | None:
    normalized = ctx.normalized_lines.get(line.line_id)
    if not normalized:
        return None
    searchable_text = normalized.searchable_text.lower()
    for ref, entity in ref_lookup.items():
        if ref.lower() in searchable_text:
            return entity
    return None


def _payment_intent_ref_lookup(
    intents: list[PaymentIntent],
) -> dict[str, PaymentIntent]:
    return {
        intent.paystack_reference: intent
        for intent in intents
        if getattr(intent, "paystack_reference", None)
    }


def _splynx_ref_lookup(service: Any, payments: list[Any]) -> dict[str, Any]:
    ref_to_payment: dict[str, Any] = {}
    for payment in payments:
        paystack_ref = service._extract_paystack_ref(payment.description)
        if paystack_ref:
            ref_to_payment[paystack_ref] = payment
    for payment in payments:
        if payment.reference and payment.reference not in ref_to_payment:
            ref_to_payment[payment.reference] = payment
    return ref_to_payment


def _perform_match(
    service: Any,
    ctx: ReconciliationRunContext,
    line: BankStatementLine,
    journal_line: Any,
    *,
    source_type: str,
    source_id: UUID | None,
    confidence: int,
    explanation: str,
) -> None:
    service._perform_match(
        ctx.db,
        ctx.organization_id,
        line,
        journal_line,
        source_type=source_type,
        source_id=source_id,
    )
    service._log_match(
        ctx.db,
        ctx.organization_id,
        line=line,
        source_type=source_type,
        source_id=source_id,
        journal_line_id=journal_line.line_id,
        confidence=confidence,
        explanation=explanation,
    )
    ctx.matched_line_ids.add(line.line_id)
    ctx.result.matched += 1


def _reference_payment_lookup(payments: list[Any]) -> dict[str, Any]:
    ref_to_payment: dict[str, Any] = {}
    for payment in payments:
        if getattr(payment, "payment_number", None):
            ref_to_payment[payment.payment_number] = payment
        if (
            getattr(payment, "reference", None)
            and payment.reference not in ref_to_payment
        ):
            ref_to_payment[payment.reference] = payment
    return ref_to_payment


def _run_directional_reference_match(
    service: Any,
    ctx: ReconciliationRunContext,
    *,
    payments: list[Any],
    matched_payment_ids: set[UUID],
    line_type: StatementLineType,
    source_type: str,
    explanation_prefix: str,
) -> None:
    ref_to_payment = _reference_payment_lookup(payments)
    if not ref_to_payment:
        return

    for line in ctx.still_unmatched_lines():
        if line.transaction_type != line_type:
            continue
        try:
            payment = _find_entity_for_line(ctx, line, ref_to_payment)
            if not payment or payment.payment_id in matched_payment_ids:
                continue

            tolerance = ctx.config.amount_tolerance if ctx.config else None
            if not service._amounts_match(
                line.amount, payment.amount, tolerance=tolerance
            ):
                continue
            if not payment.correlation_id:
                continue

            journal_line = service._find_journal_line(
                ctx.db,
                ctx.organization_id,
                payment.correlation_id,
                ctx.bank_account.gl_account_id,
                extra_gl_account_ids=ctx.extra_gl_account_ids,
            )
            if not journal_line:
                continue

            _perform_match(
                service,
                ctx,
                line,
                journal_line,
                source_type=source_type,
                source_id=payment.payment_id,
                confidence=100,
                explanation=f"{explanation_prefix} {payment.payment_number} (reference match)",
            )
            matched_payment_ids.add(payment.payment_id)
        except Exception as exc:
            service.logger.exception(
                "Error matching line %s via %s ref: %s",
                line.line_id,
                explanation_prefix,
                exc,
            )
            ctx.result.errors.append(f"Line {line.line_number}: {exc}")


def _run_directional_date_amount_match(
    service: Any,
    ctx: ReconciliationRunContext,
    *,
    payments: list[Any],
    matched_payment_ids: set[UUID],
    line_type: StatementLineType,
    source_type: str,
    explanation_prefix: str,
) -> None:
    payment_index: dict[tuple[object, int], list[Any]] = {}
    for payment in payments:
        if payment.payment_id in matched_payment_ids or not payment.correlation_id:
            continue
        key = (payment.payment_date, int(Decimal(payment.amount) * 100))
        payment_index.setdefault(key, []).append(payment)

    line_index: dict[tuple[object, int], list[BankStatementLine]] = {}
    for line in ctx.still_unmatched_lines():
        if line.transaction_type != line_type:
            continue
        key = (line.transaction_date, int(Decimal(line.amount) * 100))
        line_index.setdefault(key, []).append(line)

    for key, indexed_payments in payment_index.items():
        available_lines = [
            line
            for line in line_index.get(key, [])
            if line.line_id not in ctx.matched_line_ids
        ]
        if not available_lines:
            continue

        pairs = min(len(indexed_payments), len(available_lines))
        for idx in range(pairs):
            payment = indexed_payments[idx]
            line = available_lines[idx]
            if payment.payment_id in matched_payment_ids:
                continue
            try:
                journal_line = service._find_journal_line(
                    ctx.db,
                    ctx.organization_id,
                    payment.correlation_id,
                    ctx.bank_account.gl_account_id,
                    extra_gl_account_ids=ctx.extra_gl_account_ids,
                )
                if not journal_line:
                    continue

                _perform_match(
                    service,
                    ctx,
                    line,
                    journal_line,
                    source_type=source_type,
                    source_id=payment.payment_id,
                    confidence=80,
                    explanation=f"{explanation_prefix} {payment.payment_number} (date+amount fallback)",
                )
                matched_payment_ids.add(payment.payment_id)
            except Exception as exc:
                service.logger.exception(
                    "Error matching line %s via %s date+amount: %s",
                    line.line_id,
                    explanation_prefix,
                    exc,
                )
                ctx.result.errors.append(f"Line {line.line_number}: {exc}")


@dataclass(frozen=True)
class PaymentIntentProvider(CandidateProvider):
    provider_key: str = "gateway_payment_intent"
    source_type: str = "payment_intent"

    def load(self, service: Any, ctx: ReconciliationRunContext) -> list[PaymentIntent]:
        cached = ctx.provider_cache.get(self.provider_key)
        if cached is not None:
            return cached

        buffer_days = ctx.config.date_buffer_days if ctx.config else 7
        date_buffer = timedelta(days=buffer_days)
        stmt = select(PaymentIntent).where(
            PaymentIntent.organization_id == ctx.organization_id,
            PaymentIntent.bank_account_id == ctx.statement.bank_account_id,
            PaymentIntent.status == PaymentIntentStatus.COMPLETED,
        )
        if ctx.statement.period_start and ctx.statement.period_end:
            stmt = stmt.where(
                PaymentIntent.paid_at >= ctx.statement.period_start - date_buffer,
                PaymentIntent.paid_at
                < ctx.statement.period_end + date_buffer + timedelta(days=1),
            )
        loaded = list(ctx.db.scalars(stmt).all())
        ctx.provider_cache[self.provider_key] = loaded
        return loaded


@dataclass(frozen=True)
class SplynxCustomerPaymentProvider(CandidateProvider):
    provider_key: str = "receivable_payment_synced"
    source_type: str = "customer_payment"

    def load(self, service: Any, ctx: ReconciliationRunContext) -> list[Any]:
        cached = ctx.provider_cache.get(self.provider_key)
        if cached is not None:
            return cached
        loaded = service._load_splynx_payments(
            ctx.db,
            ctx.organization_id,
            ctx.statement,
            config=ctx.config,
        )
        ctx.provider_cache[self.provider_key] = loaded
        return cast(list[Any], loaded)


@dataclass(frozen=True)
class SupplierPaymentProvider(CandidateProvider):
    provider_key: str = "payable_payment"
    source_type: str = "supplier_payment"

    def load(self, service: Any, ctx: ReconciliationRunContext) -> list[Any]:
        cached = ctx.provider_cache.get(self.provider_key)
        if cached is not None:
            return cached
        loaded = service._load_ap_payments(
            ctx.db,
            ctx.organization_id,
            ctx.statement,
            config=ctx.config,
        )
        ctx.provider_cache[self.provider_key] = loaded
        return cast(list[Any], loaded)


@dataclass(frozen=True)
class CustomerReceiptProvider(CandidateProvider):
    provider_key: str = "receivable_payment"
    source_type: str = "customer_payment"

    def load(self, service: Any, ctx: ReconciliationRunContext) -> list[Any]:
        cached = ctx.provider_cache.get(self.provider_key)
        if cached is not None:
            return cached
        loaded = service._load_non_splynx_ar_payments(
            ctx.db,
            ctx.organization_id,
            ctx.statement,
            config=ctx.config,
        )
        ctx.provider_cache[self.provider_key] = loaded
        return cast(list[Any], loaded)


@dataclass(frozen=True)
class PaymentIntentReferenceStrategy(MatchStrategy):
    strategy_id: str = "exact_external_reference"
    provider: PaymentIntentProvider = PaymentIntentProvider()
    source_type: str = "payment_intent"

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if (
            not ctx.policy.allows_strategy(self.strategy_id)
            or not ctx.policy.allows_source_type(self.source_type)
            or not ctx.policy.allows_provider(self.provider.provider_key)
        ):
            return
        intents = self.provider.load(service, ctx)
        if not intents:
            return

        ref_to_intent = _payment_intent_ref_lookup(intents)
        matched_intent_ids = ctx.tracker(self.provider.provider_key)

        for line in ctx.still_unmatched_lines():
            try:
                intent = _find_entity_for_line(ctx, line, ref_to_intent)
                if not intent or intent.intent_id in matched_intent_ids:
                    continue

                tolerance = ctx.config.amount_tolerance if ctx.config else None
                if not service._amounts_match(
                    line.amount, intent.amount, tolerance=tolerance
                ):
                    continue

                journal_line = service._find_journal_line(
                    ctx.db,
                    ctx.organization_id,
                    str(intent.intent_id),
                    ctx.bank_account.gl_account_id,
                    extra_gl_account_ids=ctx.extra_gl_account_ids,
                )
                if not journal_line:
                    continue

                _perform_match(
                    service,
                    ctx,
                    line,
                    journal_line,
                    source_type="PAYMENT_INTENT",
                    source_id=intent.intent_id,
                    confidence=100,
                    explanation=f"Paystack reference {intent.paystack_reference} (exact match)",
                )
                matched_intent_ids.add(intent.intent_id)
            except Exception as exc:
                service.logger.exception(
                    "Error matching line %s via PaymentIntent: %s",
                    line.line_id,
                    exc,
                )
                ctx.result.errors.append(f"Line {line.line_number}: {exc}")

        service._match_expense_intents_by_date_amount(
            ctx.db,
            ctx.organization_id,
            ctx.bank_account,
            intents,
            ctx.unmatched_lines,
            ctx.matched_line_ids,
            matched_intent_ids,
            ctx.result,
            extra_gl_account_ids=ctx.extra_gl_account_ids,
        )


@dataclass(frozen=True)
class CustomerPaymentReferenceStrategy(MatchStrategy):
    strategy_id: str = "exact_synced_receivable_reference"
    provider: SplynxCustomerPaymentProvider = SplynxCustomerPaymentProvider()

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if (
            not ctx.policy.allows_strategy(self.strategy_id)
            or not ctx.policy.allows_source_type(self.provider.source_type)
            or not ctx.policy.allows_provider(self.provider.provider_key)
        ):
            return
        payments = self.provider.load(service, ctx)
        if not payments:
            return
        ref_to_payment = _splynx_ref_lookup(service, payments)
        matched_payment_ids = ctx.tracker(self.provider.provider_key)

        for line in ctx.still_unmatched_lines():
            try:
                payment = _find_entity_for_line(ctx, line, ref_to_payment)
                if not payment or payment.payment_id in matched_payment_ids:
                    continue

                tolerance = ctx.config.amount_tolerance if ctx.config else None
                if not service._amounts_match(
                    line.amount, payment.amount, tolerance=tolerance
                ):
                    continue
                if not payment.correlation_id:
                    continue

                journal_line = service._find_journal_line(
                    ctx.db,
                    ctx.organization_id,
                    payment.correlation_id,
                    ctx.bank_account.gl_account_id,
                    extra_gl_account_ids=ctx.extra_gl_account_ids,
                )
                if not journal_line:
                    continue

                _perform_match(
                    service,
                    ctx,
                    line,
                    journal_line,
                    source_type="CUSTOMER_PAYMENT",
                    source_id=payment.payment_id,
                    confidence=95,
                    explanation=f"Splynx payment {payment.splynx_id} (reference match)",
                )
                matched_payment_ids.add(payment.payment_id)
            except Exception as exc:
                service.logger.exception(
                    "Error matching line %s via Splynx payment: %s",
                    line.line_id,
                    exc,
                )
                ctx.result.errors.append(f"Line {line.line_number}: {exc}")


@dataclass(frozen=True)
class UniqueDateAmountStrategy(MatchStrategy):
    strategy_id: str = "unique_date_amount"
    provider: SplynxCustomerPaymentProvider = SplynxCustomerPaymentProvider()

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if (
            not ctx.policy.allows_strategy(self.strategy_id)
            or not ctx.policy.allows_source_type(self.provider.source_type)
            or not ctx.policy.allows_provider(self.provider.provider_key)
        ):
            return
        payments = [
            payment
            for payment in self.provider.load(service, ctx)
            if payment.payment_id not in ctx.tracker(self.provider.provider_key)
        ]
        if not payments:
            return
        payment_index: dict[tuple[object, int], list[Any]] = {}
        for payment in payments:
            if not payment.correlation_id:
                continue
            key = (payment.payment_date, int(Decimal(payment.amount) * 100))
            payment_index.setdefault(key, []).append(payment)

        line_index: dict[tuple[object, int], list[BankStatementLine]] = {}
        for line in ctx.still_unmatched_lines():
            key = (line.transaction_date, int(Decimal(line.amount) * 100))
            line_index.setdefault(key, []).append(line)

        matched_payment_ids = ctx.tracker(self.provider.provider_key)
        for key, indexed_payments in payment_index.items():
            available_lines = [
                line
                for line in line_index.get(key, [])
                if line.line_id not in ctx.matched_line_ids
            ]
            if not available_lines:
                continue

            pairs = min(len(indexed_payments), len(available_lines))
            for idx in range(pairs):
                payment = indexed_payments[idx]
                line = available_lines[idx]
                if payment.payment_id in matched_payment_ids:
                    continue
                try:
                    journal_line = service._find_journal_line(
                        ctx.db,
                        ctx.organization_id,
                        payment.correlation_id,
                        ctx.bank_account.gl_account_id,
                        extra_gl_account_ids=ctx.extra_gl_account_ids,
                    )
                    if not journal_line:
                        continue

                    _perform_match(
                        service,
                        ctx,
                        line,
                        journal_line,
                        source_type="CUSTOMER_PAYMENT",
                        source_id=payment.payment_id,
                        confidence=80,
                        explanation=f"Splynx payment {payment.splynx_id} (date+amount fallback)",
                    )
                    matched_payment_ids.add(payment.payment_id)
                except Exception as exc:
                    service.logger.exception(
                        "Error matching line %s via date+amount: %s",
                        line.line_id,
                        exc,
                    )
                    ctx.result.errors.append(f"Line {line.line_number}: {exc}")


@dataclass(frozen=True)
class SupplierPaymentReferenceStrategy(MatchStrategy):
    strategy_id: str = "exact_payable_reference"
    provider: SupplierPaymentProvider = SupplierPaymentProvider()

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if (
            not ctx.policy.allows_strategy(self.strategy_id)
            or not ctx.policy.allows_source_type(self.provider.source_type)
            or not ctx.policy.allows_provider(self.provider.provider_key)
        ):
            return
        payments = self.provider.load(service, ctx)
        if not payments:
            return
        matched_payment_ids = ctx.tracker(self.provider.provider_key)
        _run_directional_reference_match(
            service,
            ctx,
            payments=payments,
            matched_payment_ids=matched_payment_ids,
            line_type=StatementLineType.debit,
            source_type="SUPPLIER_PAYMENT",
            explanation_prefix="AP payment",
        )
        _run_directional_date_amount_match(
            service,
            ctx,
            payments=payments,
            matched_payment_ids=matched_payment_ids,
            line_type=StatementLineType.debit,
            source_type="SUPPLIER_PAYMENT",
            explanation_prefix="AP payment",
        )


@dataclass(frozen=True)
class CustomerReceiptReferenceStrategy(MatchStrategy):
    strategy_id: str = "exact_receivable_reference"
    provider: CustomerReceiptProvider = CustomerReceiptProvider()

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if (
            not ctx.policy.allows_strategy(self.strategy_id)
            or not ctx.policy.allows_source_type(self.provider.source_type)
            or not ctx.policy.allows_provider(self.provider.provider_key)
        ):
            return
        payments = self.provider.load(service, ctx)
        if not payments:
            return
        matched_payment_ids = ctx.tracker(self.provider.provider_key)
        _run_directional_reference_match(
            service,
            ctx,
            payments=payments,
            matched_payment_ids=matched_payment_ids,
            line_type=StatementLineType.credit,
            source_type="CUSTOMER_PAYMENT",
            explanation_prefix="AR payment",
        )
        _run_directional_date_amount_match(
            service,
            ctx,
            payments=payments,
            matched_payment_ids=matched_payment_ids,
            line_type=StatementLineType.credit,
            source_type="CUSTOMER_PAYMENT",
            explanation_prefix="AR payment",
        )


@dataclass(frozen=True)
class BankFeeStrategy(MatchStrategy):
    strategy_id: str = "fee_classification"
    provider_key: str = "bank_fee"

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if (
            not ctx.policy.allows_strategy(self.strategy_id)
            or not ctx.policy.allows_source_type("bank_fee")
            or not ctx.policy.allows_provider(self.provider_key)
        ):
            return
        still_unmatched = ctx.still_unmatched_lines()
        if not still_unmatched:
            return
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import JournalInput, JournalLineInput
        from app.services.finance.posting.base import BasePostingAdapter

        account_code = ctx.policy.gl_mappings.get(
            "fee_expense_account_code",
            ctx.config.finance_cost_account_code if ctx.config else "6080",
        )
        finance_cost_account = ctx.db.scalar(
            select(Account).where(
                Account.organization_id == ctx.organization_id,
                Account.account_code == account_code,
            )
        )
        if not finance_cost_account:
            return

        fee_lines = [
            line
            for line in still_unmatched
            if line.description
            and any(
                keyword in line.description.lower()
                for keyword in ctx.policy.fee_keywords
            )
        ]

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
                            account_id=ctx.bank_account.gl_account_id,
                            credit_amount=amount,
                            description=line.description,
                        ),
                    ],
                )
                journal, create_error = BasePostingAdapter.create_and_approve_journal(
                    ctx.db,
                    ctx.organization_id,
                    journal_input,
                    service.SYSTEM_USER_ID,
                    error_prefix="Fee journal creation failed",
                )
                if create_error:
                    ctx.result.errors.append(
                        f"Line {line.line_number}: {create_error.message}"
                    )
                    continue

                idempotency_key = BasePostingAdapter.make_idempotency_key(
                    ctx.organization_id,
                    "BANKING",
                    line.line_id,
                    action="bank-fee",
                )
                posting_result = BasePostingAdapter.post_to_ledger(
                    ctx.db,
                    organization_id=ctx.organization_id,
                    journal_entry_id=journal.journal_entry_id,
                    posting_date=line.transaction_date,
                    idempotency_key=idempotency_key,
                    source_module="BANKING",
                    correlation_id=correlation_id,
                    posted_by_user_id=service.SYSTEM_USER_ID,
                    success_message="Bank fee posted",
                    error_prefix="Fee journal posting failed",
                )
                if not posting_result.success:
                    ctx.result.errors.append(
                        f"Line {line.line_number}: {posting_result.message}"
                    )
                    continue

                journal_line = service._find_journal_line(
                    ctx.db,
                    ctx.organization_id,
                    correlation_id,
                    ctx.bank_account.gl_account_id,
                )
                if not journal_line:
                    continue

                _perform_match(
                    service,
                    ctx,
                    line,
                    journal_line,
                    source_type="BANK_FEE",
                    source_id=None,
                    confidence=95,
                    explanation=f"Bank fee: {line.description}",
                )
            except Exception as exc:
                service.logger.exception(
                    "Error matching fee line %s: %s", line.line_id, exc
                )
                ctx.result.errors.append(f"Line {line.line_number}: {exc}")


@dataclass(frozen=True)
class InterbankCounterpartStrategy(MatchStrategy):
    strategy_id: str = "counterpart_transfer"
    provider_key: str = "bank_transfer"

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if (
            not ctx.policy.allows_strategy(self.strategy_id)
            or not ctx.policy.allows_source_type("interbank_transfer")
            or not ctx.policy.allows_provider(self.provider_key)
        ):
            return
        still_unmatched = ctx.still_unmatched_lines()
        if not still_unmatched:
            return
        from datetime import timedelta

        from app.models.finance.gl.journal_entry import JournalType
        from app.services.finance.gl.journal import JournalInput, JournalLineInput
        from app.services.finance.posting.base import BasePostingAdapter

        window_days = ctx.policy.settlement_window_days
        date_window = timedelta(days=window_days)
        settlement_lines = [
            line
            for line in still_unmatched
            if line.description
            and any(
                keyword in line.description.lower()
                for keyword in ctx.policy.transfer_keywords
            )
            and not any(
                keyword in line.description.lower()
                for keyword in ctx.policy.fee_keywords
            )
        ]
        if not settlement_lines:
            return

        dedup_groups: dict[tuple[object, str | None, int], list[BankStatementLine]] = {}
        unique_settlements: list[BankStatementLine] = []
        for line in settlement_lines:
            key = (line.transaction_date, line.reference, int(line.amount * 100))
            group = dedup_groups.setdefault(key, [])
            group.append(line)
            if len(group) == 1:
                unique_settlements.append(line)

        min_date = min(line.transaction_date for line in unique_settlements)
        max_date = (
            max(line.transaction_date for line in unique_settlements) + date_window
        )

        other_bank_ids = list(
            ctx.db.scalars(
                select(BankAccount.bank_account_id).where(
                    BankAccount.organization_id == ctx.organization_id,
                    BankAccount.bank_account_id != ctx.bank_account.bank_account_id,
                    BankAccount.gl_account_id.isnot(None),
                )
            ).all()
        )
        if not other_bank_ids:
            return

        deposit_lines = list(
            ctx.db.scalars(
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
        deposit_lines = [
            dep
            for dep in deposit_lines
            if dep.description
            and (
                not ctx.policy.deposit_keywords
                or any(
                    keyword in dep.description.lower()
                    for keyword in ctx.policy.deposit_keywords
                )
            )
        ]
        if not deposit_lines:
            return

        target_accounts = {
            bank.bank_account_id: bank
            for bank in ctx.db.scalars(
                select(BankAccount).where(
                    BankAccount.bank_account_id.in_(other_bank_ids)
                )
            ).all()
        }
        deposits_by_date: dict[object, list[BankStatementLine]] = {}
        for dep in deposit_lines:
            deposits_by_date.setdefault(dep.transaction_date, []).append(dep)

        matched_deposit_ids: set[UUID] = set()
        for settlement_line in unique_settlements:
            try:
                candidates: list[BankStatementLine] = []
                for day_offset in range(window_days + 1):
                    check_date = settlement_line.transaction_date + timedelta(
                        days=day_offset
                    )
                    for dep in deposits_by_date.get(check_date, []):
                        if dep.line_id not in matched_deposit_ids:
                            candidates.append(dep)
                if not candidates:
                    continue

                best_deposit = min(
                    candidates, key=lambda dep: abs(dep.amount - settlement_line.amount)
                )
                dep_statement = ctx.db.get(BankStatement, best_deposit.statement_id)
                if not dep_statement:
                    continue
                dest_bank = target_accounts.get(dep_statement.bank_account_id)
                if not dest_bank or not dest_bank.gl_account_id:
                    continue

                correlation_id = f"settlement-{settlement_line.line_id}"
                credit_jl = service._find_journal_line(
                    ctx.db,
                    ctx.organization_id,
                    correlation_id,
                    ctx.bank_account.gl_account_id,
                )
                debit_jl = None
                if credit_jl:
                    debit_jl = service._find_journal_line(
                        ctx.db,
                        ctx.organization_id,
                        correlation_id,
                        dest_bank.gl_account_id,
                    )
                else:
                    amount = abs(settlement_line.amount)
                    journal_input = JournalInput(
                        journal_type=JournalType.STANDARD,
                        entry_date=settlement_line.transaction_date,
                        posting_date=settlement_line.transaction_date,
                        description=f"Bank transfer - {settlement_line.reference}",
                        reference=settlement_line.reference,
                        source_module="BANKING",
                        source_document_type="BANK_TRANSFER",
                        correlation_id=correlation_id,
                        lines=[
                            JournalLineInput(
                                account_id=dest_bank.gl_account_id,
                                debit_amount=amount,
                                description=f"Transfer deposit - {settlement_line.reference}",
                            ),
                            JournalLineInput(
                                account_id=ctx.bank_account.gl_account_id,
                                credit_amount=amount,
                                description=f"Settlement transfer - {settlement_line.reference}",
                            ),
                        ],
                    )
                    journal, create_error = (
                        BasePostingAdapter.create_and_approve_journal(
                            ctx.db,
                            ctx.organization_id,
                            journal_input,
                            service.SYSTEM_USER_ID,
                            error_prefix="Settlement journal creation failed",
                        )
                    )
                    if create_error:
                        ctx.result.errors.append(
                            f"Line {settlement_line.line_number}: {create_error.message}"
                        )
                        continue
                    idempotency_key = BasePostingAdapter.make_idempotency_key(
                        ctx.organization_id,
                        "BANKING",
                        settlement_line.line_id,
                        action="settlement",
                    )
                    posting_result = BasePostingAdapter.post_to_ledger(
                        ctx.db,
                        organization_id=ctx.organization_id,
                        journal_entry_id=journal.journal_entry_id,
                        posting_date=settlement_line.transaction_date,
                        idempotency_key=idempotency_key,
                        source_module="BANKING",
                        correlation_id=correlation_id,
                        posted_by_user_id=service.SYSTEM_USER_ID,
                        success_message="Settlement transfer posted",
                        error_prefix="Settlement journal posting failed",
                    )
                    if not posting_result.success:
                        ctx.result.errors.append(
                            f"Line {settlement_line.line_number}: {posting_result.message}"
                        )
                        continue
                    credit_jl = service._find_journal_line(
                        ctx.db,
                        ctx.organization_id,
                        correlation_id,
                        ctx.bank_account.gl_account_id,
                    )
                    debit_jl = service._find_journal_line(
                        ctx.db,
                        ctx.organization_id,
                        correlation_id,
                        dest_bank.gl_account_id,
                    )

                dedup_key = (
                    settlement_line.transaction_date,
                    settlement_line.reference,
                    int(settlement_line.amount * 100),
                )
                if credit_jl:
                    for dup_line in dedup_groups.get(dedup_key, [settlement_line]):
                        if dup_line.line_id in ctx.matched_line_ids:
                            continue
                        try:
                            _perform_match(
                                service,
                                ctx,
                                dup_line,
                                credit_jl,
                                source_type="INTER_BANK",
                                source_id=None,
                                confidence=85,
                                explanation=f"Settlement transfer: {settlement_line.reference}",
                            )
                        except Exception:
                            service.logger.debug(
                                "Settlement line %s match skipped",
                                dup_line.line_id,
                                exc_info=True,
                            )

                if debit_jl and best_deposit.line_id not in matched_deposit_ids:
                    try:
                        service._perform_match(
                            ctx.db,
                            ctx.organization_id,
                            best_deposit,
                            debit_jl,
                            source_type="INTER_BANK",
                            source_id=None,
                        )
                        matched_deposit_ids.add(best_deposit.line_id)
                    except Exception:
                        service.logger.debug(
                            "Deposit line %s match skipped",
                            best_deposit.line_id,
                            exc_info=True,
                        )
            except Exception as exc:
                service.logger.exception(
                    "Error matching settlement line %s: %s",
                    settlement_line.line_id,
                    exc,
                )
                ctx.result.errors.append(f"Line {settlement_line.line_number}: {exc}")


@dataclass(frozen=True)
class LegacyCustomRuleStrategy(MatchStrategy):
    strategy_id: str = "legacy_custom_rules"

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        if not ctx.policy.allows_strategy(self.strategy_id):
            return
        still_unmatched = ctx.still_unmatched_lines()
        if not still_unmatched:
            return
        try:
            from app.services.finance.banking.reconciliation_engine import (
                ReconciliationEngine,
            )

            engine = ReconciliationEngine(ctx.db)
            engine_result = engine.run_custom_rules(
                ctx.organization_id,
                ctx.statement,
                ctx.bank_account,
                ctx.unmatched_lines,
                ctx.matched_line_ids,
                amount_tolerance=ctx.config.amount_tolerance,
                date_buffer_days=ctx.config.date_buffer_days,
                extra_gl_account_ids=ctx.extra_gl_account_ids,
            )
            ctx.result.matched += engine_result.matched
            ctx.result.errors.extend(engine_result.errors)
        except Exception:
            service.logger.warning(
                "Programmatic core fallback (legacy custom rules) failed",
                exc_info=True,
            )


class ProgrammaticReconciliationEngine:
    def __init__(self) -> None:
        self.strategies: tuple[MatchStrategy, ...] = (
            PaymentIntentReferenceStrategy(),
            CustomerPaymentReferenceStrategy(),
            UniqueDateAmountStrategy(),
            SupplierPaymentReferenceStrategy(),
            CustomerReceiptReferenceStrategy(),
            BankFeeStrategy(),
            InterbankCounterpartStrategy(),
            LegacyCustomRuleStrategy(),
        )

    def run(self, service: Any, ctx: ReconciliationRunContext) -> None:
        ctx.normalized_lines = {
            line.line_id: normalize_statement_line(line) for line in ctx.unmatched_lines
        }
        ctx.line_signals = {
            line_id: extract_line_signals(normalized)
            for line_id, normalized in ctx.normalized_lines.items()
        }
        # Preserve the original query order from AutoReconciliationService:
        # preload Splynx payments once before running the pass sequence.
        SplynxCustomerPaymentProvider().load(service, ctx)

        for strategy in self.strategies:
            if not ctx.still_unmatched_lines():
                break
            strategy.run(service, ctx)


def build_extra_gl_account_ids(
    db: Any,
    organization_id: Any,
    bank_account: BankAccount,
) -> set[Any] | None:
    all_bank_gl_ids = set(
        db.scalars(
            select(BankAccount.gl_account_id).where(
                BankAccount.organization_id == organization_id,
                BankAccount.gl_account_id.isnot(None),
                BankAccount.gl_account_id != bank_account.gl_account_id,
            )
        ).all()
    )
    return all_bank_gl_ids or None
