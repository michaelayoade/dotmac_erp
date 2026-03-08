from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.models.finance.banking.reconciliation_policy import (
    ReconciliationPolicyProfile,
)
from app.services.finance.banking.auto_reconciliation import AutoMatchConfig
from app.services.finance.banking.reconciliation_policy import (
    build_policy_from_config,
)
from app.services.finance.banking.reconciliation_policy_service import (
    ReconciliationPolicyService,
)


def test_resolve_uses_legacy_compatibility_when_no_profile() -> None:
    service = ReconciliationPolicyService()
    db = MagicMock()
    db.scalar.return_value = None  # No org-specific profile in DB

    policy = service.resolve(
        db,
        uuid.uuid4(),
        legacy_config=AutoMatchConfig(),
    )

    assert "gateway_payment_intent" in policy.enabled_provider_keys
    assert "fee_classification" in policy.enabled_strategy_keys
    assert "paystack" in policy.deposit_keywords
    assert policy.gl_mappings["fee_expense_account_code"] == "6080"


def test_merge_profile_overrides_keywords_thresholds_and_mappings() -> None:
    base = ReconciliationPolicyService()._with_legacy_compatibility(
        build_policy_from_config(AutoMatchConfig())
    )
    profile = ReconciliationPolicyProfile(
        organization_id=uuid.uuid4(),
        name="acme-default",
        is_active=True,
        enabled_provider_keys=["bank_fee", "bank_transfer"],
        enabled_strategy_keys=["fee_classification", "counterpart_transfer"],
        decision_thresholds={"auto_match": 97, "suggest": 80, "ignore": 10},
        keyword_config={
            "fee_keywords": ["monthly service charge"],
            "transfer_keywords": ["internal transfer"],
            "deposit_keywords": ["stripe payout"],
        },
        gl_mapping_config={"fee_expense_account_code": "7001"},
        amount_tolerance_cents=5,
        date_buffer_days=4,
        settlement_window_days=3,
        journal_creation_strategy_keys=["fee_classification"],
        auto_post_strategy_keys=["fee_classification"],
    )

    resolved = ReconciliationPolicyService()._merge_profile(base, profile)

    assert resolved.enabled_provider_keys == frozenset({"bank_fee", "bank_transfer"})
    assert resolved.enabled_strategy_keys == frozenset(
        {"fee_classification", "counterpart_transfer"}
    )
    assert resolved.auto_match_threshold == 97
    assert resolved.suggest_threshold == 80
    assert resolved.ignore_threshold == 10
    assert resolved.fee_keywords == ("monthly service charge",)
    assert resolved.transfer_keywords == ("internal transfer",)
    assert resolved.deposit_keywords == ("stripe payout",)
    assert resolved.gl_mappings["fee_expense_account_code"] == "7001"
    assert str(resolved.amount_tolerance) == "0.05"
    assert resolved.date_buffer_days == 4
    assert resolved.settlement_window_days == 3
