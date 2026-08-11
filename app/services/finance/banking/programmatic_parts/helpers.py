"""Programmatic reconciliation helper functions."""

from __future__ import annotations

from app.services.finance.banking.programmatic_parts.base import (
    Any,
    BankAccount,
    BankStatementLine,
    Decimal,
    PaymentIntent,
    ReconciliationRunContext,
    StatementLineType,
    UUID,
    select,
)


def _amount_cents(value: Any) -> int:
    return int((Decimal(value) * 100).to_integral_value())


def _date_offset_days(left: Any, right: Any) -> int | None:
    if left is None or right is None:
        return None
    if hasattr(left, "date"):
        left = left.date()
    if hasattr(right, "date"):
        right = right.date()
    return int(abs((left - right).days))


def _date_within_window(left: Any, right: Any, window_days: int) -> bool:
    offset = _date_offset_days(left, right)
    return offset is not None and offset <= window_days


def _reference_confidence(ctx: ReconciliationRunContext) -> int:
    return max(95, int(getattr(ctx.policy, "auto_match_threshold", 95)))


def _fallback_confidence(ctx: ReconciliationRunContext) -> int:
    # Exact amount + date-only matches are intentionally below the auto-match
    # threshold. They remain available as implementation scaffolding, but should
    # not be auto-suggested without a strong reference/counterparty signal.
    return min(60, int(getattr(ctx.policy, "auto_match_threshold", 95)) - 1)


def _can_auto_match(ctx: ReconciliationRunContext, confidence: int) -> bool:
    return confidence >= int(getattr(ctx.policy, "auto_match_threshold", 95))


def _find_entity_for_line(
    ctx: ReconciliationRunContext,
    line: BankStatementLine,
    ref_lookup: dict[str, Any],
) -> Any | None:
    normalized = ctx.normalized_lines.get(line.line_id)
    if not normalized:
        return None
    searchable_text = normalized.searchable_text.lower()
    matches: list[Any] = []
    for ref, entity in ref_lookup.items():
        if ref.lower() in searchable_text:
            matches.append(entity)
    if len({id(match) for match in matches}) != 1:
        return None
    return matches[0]


def _payment_intent_ref_lookup(
    intents: list[PaymentIntent],
) -> dict[str, PaymentIntent]:
    ref_to_intent: dict[str, PaymentIntent] = {}
    ambiguous_refs: set[str] = set()
    for intent in intents:
        ref = getattr(intent, "paystack_reference", None)
        if not ref:
            continue
        if ref in ref_to_intent and ref_to_intent[ref] is not intent:
            ambiguous_refs.add(ref)
        else:
            ref_to_intent[ref] = intent
    for ref in ambiguous_refs:
        ref_to_intent.pop(ref, None)
    return ref_to_intent


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
    ambiguous_refs: set[str] = set()
    for payment in payments:
        if getattr(payment, "payment_number", None):
            ref = payment.payment_number
            if ref in ref_to_payment and ref_to_payment[ref] is not payment:
                ambiguous_refs.add(ref)
            else:
                ref_to_payment[ref] = payment
        if (
            getattr(payment, "reference", None)
            and payment.reference not in ref_to_payment
        ):
            ref_to_payment[payment.reference] = payment
        elif (
            getattr(payment, "reference", None)
            and ref_to_payment.get(payment.reference) is not payment
        ):
            ambiguous_refs.add(payment.reference)
    for ref in ambiguous_refs:
        ref_to_payment.pop(ref, None)
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

            tolerance = ctx.policy.amount_tolerance
            if not service._amounts_match(
                line.amount, payment.amount, tolerance=tolerance
            ):
                continue
            if not _date_within_window(
                line.transaction_date,
                payment.payment_date,
                ctx.policy.date_buffer_days,
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

            confidence = _reference_confidence(ctx)
            if not _can_auto_match(ctx, confidence):
                continue
            _perform_match(
                service,
                ctx,
                line,
                journal_line,
                source_type=source_type,
                source_id=payment.payment_id,
                confidence=confidence,
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
        key = (payment.payment_date, _amount_cents(payment.amount))
        payment_index.setdefault(key, []).append(payment)

    line_index: dict[tuple[object, int], list[BankStatementLine]] = {}
    for line in ctx.still_unmatched_lines():
        if line.transaction_type != line_type:
            continue
        key = (line.transaction_date, _amount_cents(line.amount))
        line_index.setdefault(key, []).append(line)

    for key, indexed_payments in payment_index.items():
        available_lines = [
            line
            for line in line_index.get(key, [])
            if line.line_id not in ctx.matched_line_ids
        ]
        if len(indexed_payments) != 1 or len(available_lines) != 1:
            continue

        payment = indexed_payments[0]
        line = available_lines[0]
        if payment.payment_id in matched_payment_ids:
            continue
        confidence = _fallback_confidence(ctx)
        if not _can_auto_match(ctx, confidence):
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
                confidence=confidence,
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
