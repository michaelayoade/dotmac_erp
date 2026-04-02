"""
Tests for PMSGovernanceService.
"""

from __future__ import annotations

from datetime import date
import uuid
from unittest.mock import MagicMock

import pytest

from app.models.people.perf.appraisal import Appraisal
from app.models.people.perf.appraisal_cycle import AppraisalCycle
from app.models.people.perf.institutional_performance import InstitutionalPerformance
from app.models.people.perf.pms_enums import InstitutionType
from app.services.people.perf.governance_service import (
    GovernanceNotFoundError,
    GovernanceValidationError,
    PMSGovernanceService,
)

ORG_ID = uuid.uuid4()
INST_ID = uuid.uuid4()
EMP_ID = uuid.uuid4()


def _make_record(stage: str = "DRAFT") -> InstitutionalPerformance:
    record = InstitutionalPerformance(
        organization_id=ORG_ID,
        cycle_id=uuid.uuid4(),
        institution_type=InstitutionType.MINISTRY,
    )
    record.inst_perf_id = INST_ID
    record.workflow_stage = stage
    return record


def test_transition_stage_updates_workflow_and_logs_action() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    record = _make_record("DRAFT")
    added: list[object] = []
    db.scalar.return_value = record
    db.add.side_effect = lambda obj: added.append(obj)

    updated = svc.transition_stage(
        ORG_ID,
        inst_perf_id=INST_ID,
        target_stage="INTERNAL_REVIEW",
        actor_employee_id=EMP_ID,
        actor_role="MDA_PRS",
        note="Submitted for review",
    )

    assert updated.workflow_stage == "INTERNAL_REVIEW"
    assert updated.submitted_for_review_date is not None
    assert db.add.call_count >= 1
    assert db.flush.call_count >= 1
    assert getattr(added[-1], "action_type", None) == "MDA_INTERNAL_SUBMISSION"
    assert getattr(added[-1], "actor_role", None) == "MDA_PRS"


def test_transition_stage_rejects_invalid_jump() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    db.scalar.return_value = _make_record("DRAFT")

    with pytest.raises(GovernanceValidationError):
        svc.transition_stage(
            ORG_ID,
            inst_perf_id=INST_ID,
            target_stage="APPROVED",
            actor_employee_id=EMP_ID,
            actor_role="OHCSF_PMS",
        )


def test_transition_stage_rejects_unauthorized_actor_role() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    db.scalar.return_value = _make_record("DRAFT")

    with pytest.raises(GovernanceValidationError, match="allowed roles"):
        svc.transition_stage(
            ORG_ID,
            inst_perf_id=INST_ID,
            target_stage="INTERNAL_REVIEW",
            actor_employee_id=EMP_ID,
            actor_role="OHCSF_PMS",
        )


def test_assign_roles_accepts_legacy_hrm_alias_and_logs_canonical_role() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    record = _make_record("DRAFT")
    added: list[object] = []
    db.scalar.return_value = record
    db.add.side_effect = lambda obj: added.append(obj)

    svc.assign_governance_roles(
        ORG_ID,
        inst_perf_id=INST_ID,
        actor_employee_id=EMP_ID,
        actor_role="HRM",
        owner_id=uuid.uuid4(),
        reviewer_id=uuid.uuid4(),
        approver_id=uuid.uuid4(),
    )

    assert getattr(added[-1], "actor_role", None) == "MDA_HRM"
    assert getattr(added[-1], "action_type", None) == "OHCSF_GOVERNANCE_ROLE_ASSIGNMENT"


def test_assign_roles_rejects_invalid_actor_role() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    db.scalar.return_value = _make_record("DRAFT")

    with pytest.raises(
        GovernanceValidationError, match="assign institutional governance roles"
    ):
        svc.assign_governance_roles(
            ORG_ID,
            inst_perf_id=INST_ID,
            actor_employee_id=EMP_ID,
            actor_role="FMFBNP",
            owner_id=None,
            reviewer_id=None,
            approver_id=None,
        )


def test_assign_grievance_sets_assignee_and_under_review() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    grievance = MagicMock()
    grievance.status = "OPEN"
    db.scalar.return_value = grievance

    result = svc.assign_grievance(
        ORG_ID,
        grievance_id=uuid.uuid4(),
        assigned_to_employee_id=EMP_ID,
    )

    assert result.assigned_to_employee_id == EMP_ID
    assert result.status == "UNDER_REVIEW"
    assert db.flush.call_count == 1


def test_resolve_grievance_sets_resolved_fields() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    grievance = MagicMock()
    db.scalar.return_value = grievance

    result = svc.resolve_grievance(
        ORG_ID,
        grievance_id=uuid.uuid4(),
        resolution_notes="Reviewed and resolved",
    )

    assert result.status == "RESOLVED"
    assert result.resolution_notes == "Reviewed and resolved"
    assert result.resolved_date is not None


