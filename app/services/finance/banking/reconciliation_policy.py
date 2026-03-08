from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol


class AutoMatchConfigLike(Protocol):
    pass_payment_intents_enabled: bool
    pass_splynx_by_ref_enabled: bool
    pass_splynx_date_amount_enabled: bool
    pass_ap_payments_enabled: bool
    pass_ar_payments_enabled: bool
    pass_bank_fees_enabled: bool
    pass_settlements_enabled: bool


@dataclass(frozen=True)
class ReconciliationPolicy:
    enabled_provider_keys: frozenset[str] = field(default_factory=frozenset)
    enabled_source_types: frozenset[str] = field(default_factory=frozenset)
    enabled_strategy_keys: frozenset[str] = field(default_factory=frozenset)
    auto_match_threshold: int = 90
    suggest_threshold: int = 70
    ignore_threshold: int = 0
    journal_creation_strategy_keys: frozenset[str] = field(default_factory=frozenset)
    auto_post_strategy_keys: frozenset[str] = field(default_factory=frozenset)
    fee_keywords: tuple[str, ...] = ()
    transfer_keywords: tuple[str, ...] = ()
    deposit_keywords: tuple[str, ...] = ()
    gl_mappings: dict[str, str] = field(default_factory=dict)
    amount_tolerance: Decimal = Decimal("0.01")
    date_buffer_days: int = 7
    settlement_window_days: int = 10

    def allows_provider(self, provider_key: str) -> bool:
        return provider_key in self.enabled_provider_keys

    def allows_strategy(self, strategy_id: str) -> bool:
        return strategy_id in self.enabled_strategy_keys

    def allows_source_type(self, source_type: str) -> bool:
        return source_type in self.enabled_source_types


def build_policy_from_config(config: AutoMatchConfigLike) -> ReconciliationPolicy:
    enabled_sources: set[str] = set()
    enabled_providers: set[str] = set()
    enabled_strategies: set[str] = set()
    journal_creation: set[str] = set()
    auto_post: set[str] = set()

    if config.pass_payment_intents_enabled:
        enabled_sources.add("payment_intent")
        enabled_providers.add("gateway_payment_intent")
        enabled_strategies.add("exact_external_reference")
    if config.pass_splynx_by_ref_enabled:
        enabled_sources.add("customer_payment")
        enabled_providers.add("receivable_payment_synced")
        enabled_strategies.add("exact_synced_receivable_reference")
    if config.pass_splynx_date_amount_enabled:
        enabled_sources.add("customer_payment")
        enabled_providers.add("receivable_payment_synced")
        enabled_strategies.add("unique_date_amount")
    if config.pass_ap_payments_enabled:
        enabled_sources.add("supplier_payment")
        enabled_providers.add("payable_payment")
        enabled_strategies.add("exact_payable_reference")
    if config.pass_ar_payments_enabled:
        enabled_sources.add("customer_payment")
        enabled_providers.add("receivable_payment")
        enabled_strategies.add("exact_receivable_reference")
    if config.pass_bank_fees_enabled:
        enabled_sources.add("bank_fee")
        enabled_providers.add("bank_fee")
        enabled_strategies.add("fee_classification")
        journal_creation.add("fee_classification")
        auto_post.add("fee_classification")
    if config.pass_settlements_enabled:
        enabled_sources.add("interbank_transfer")
        enabled_providers.add("bank_transfer")
        enabled_strategies.add("counterpart_transfer")
        journal_creation.add("counterpart_transfer")
        auto_post.add("counterpart_transfer")

    # Keep custom DB rules as a legacy extension stage, but behind the same policy surface.
    enabled_strategies.add("legacy_custom_rules")

    return ReconciliationPolicy(
        enabled_provider_keys=frozenset(enabled_providers),
        enabled_source_types=frozenset(enabled_sources),
        enabled_strategy_keys=frozenset(enabled_strategies),
        journal_creation_strategy_keys=frozenset(journal_creation),
        auto_post_strategy_keys=frozenset(auto_post),
        fee_keywords=("fee", "charge", "commission", "levy"),
        transfer_keywords=("settlement",),
        deposit_keywords=(),
        gl_mappings={
            "fee_expense_account_code": getattr(
                config,
                "finance_cost_account_code",
                "6080",
            )
            or "6080"
        },
        amount_tolerance=getattr(config, "amount_tolerance", Decimal("0.01")),
        date_buffer_days=getattr(config, "date_buffer_days", 7),
        settlement_window_days=getattr(config, "settlement_date_window_days", 10),
    )
