"""Payment-oriented programmatic reconciliation strategies."""

from __future__ import annotations

from app.services.finance.banking.programmatic_parts.base import (
    Any,
    MatchStrategy,
    ReconciliationRunContext,
    StatementLineType,
    dataclass,
)
from app.services.finance.banking.programmatic_parts.helpers import (
    _can_auto_match,
    _find_entity_for_line,
    _payment_intent_ref_lookup,
    _perform_match,
    _reference_confidence,
    _run_directional_date_amount_match,
    _run_directional_reference_match,
    _date_within_window,
)
from app.services.finance.banking.programmatic_parts.providers import (
    CustomerReceiptProvider,
    PaymentIntentProvider,
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

                tolerance = ctx.policy.amount_tolerance
                if not service._amounts_match(
                    line.amount, intent.amount, tolerance=tolerance
                ):
                    continue
                if not _date_within_window(
                    line.transaction_date,
                    intent.paid_at,
                    ctx.policy.date_buffer_days,
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

                confidence = _reference_confidence(ctx)
                if not _can_auto_match(ctx, confidence):
                    continue
                _perform_match(
                    service,
                    ctx,
                    line,
                    journal_line,
                    source_type="PAYMENT_INTENT",
                    source_id=intent.intent_id,
                    confidence=confidence,
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
