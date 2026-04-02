"""
Tests for PMS governance models.
"""

import uuid

from app.models.people.perf.pms_governance import (
    InstitutionalGovernanceAction,
    PMSGovernanceGrievance,
    PMSStakeholderFeedback,
)


def test_institutional_governance_action_instantiation() -> None:
    action = InstitutionalGovernanceAction(
        organization_id=uuid.uuid4(),
        inst_perf_id=uuid.uuid4(),
        actor_role="OHCSF_PMS",
        action_type="TRANSITION_STAGE",
    )
    assert action.actor_role == "OHCSF_PMS"
    assert action.action_type == "TRANSITION_STAGE"


def test_grievance_defaults() -> None:
    from sqlalchemy import inspect as sa_inspect

    mapper = sa_inspect(PMSGovernanceGrievance)
    status_col = mapper.columns["status"]
    channel_col = mapper.columns["channel"]
    assert status_col.default is not None
    assert status_col.default.arg == "OPEN"
    assert channel_col.default is not None
    assert channel_col.default.arg == "INTERNAL"


def test_stakeholder_feedback_defaults() -> None:
    from sqlalchemy import inspect as sa_inspect

    mapper = sa_inspect(PMSStakeholderFeedback)
    status_col = mapper.columns["status"]
    source_col = mapper.columns["source_type"]
    assert status_col.default is not None
    assert status_col.default.arg == "RECEIVED"
    assert source_col.default is not None
    assert source_col.default.arg == "SERVICOM"