def test_escalate_grievance_sets_fcsc_fields() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    grievance = MagicMock()
    grievance.inst_perf_id = None
    db.scalar.return_value = grievance

    result = svc.escalate_grievance_to_fcsc(
        ORG_ID,
        grievance_id=uuid.uuid4(),
        escalation_notes="Escalated after failed mediation",
    )

    assert result.status == "ESCALATED"
    assert result.escalated_to_fcsc is True
    assert result.committee_level == "FCSC"


def test_get_grievance_raises_when_not_found() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    db.scalar.return_value = None

    with pytest.raises(GovernanceNotFoundError):
        svc.get_grievance(ORG_ID, uuid.uuid4())


def test_create_appraisal_grievance_rejects_late_filing() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)

    appraisal_id = uuid.uuid4()
    appraisal = Appraisal(
        organization_id=ORG_ID,
        employee_id=EMP_ID,
        cycle_id=uuid.uuid4(),
        manager_id=uuid.uuid4(),
    )
    appraisal.completed_on = date(2026, 3, 20)
    db.scalar.side_effect = [appraisal]

    with pytest.raises(GovernanceValidationError, match="5 working days"):
        svc.create_grievance(
            ORG_ID,
            raised_by_employee_id=EMP_ID,
            title="Late filing",
            description="Filed outside window",
            appraisal_id=appraisal_id,
        )


def test_create_appraisal_grievance_sets_due_date_from_cycle_year() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)

    cycle_id = uuid.uuid4()
    appraisal = Appraisal(
        organization_id=ORG_ID,
        employee_id=EMP_ID,
        cycle_id=cycle_id,
        manager_id=uuid.uuid4(),
    )
    appraisal.completed_on = date.today()

    cycle = AppraisalCycle(
        organization_id=ORG_ID,
        cycle_code="2026",
        cycle_name="2026 Annual",
        review_period_start=date(2026, 1, 1),
        review_period_end=date(2026, 12, 31),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
    )

    db.scalar.side_effect = [appraisal, cycle]
    added: list[object] = []
    db.add.side_effect = lambda obj: added.append(obj)

    svc.create_grievance(
        ORG_ID,
        raised_by_employee_id=EMP_ID,
        title="Rating concern",
        description="Request review",
        appraisal_id=uuid.uuid4(),
    )

    grievance = added[0]
    assert grievance.due_date == date(2027, 2, 28)
    assert grievance.committee_level == "HR"


def test_get_overdue_grievances_includes_escalated_status() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    grievance = MagicMock()
    grievance.raised_date = date(2024, 1, 1)
    grievance.appraisal_id = None
    grievance.status = "ESCALATED"
    db.scalars.return_value.all.return_value = [grievance]

    overdue = svc.get_overdue_grievances(ORG_ID)

    assert overdue == [grievance]


def test_create_stakeholder_feedback_rejects_invalid_source_type() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)

    with pytest.raises(GovernanceValidationError, match="source_type"):
        svc.create_stakeholder_feedback(
            ORG_ID,
            title="Feedback",
            feedback_text="Details",
            source_type="UNKNOWN",
        )


def test_escalate_grievance_logs_fcsc_touchpoint_for_institutional_record() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    grievance = MagicMock()
    grievance.inst_perf_id = INST_ID
    db.scalar.return_value = grievance
    added: list[object] = []
    db.add.side_effect = lambda obj: added.append(obj)

    svc.escalate_grievance_to_fcsc(
        ORG_ID,
        grievance_id=uuid.uuid4(),
        escalation_notes="Escalating to ombudsman",
    )

    assert added
    assert getattr(added[-1], "action_type", None) == "FCSC_GRIEVANCE_ESCALATION"
    assert getattr(added[-1], "actor_role", None) == "FCSC_OMBUDSMAN"


def test_create_stakeholder_feedback_logs_servicom_touchpoint_for_record() -> None:
    db = MagicMock()
    svc = PMSGovernanceService(db)
    added: list[object] = []
    db.add.side_effect = lambda obj: added.append(obj)

    svc.create_stakeholder_feedback(
        ORG_ID,
        title="Citizen complaint",
        feedback_text="Service delay",
        source_type="SERVICOM",
        inst_perf_id=INST_ID,
    )

    assert added
    assert (
        getattr(added[-1], "action_type", None)
        == "SERVICOM_STAKEHOLDER_FEEDBACK_CAPTURED"
    )
    assert getattr(added[-1], "actor_role", None) == "SERVICOM_NODAL"
