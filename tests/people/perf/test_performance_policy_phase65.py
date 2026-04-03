from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.services.people.perf.contract_service import PerformanceContractService
from app.services.people.perf.performance_policy import (
    PerformancePolicyError,
    get_policy_profile,
)
from app.services.people.perf.scoring_engine import OHCSFScoringEngine


def test_get_policy_profile_government_defaults() -> None:
    policy = get_policy_profile("GOVERNMENT_PMS")
    assert policy.objective.required_total_weight == 70
    assert policy.competency.required_count == 5
    assert policy.appraisal_weights.objectives_pct == Decimal("0.70")
    assert policy.appraisal_weights.process_pct == Decimal("0.10")
    assert policy.appraisal_weights.competencies_pct == Decimal("0.20")
    assert policy.appeal_filing_window_workdays == 5
    assert policy.active_cycle_status == "ACTIVE"
    assert policy.active_cycle_type == "ANNUAL"
    assert ("rating-summary", "Rating Summary") in policy.mandatory_report_pack
    assert policy.ui_labels["pms_nav_label"] == "PMS (Government)"
    assert "mode_blocked_pms_write" in policy.ui_messages
    assert policy.appeal_decisions_requiring_adjusted_rating == frozenset(
        {"UPHELD", "PARTIALLY_UPHELD"}
    )
    assert policy.committee_decisions_requiring_adjusted_rating == frozenset(
        {"ADJUSTED"}
    )
    assert len(policy.ohcsf_seed_competencies) == 5
    assert len(policy.ohcsf_institutional_weights) == 6


def test_get_policy_profile_private_defaults() -> None:
    policy = get_policy_profile("PRIVATE")
    assert policy.objective.required_total_weight == 100
    assert policy.objective.min_count == 1
    assert policy.objective.max_count == 12
    assert policy.competency.required_count == 0
    assert policy.appraisal_weights.objectives_pct == Decimal("1.00")
    assert policy.ui_labels["performance_nav_label"] == "Performance (Private)"
    assert "mode_blocked_private_write" in policy.ui_messages
    assert not policy.ohcsf_seed_competencies
    assert not policy.ohcsf_institutional_weights


def test_shared_pagination_defaults_are_intentionally_common() -> None:
    gov = get_policy_profile("GOVERNMENT_PMS")
    private = get_policy_profile("PRIVATE")
    assert gov.shared_pagination_defaults == private.shared_pagination_defaults


def test_unknown_policy_profile_raises() -> None:
    with pytest.raises(PerformancePolicyError, match="Unknown performance policy profile"):
        get_policy_profile("NOT_A_PROFILE")


def test_scoring_engine_uses_profile_weights() -> None:
    gov_engine = OHCSFScoringEngine(policy_profile_name="GOVERNMENT_PMS")
    private_engine = OHCSFScoringEngine(policy_profile_name="PRIVATE")

    gov_score = gov_engine.calculate_appraisal_final(
        objective_composite=Decimal("100"),
        competency_score=Decimal("0"),
        process_score=Decimal("0"),
    )
    private_score = private_engine.calculate_appraisal_final(
        objective_composite=Decimal("100"),
        competency_score=Decimal("0"),
        process_score=Decimal("0"),
    )

    assert gov_score == Decimal("70.00")
    assert private_score == Decimal("100.00")


def test_contract_validation_uses_profile_limits_not_hardcoded() -> None:
    svc = PerformanceContractService(
        MagicMock(),
        policy_profile_name="PRIVATE",
    )
    objectives = [
        {"objective": f"Obj {i}", "kpi": f"KPI {i}", "target": "T", "weight": 10}
        for i in range(1, 11)
    ]  # 10 objectives, total=100

    svc._validate_objectives(objectives)
