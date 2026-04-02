"""
Tests for PMSDisputeSLAService automation logic.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock, patch

from app.models.people.perf.appraisal import Appraisal
from app.models.people.perf.appraisal_appeal import AppraisalAppeal
from app.models.people.perf.appraisal_cycle import AppraisalCycle
from app.models.people.perf.pip import PerformanceImprovementPlan
from app.models.people.perf.pms_enums import AppealStatus, PIPCauseCategory, PIPStatus
from app.models.people.perf.pms_governance import PMSGovernanceGrievance
from app.services.people.perf.dispute_sla_service import PMSDisputeSLAService


def test_enforce_overdue_appeals_auto_refers_to_committee() -> None:
    db = MagicMock()
    service = PMSDisputeSLAService(db)

    org_id = uuid.uuid4()
    appraisal_id = uuid.uuid4()
    cycle_id = uuid.uuid4()

    appeal = AppraisalAppeal(
        organization_id=org_id,
        appraisal_id=appraisal_id,
        employee_id=uuid.uuid4(),
        status=AppealStatus.FILED,
        filed_date=date(2025, 3, 1),
        reason="Rating dispute",
    )

    appraisal = Appraisal(
        organization_id=org_id,
        employee_id=uuid.uuid4(),
        cycle_id=cycle_id,
        manager_id=uuid.uuid4(),
    )
    cycle = AppraisalCycle(
        organization_id=org_id,
        cycle_code="2025",
        cycle_name="2025 Annual",
        review_period_start=date(2025, 1, 1),
        review_period_end=date(2025, 12, 31),
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
    )

    db.scalars.return_value.all.return_value = [appeal]
    db.scalar.side_effect = [appraisal, cycle]

    result = service.enforce_overdue_appeals(today=date(2026, 3, 10))

    assert result["auto_referred"] == 1
    assert appeal.status == AppealStatus.REFERRED_TO_COMMITTEE
    assert appeal.committee_referral_date == date(2026, 3, 10)


def test_enforce_overdue_grievances_auto_escalates_fcsc() -> None:
    db = MagicMock()
    service = PMSDisputeSLAService(db)

    grievance = PMSGovernanceGrievance(
        organization_id=uuid.uuid4(),
        raised_by_employee_id=uuid.uuid4(),
        title="Appeal handling delay",
        description="No committee response",
        status="OPEN",
    )
    grievance.grievance_id = uuid.uuid4()
    grievance.escalated_to_fcsc = False
    db.scalars.return_value.all.return_value = [grievance]

    with patch(
        "app.services.people.perf.dispute_sla_service.PMSGovernanceService"
    ) as mock_gov_cls:
        mock_gov = mock_gov_cls.return_value
        mock_gov.get_overdue_grievances.return_value = [grievance]

        result = service.enforce_overdue_grievances(today=date(2026, 3, 10))

    assert result["auto_escalated"] == 1
    mock_gov.escalate_grievance_to_fcsc.assert_called_once()


def test_enforce_overdue_pips_auto_escalates() -> None:
    db = MagicMock()
    service = PMSDisputeSLAService(db)

    pip = PerformanceImprovementPlan(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        supervisor_id=uuid.uuid4(),
        hr_officer_id=uuid.uuid4(),
        pip_code="PIP-2026-0001",
        status=PIPStatus.ACTIVE,
        start_date=date(2025, 10, 1),
        end_date=date(2025, 12, 31),
        reason="Underperformance",
        cause_category=PIPCauseCategory.SKILLS,
        improvement_areas=[],
    )
    db.scalars.return_value.all.return_value = [pip]

    result = service.enforce_overdue_pips(today=date(2026, 1, 10))

    assert result["auto_escalated"] == 1
    assert pip.status == PIPStatus.ESCALATED
    assert pip.committee_referral_date == date(2026, 1, 10)


def test_collect_upcoming_deadline_reminders_includes_grievances_and_pips() -> None:
    db = MagicMock()
    service = PMSDisputeSLAService(db)

    grievance = PMSGovernanceGrievance(
        organization_id=uuid.uuid4(),
        raised_by_employee_id=uuid.uuid4(),
        title="Rating dispute",
        description="Awaiting action",
        status="UNDER_REVIEW",
        due_date=date(2026, 4, 5),
    )
    grievance.grievance_id = uuid.uuid4()

    pip = PerformanceImprovementPlan(
        organization_id=uuid.uuid4(),
        employee_id=uuid.uuid4(),
        supervisor_id=uuid.uuid4(),
        hr_officer_id=uuid.uuid4(),
        pip_code="PIP-2026-0100",
        status=PIPStatus.ACTIVE,
        start_date=date(2026, 3, 1),
        end_date=date(2026, 4, 4),
        reason="Performance support",
        cause_category=PIPCauseCategory.CLARITY,
        improvement_areas=[],
    )

    grievance_rows = MagicMock()
    grievance_rows.all.return_value = [grievance]
    pip_rows = MagicMock()
    pip_rows.all.return_value = [pip]
    db.scalars.side_effect = [grievance_rows, pip_rows]

    result = service.collect_upcoming_deadline_reminders(
        days_ahead=7, today=date(2026, 4, 1)
    )

    assert len(result["grievances"]) == 1
    assert len(result["pips"]) == 1
