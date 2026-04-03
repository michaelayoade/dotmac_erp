from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.models.people.perf import AppraisalTemplateProfile
from app.services.people.perf.perf_service import PerformanceService, PerformanceServiceError


def test_create_appraisal_rejects_disallowed_template_profile() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    org_id = uuid4()

    svc.get_cycle = MagicMock(return_value=SimpleNamespace(cycle_id=uuid4()))  # type: ignore[method-assign]
    svc.get_template = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(template_profile=AppraisalTemplateProfile.PMS)
    )
    svc.allowed_template_profiles_for_org = MagicMock(  # type: ignore[method-assign]
        return_value={AppraisalTemplateProfile.PRIVATE}
    )

    with pytest.raises(PerformanceServiceError, match="not allowed"):
        svc.create_appraisal(
            org_id,
            employee_id=uuid4(),
            cycle_id=uuid4(),
            manager_id=uuid4(),
            template_id=uuid4(),
        )

    db.add.assert_not_called()


def test_create_appraisal_allows_compatible_template_profile() -> None:
    db = MagicMock()
    svc = PerformanceService(db)
    org_id = uuid4()

    svc.get_cycle = MagicMock(return_value=SimpleNamespace(cycle_id=uuid4()))  # type: ignore[method-assign]
    svc.get_template = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(template_profile=AppraisalTemplateProfile.BOTH)
    )
    svc.allowed_template_profiles_for_org = MagicMock(  # type: ignore[method-assign]
        return_value={AppraisalTemplateProfile.PRIVATE, AppraisalTemplateProfile.BOTH}
    )

    appraisal = svc.create_appraisal(
        org_id,
        employee_id=uuid4(),
        cycle_id=uuid4(),
        manager_id=uuid4(),
        template_id=uuid4(),
    )

    assert appraisal.template_id is not None
    db.add.assert_called_once()
    db.flush.assert_called()
