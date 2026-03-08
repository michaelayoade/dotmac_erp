from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from unittest.mock import Mock
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.banking.reconciliation_policy import (
    ReconciliationPolicyProfile,
)
from app.services.finance.banking.reconciliation_policy import (
    AutoMatchConfigLike,
    ReconciliationPolicy,
    build_policy_from_config,
)

DEFAULT_ENABLED_PROVIDER_KEYS = frozenset(
    {
        "gateway_payment_intent",
        "receivable_payment_synced",
        "payable_payment",
        "receivable_payment",
        "bank_fee",
        "bank_transfer",
    }
)

DEFAULT_FEE_KEYWORDS = ("fee", "charge", "commission", "levy")
DEFAULT_TRANSFER_KEYWORDS = ("settlement",)
DEFAULT_DEPOSIT_KEYWORDS = ()
DOTMAC_COMPAT_DEPOSIT_KEYWORDS = ("paystack", "psst10")


class ReconciliationPolicyService:
    """Resolves org-scoped reconciliation policy for the banking engine."""

    def resolve(
        self,
        db: Session,
        organization_id: UUID,
        *,
        legacy_config: AutoMatchConfigLike,
    ) -> ReconciliationPolicy:
        legacy = build_policy_from_config(legacy_config)
        if isinstance(db, Mock):
            return self._with_legacy_compatibility(legacy)

        profile = db.scalar(
            select(ReconciliationPolicyProfile).where(
                ReconciliationPolicyProfile.organization_id == organization_id,
                ReconciliationPolicyProfile.is_active.is_(True),
            )
        )
        if not profile:
            return self._with_legacy_compatibility(legacy)
        return self._merge_profile(legacy, profile)

    @staticmethod
    def _with_legacy_compatibility(policy: ReconciliationPolicy) -> ReconciliationPolicy:
        deposit_keywords = tuple(dict.fromkeys((*policy.deposit_keywords, *DOTMAC_COMPAT_DEPOSIT_KEYWORDS)))
        return replace(
            policy,
            enabled_provider_keys=frozenset(
                set(policy.enabled_provider_keys) | set(DEFAULT_ENABLED_PROVIDER_KEYS)
            ),
            fee_keywords=tuple(dict.fromkeys((*policy.fee_keywords, *DEFAULT_FEE_KEYWORDS))),
            transfer_keywords=tuple(
                dict.fromkeys((*policy.transfer_keywords, *DEFAULT_TRANSFER_KEYWORDS))
            ),
            deposit_keywords=deposit_keywords,
        )

    @staticmethod
    def _merge_profile(
        base: ReconciliationPolicy,
        profile: ReconciliationPolicyProfile,
    ) -> ReconciliationPolicy:
        thresholds = profile.decision_thresholds or {}
        keywords = profile.keyword_config or {}
        gl_mappings = profile.gl_mapping_config or {}

        amount_tolerance = base.amount_tolerance
        if profile.amount_tolerance_cents is not None:
            amount_tolerance = Decimal(profile.amount_tolerance_cents) / Decimal(100)

        def _tuple_values(key: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
            values = keywords.get(key)
            if not values:
                return fallback
            return tuple(str(value).strip().lower() for value in values if str(value).strip())

        return replace(
            base,
            enabled_provider_keys=frozenset(profile.enabled_provider_keys or []),
            enabled_strategy_keys=frozenset(profile.enabled_strategy_keys or []),
            auto_match_threshold=int(
                thresholds.get("auto_match", base.auto_match_threshold)
            ),
            suggest_threshold=int(thresholds.get("suggest", base.suggest_threshold)),
            ignore_threshold=int(thresholds.get("ignore", base.ignore_threshold)),
            journal_creation_strategy_keys=frozenset(
                profile.journal_creation_strategy_keys or []
            ),
            auto_post_strategy_keys=frozenset(profile.auto_post_strategy_keys or []),
            fee_keywords=_tuple_values("fee_keywords", base.fee_keywords),
            transfer_keywords=_tuple_values(
                "transfer_keywords",
                base.transfer_keywords,
            ),
            deposit_keywords=_tuple_values(
                "deposit_keywords",
                base.deposit_keywords,
            ),
            gl_mappings={**base.gl_mappings, **gl_mappings},
            amount_tolerance=amount_tolerance,
            date_buffer_days=(
                profile.date_buffer_days
                if profile.date_buffer_days is not None
                else base.date_buffer_days
            ),
            settlement_window_days=(
                profile.settlement_window_days
                if profile.settlement_window_days is not None
                else base.settlement_window_days
            ),
        )


reconciliation_policy_service = ReconciliationPolicyService()
