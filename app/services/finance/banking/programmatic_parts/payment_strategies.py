"""Payment-oriented programmatic reconciliation strategies."""

from __future__ import annotations

from app.services.finance.banking.programmatic_parts.base import (
    Any,
    BankStatementLine,
    Decimal,
    MatchStrategy,
    ReconciliationRunContext,
    StatementLineType,
    dataclass,
)
from app.services.finance.banking.programmatic_parts.helpers import (
    _find_entity_for_line,
    _payment_intent_ref_lookup,
    _perform_match,
    _run_directional_date_amount_match,
    _run_directional_reference_match,
    _splynx_ref_lookup,
)
from app.services.finance.banking.programmatic_parts.providers import (
    CustomerReceiptProvider,
    PaymentIntentProvider,
    SplynxCustomerPaymentProvider,
    SupplierPaymentProvider,
)


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

        # Pass 1: exact date + amount (original greedy pairing)
        for key, indexed_payments in payment_index.items():
            available_lines = [
                line
                for line in line_index.get(key, [])
                if line.line_id not in ctx.matched_line_ids
            ]
            # Ambiguous buckets stay for human review. We only auto-suggest
            # from this fallback when there is exactly one payment candidate
            # and one statement line candidate for the bucket.
            if len(indexed_payments) != 1 or len(available_lines) != 1:
                continue

            payment = indexed_payments[0]
            line = available_lines[0]
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
                    explanation=(
                        f"Splynx payment {payment.splynx_id} "
                        "(unique exact date+amount fallback)"
                    ),
                )
                matched_payment_ids.add(payment.payment_id)
            except Exception as exc:
                service.logger.exception(
                    "Error matching line %s via date+amount: %s",
                    line.line_id,
                    exc,
                )
                ctx.result.errors.append(f"Line {line.line_number}: {exc}")

        # Pass 2: date-tolerant amount match.
        # Splynx operators often record payments 1-7 days after the bank
        # posts them.  For each still-unmatched bank line, find the
        # closest-date payment with the same amount.

        buffer_days = ctx.config.date_buffer_days if ctx.config else 7
        amount_index: dict[int, list[Any]] = {}
        for payment in payments:
            if payment.payment_id in matched_payment_ids or not payment.correlation_id:
                continue
            amt_key = int(Decimal(payment.amount) * 100)
            amount_index.setdefault(amt_key, []).append(payment)

        for line in ctx.still_unmatched_lines():
            amt_key = int(Decimal(line.amount) * 100)
            candidates = amount_index.get(amt_key)
            if not candidates:
                continue
            competing_lines = [
                candidate_line
                for candidate_line in ctx.still_unmatched_lines()
                if candidate_line.line_id not in ctx.matched_line_ids
                and int(Decimal(candidate_line.amount) * 100) == amt_key
                and abs((candidate_line.transaction_date - line.transaction_date).days)
                <= buffer_days
            ]
            if len(competing_lines) != 1:
                continue
            nearby = [
                p
                for p in candidates
                if p.payment_id not in matched_payment_ids
                and abs((p.payment_date - line.transaction_date).days) <= buffer_days
            ]
            if len(nearby) != 1:
                continue
            best = nearby[0]
            try:
                journal_line = service._find_journal_line(
                    ctx.db,
                    ctx.organization_id,
                    best.correlation_id,
                    ctx.bank_account.gl_account_id,
                    extra_gl_account_ids=ctx.extra_gl_account_ids,
                )
                if not journal_line:
                    continue
                days_off = abs((best.payment_date - line.transaction_date).days)
                _perform_match(
                    service,
                    ctx,
                    line,
                    journal_line,
                    source_type="CUSTOMER_PAYMENT",
                    source_id=best.payment_id,
                    confidence=70,
                    explanation=(
                        f"Splynx payment {best.splynx_id} "
                        f"(amount match, {days_off}d date offset)"
                    ),
                )
                matched_payment_ids.add(best.payment_id)
            except Exception as exc:
                service.logger.exception(
                    "Error matching line %s via date-tolerant amount: %s",
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
