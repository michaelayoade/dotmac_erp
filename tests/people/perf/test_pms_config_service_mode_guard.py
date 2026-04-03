from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.finance.core_org import Organization, PerformanceMode
from app.services.people.perf.pms_config_service import (
    PMSConfigService,
    PMSConfigServiceError,
)


def _org(mode: PerformanceMode) -> Organization:
    return Organization(
        organization_code=f"ORG-{uuid4().hex[:6]}",
        legal_name="Policy Guard Org",
        functional_currency_code="USD",
        presentation_currency_code="USD",
        fiscal_year_end_month=12,
        fiscal_year_end_day=31,
        performance_mode=mode,
        pms_ohcsf_enabled=mode in {PerformanceMode.GOVERNMENT_PMS, PerformanceMode.HYBRID},
    )


def test_activate_ohcsf_pms_blocked_in_private_mode() -> None:
    db = MagicMock()
    db.get.return_value = _org(PerformanceMode.PRIVATE)
    svc = PMSConfigService(db)
    svc._seed_competencies = MagicMock(return_value=99)  # type: ignore[method-assign]
    svc._seed_criteria_templates = MagicMock(return_value=99)  # type: ignore[method-assign]

    with pytest.raises(PMSConfigServiceError, match="MODE_POLICY_BLOCKED:pms_write"):
        svc.activate_ohcsf_pms(uuid4())

    svc._seed_competencies.assert_not_called()  # type: ignore[attr-defined]
    svc._seed_criteria_templates.assert_not_called()  # type: ignore[attr-defined]


def test_activate_ohcsf_pms_allowed_in_government_mode() -> None:
    db = MagicMock()
    db.get.return_value = _org(PerformanceMode.GOVERNMENT_PMS)
    svc = PMSConfigService(db)
    svc._seed_competencies = MagicMock(return_value=3)  # type: ignore[method-assign]
    svc._seed_criteria_templates = MagicMock(return_value=8)  # type: ignore[method-assign]

    result = svc.activate_ohcsf_pms(uuid4())

    assert result == {"competencies_created": 3, "templates_created": 8}


def test_activate_ohcsf_pms_idempotent_on_repeat_calls() -> None:
    db = MagicMock()
    db.get.return_value = _org(PerformanceMode.GOVERNMENT_PMS)
    svc = PMSConfigService(db)
    svc._seed_competencies = MagicMock(  # type: ignore[method-assign]
        side_effect=[3, 0]
    )
    svc._seed_criteria_templates = MagicMock(  # type: ignore[method-assign]
        side_effect=[8, 0]
    )

    org_id = uuid4()
    first = svc.activate_ohcsf_pms(org_id)
    second = svc.activate_ohcsf_pms(org_id)

    assert first == {"competencies_created": 3, "templates_created": 8}
    assert second == {"competencies_created": 0, "templates_created": 0}
    assert svc._seed_competencies.call_count == 2  # type: ignore[attr-defined]
    assert svc._seed_criteria_templates.call_count == 2  # type: ignore[attr-defined]
