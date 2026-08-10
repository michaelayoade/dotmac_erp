"""Programmatic reconciliation candidate providers."""

from __future__ import annotations

from app.services.finance.banking.programmatic_parts.base import (
    Any,
    CandidateProvider,
    PaymentIntent,
    PaymentIntentStatus,
    ReconciliationRunContext,
    cast,
    dataclass,
    select,
    timedelta,
)


@dataclass(frozen=True)
class PaymentIntentProvider(CandidateProvider):
    provider_key: str = "gateway_payment_intent"
    source_type: str = "payment_intent"

    def load(self, service: Any, ctx: ReconciliationRunContext) -> list[PaymentIntent]:
        cached = ctx.provider_cache.get(self.provider_key)
        if cached is not None:
            return cached

        buffer_days = ctx.policy.date_buffer_days
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
        loaded = service._load_ar_payments(
            ctx.db,
            ctx.organization_id,
            ctx.statement,
            config=ctx.config,
        )
        ctx.provider_cache[self.provider_key] = loaded
        return cast(list[Any], loaded)
