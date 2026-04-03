from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.finance.core_org import Organization, PerformanceMode
from app.models.people.perf.pms_enums import ContractType
from app.services.people.perf.contract_service import (
    ContractValidationError,
    PerformanceContractService,
)
from app.services.people.perf.ohcsf_appraisal_service import (
    OHCSFAppraisalError,
    OHCSFAppraisalService,
)
from app.services.people.perf.perf_service import PerformanceService, PerformanceServiceError
from app.services.people.perf.performance_mode_policy import (
    enforce_pms_write_mode,
    enforce_private_write_mode,
)


def _make_org(mode: PerformanceMode) -> Organization:
    return Organization(
        organization_code=f"ORG-{uuid4().hex[:8]}",
        legal_name="Mode Guard Org",
        functional_currency_code="USD",
        presentation_currency_code="USD",
        fiscal_year_end_month=12,
        fiscal_year_end_day=31,
        performance_mode=mode,
        pms_ohcsf_enabled=mode in {PerformanceMode.GOVERNMENT_PMS, PerformanceMode.HYBRID},
    )


def _valid_competencies() -> list[dict]:
    ids = [str(uuid4()) for _ in range(5)]
    return [
        {"competency_id": ids[0], "is_development_focus": True},
        {"competency_id": ids[1], "is_development_focus": True},
        {"competency_id": ids[2], "is_development_focus": True},
        {"competency_id": ids[3], "is_development_focus": False},
        {"competency_id": ids[4], "is_development_focus": False},
    ]


def test_enforce_pms_write_mode_blocks_private_org() -> None:
    db = MagicMock()
    db.get.return_value = _make_org(PerformanceMode.PRIVATE)

    with pytest.raises(ValueError, match="MODE_POLICY_BLOCKED:pms_write_requires"):
        enforce_pms_write_mode(db, uuid4())


def test_enforce_private_write_mode_blocks_government_org() -> None:
    db = MagicMock()
    db.get.return_value = _make_org(PerformanceMode.GOVERNMENT_PMS)

    with pytest.raises(ValueError, match="MODE_POLICY_BLOCKED:private_write_requires"):
        enforce_private_write_mode(db, uuid4())


def test_contract_create_blocked_in_private_mode() -> None:
    db = MagicMock()
    db.get.return_value = _make_org(PerformanceMode.PRIVATE)
    svc = PerformanceContractService(db)

    with pytest.raises(ContractValidationError, match="MODE_POLICY_BLOCKED:pms_write"):
        svc.create_contract(
            uuid4(),
            cycle_id=uuid4(),
            employee_id=uuid4(),
            supervisor_id=uuid4(),
            contract_code="C-001",
            contract_type=ContractType.INDIVIDUAL,
            objectives=[
                {"objective": "A", "kpi": "KPI A", "target": "T", "weight": 20},
                {"objective": "B", "kpi": "KPI B", "target": "T", "weight": 20},
                {"objective": "C", "kpi": "KPI C", "target": "T", "weight": 30},
            ],
            competency_ids=_valid_competencies(),
        )


def test_contract_create_allowed_in_hybrid_mode() -> None:
    db = MagicMock()
    db.get.return_value = _make_org(PerformanceMode.HYBRID)
    db.scalar.return_value = None
    svc = PerformanceContractService(db)

    contract = svc.create_contract(
        uuid4(),
        cycle_id=uuid4(),
        employee_id=uuid4(),
        supervisor_id=uuid4(),
        contract_code="C-002",
        contract_type=ContractType.INDIVIDUAL,
        objectives=[
            {"objective": "A", "kpi": "KPI A", "target": "T", "weight": 20},
            {"objective": "B", "kpi": "KPI B", "target": "T", "weight": 20},
            {"objective": "C", "kpi": "KPI C", "target": "T", "weight": 30},
        ],
        competency_ids=_valid_competencies(),
    )

    assert contract.contract_code == "C-002"
    db.add.assert_called()


def test_private_appraisal_flow_blocked_in_government_mode() -> None:
    db = MagicMock()
    db.get.return_value = _make_org(PerformanceMode.GOVERNMENT_PMS)
    svc = PerformanceService(db)

    with pytest.raises(PerformanceServiceError, match="MODE_POLICY_BLOCKED:private_write"):
        svc.create_appraisal(
            uuid4(),
            employee_id=uuid4(),
            cycle_id=uuid4(),
            manager_id=uuid4(),
            template_id=None,
        )


def test_private_mode_cannot_invoke_pms_workflow_transition() -> None:
    db = MagicMock()
    db.get.return_value = _make_org(PerformanceMode.PRIVATE)
    svc = OHCSFAppraisalService(db)

    with pytest.raises(OHCSFAppraisalError, match="MODE_POLICY_BLOCKED:pms_write"):
        svc.submit_self_assessment_ohcsf(
            uuid4(),
            uuid4(),
            self_overall_rating=3,
            self_summary="Test submission",
        )
