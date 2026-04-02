"""
Tests for OHCSF PMS model extensions.

Verifies that existing models (Appraisal, AppraisalCycle) have been
extended with the required OHCSF fields.
"""

from uuid import uuid4

from sqlalchemy import inspect as sa_inspect

from app.models.people.perf.appraisal import Appraisal, AppraisalStatus
from app.models.people.perf.appraisal_cycle import AppraisalCycle


def _col_default_arg(model, column_name):
    """Return the Python-level default.arg for a column."""
    mapper = sa_inspect(model)
    col = mapper.columns[column_name]
    if col.default is None:
        return None
    return col.default.arg


def test_appraisal_has_ohcsf_fields():
    a = Appraisal(
        organization_id=uuid4(),
        employee_id=uuid4(),
        cycle_id=uuid4(),
        manager_id=uuid4(),
    )
    assert a.counter_signer_id is None
    assert a.process_self_rating is None
    assert a.objective_weighted_score is None
    # Boolean defaults are False via column metadata
    assert _col_default_arg(Appraisal, "is_prior_year_carryover") is False
    assert _col_default_arg(Appraisal, "is_probation_appraisal") is False
    assert _col_default_arg(Appraisal, "is_secondment_appraisal") is False
    assert a.debrief_date is None
    assert _col_default_arg(Appraisal, "reward_nominated") is False


def test_appraisal_ohcsf_countersign_fields():
    a = Appraisal(
        organization_id=uuid4(),
        employee_id=uuid4(),
        cycle_id=uuid4(),
        manager_id=uuid4(),
    )
    assert a.counter_signer_date is None
    assert a.counter_signer_comments is None
    assert a.committee_review_date is None
    assert a.committee_decision is None
    assert a.committee_notes is None
    assert _col_default_arg(Appraisal, "is_quarterly") is False
    assert a.quarterly_rating is None


def test_appraisal_ohcsf_process_scoring_fields():
    a = Appraisal(
        organization_id=uuid4(),
        employee_id=uuid4(),
        cycle_id=uuid4(),
        manager_id=uuid4(),
    )
    assert a.process_self_rating is None
    assert a.process_manager_rating is None
    assert a.process_final_rating is None
    assert a.process_comments is None
    assert a.competency_weighted_score is None
    assert a.process_weighted_score is None


def test_appraisal_ohcsf_carryover_fields():
    a = Appraisal(
        organization_id=uuid4(),
        employee_id=uuid4(),
        cycle_id=uuid4(),
        manager_id=uuid4(),
    )
    assert a.carryover_source_id is None
    assert a.absence_months is None
    assert a.approved_absence_evidence is None


def test_appraisal_ohcsf_secondment_fields():
    a = Appraisal(
        organization_id=uuid4(),
        employee_id=uuid4(),
        cycle_id=uuid4(),
        manager_id=uuid4(),
    )
    assert a.secondment_org_name is None
    assert _col_default_arg(Appraisal, "parent_org_notified") is False
    assert a.parent_org_notified_date is None
    assert a.confirmation_recommendation is None


def test_appraisal_ohcsf_debrief_reward_fields():
    a = Appraisal(
        organization_id=uuid4(),
        employee_id=uuid4(),
        cycle_id=uuid4(),
        manager_id=uuid4(),
    )
    assert a.debrief_notes is None
    assert _col_default_arg(Appraisal, "debrief_acknowledged") is False
    assert a.reward_type is None
    assert a.reward_notes is None


def test_appraisal_status_has_ohcsf_values():
    assert AppraisalStatus.PENDING_COUNTERSIGN == "PENDING_COUNTERSIGN"
    assert AppraisalStatus.COUNTERSIGNED == "COUNTERSIGNED"
    assert AppraisalStatus.PENDING_COMMITTEE == "PENDING_COMMITTEE"


def test_appraisal_status_retains_existing_values():
    assert AppraisalStatus.DRAFT == "DRAFT"
    assert AppraisalStatus.SELF_ASSESSMENT == "SELF_ASSESSMENT"
    assert AppraisalStatus.PENDING_REVIEW == "PENDING_REVIEW"
    assert AppraisalStatus.UNDER_REVIEW == "UNDER_REVIEW"
    assert AppraisalStatus.PENDING_CALIBRATION == "PENDING_CALIBRATION"
    assert AppraisalStatus.CALIBRATION == "CALIBRATION"
    assert AppraisalStatus.COMPLETED == "COMPLETED"
    assert AppraisalStatus.CANCELLED == "CANCELLED"


def test_appraisal_cycle_has_quarterly_fields():
    cycle = AppraisalCycle(
        organization_id=uuid4(),
        cycle_code="2026-Q1",
        cycle_name="Q1 2026",
        review_period_start="2026-01-01",
        review_period_end="2026-03-31",
        start_date="2026-04-01",
        end_date="2026-04-15",
    )
    assert _col_default_arg(AppraisalCycle, "cycle_type") == "ANNUAL"
    assert cycle.parent_cycle_id is None
    assert cycle.quarter is None


def test_appraisal_cycle_quarterly_fields_settable():
    parent_id = uuid4()
    cycle = AppraisalCycle(
        organization_id=uuid4(),
        cycle_code="2026-Q1",
        cycle_name="Q1 2026",
        review_period_start="2026-01-01",
        review_period_end="2026-03-31",
        start_date="2026-04-01",
        end_date="2026-04-15",
        cycle_type="QUARTERLY",
        parent_cycle_id=parent_id,
        quarter=1,
    )
    assert cycle.cycle_type == "QUARTERLY"
    assert cycle.parent_cycle_id == parent_id
    assert cycle.quarter == 1
