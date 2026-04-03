from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.finance.core_org import PerformanceMode
from app.models.people.perf import AppraisalTemplateProfile
from app.services.people.perf.perf_service import PerformanceService, PerformanceServiceError


def test_validate_template_pms_config_rejects_private_mode() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    org_id = uuid4()
    svc._resolve_org_mode = MagicMock(  # type: ignore[method-assign]
        return_value=PerformanceMode.PRIVATE
    )

    with pytest.raises(PerformanceServiceError, match="not allowed in PRIVATE mode"):
        svc._validate_template_pms_config(
            org_id=org_id,
            template_profile=AppraisalTemplateProfile.PMS,
            pms_config={"objective_weight_pct": 70, "process_weight_pct": 10, "competency_weight_pct": 20},
        )


def test_validate_template_pms_config_rejects_private_profile() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    org_id = uuid4()
    svc._resolve_org_mode = MagicMock(  # type: ignore[method-assign]
        return_value=PerformanceMode.HYBRID
    )

    with pytest.raises(PerformanceServiceError, match="requires template_profile PMS or BOTH"):
        svc._validate_template_pms_config(
            org_id=org_id,
            template_profile=AppraisalTemplateProfile.PRIVATE,
            pms_config={"objective_weight_pct": 70, "process_weight_pct": 10, "competency_weight_pct": 20},
        )


def test_validate_template_pms_config_rejects_invalid_totals_and_counts() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    org_id = uuid4()
    svc._resolve_org_mode = MagicMock(  # type: ignore[method-assign]
        return_value=PerformanceMode.GOVERNMENT_PMS
    )

    with pytest.raises(PerformanceServiceError, match="must total 100"):
        svc._validate_template_pms_config(
            org_id=org_id,
            template_profile=AppraisalTemplateProfile.PMS,
            pms_config={"objective_weight_pct": 60, "process_weight_pct": 20, "competency_weight_pct": 10},
        )

    with pytest.raises(PerformanceServiceError, match="cannot exceed"):
        svc._validate_template_pms_config(
            org_id=org_id,
            template_profile=AppraisalTemplateProfile.BOTH,
            pms_config={
                "objective_weight_pct": 70,
                "process_weight_pct": 10,
                "competency_weight_pct": 20,
                "required_competency_count": 2,
                "required_development_focus_count": 3,
            },
        )


def test_create_template_persists_normalized_pms_config() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    org_id = uuid4()
    svc._resolve_org_mode = MagicMock(  # type: ignore[method-assign]
        return_value=PerformanceMode.GOVERNMENT_PMS
    )

    template = svc.create_template(
        org_id,
        template_code="TPL-001",
        template_name="Gov PMS Template",
        template_profile=AppraisalTemplateProfile.PMS,
        pms_config={
            "objective_weight_pct": "70",
            "process_weight_pct": "10",
            "competency_weight_pct": "20",
            "required_competency_count": "5",
            "required_development_focus_count": "3",
            "evidence_required": True,
        },
    )

    assert template.pms_config is not None
    assert template.pms_config["objective_weight_pct"] == 70
    assert template.pms_config["process_weight_pct"] == 10
    assert template.pms_config["competency_weight_pct"] == 20
    db.add.assert_called_once()
    db.flush.assert_called()


def test_update_template_clears_pms_config_when_profile_becomes_private() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    org_id = uuid4()
    template_id = uuid4()
    existing = SimpleNamespace(
        template_id=template_id,
        template_profile=AppraisalTemplateProfile.BOTH,
        pms_config={"objective_weight_pct": 70, "process_weight_pct": 10, "competency_weight_pct": 20},
    )
    svc.get_template = MagicMock(return_value=existing)  # type: ignore[method-assign]
    svc._resolve_org_mode = MagicMock(  # type: ignore[method-assign]
        return_value=PerformanceMode.HYBRID
    )

    updated = svc.update_template(
        org_id,
        template_id,
        template_profile=AppraisalTemplateProfile.PRIVATE,
    )

    assert updated.pms_config is None
    assert updated.template_profile == AppraisalTemplateProfile.PRIVATE
