"""
Performance Management Models.

This module contains models for appraisals, KPIs, KRAs, scorecards, and PMS workflows.
"""

from app.models.people.perf.appraisal import (
    Appraisal,
    AppraisalFeedback,
    AppraisalKRAScore,
    AppraisalStatus,
)
from app.models.people.perf.appraisal_appeal import AppraisalAppeal
from app.models.people.perf.appraisal_cycle import AppraisalCycle, AppraisalCycleStatus
from app.models.people.perf.appraisal_outcome_action import AppraisalOutcomeAction
from app.models.people.perf.appraisal_template import (
    AppraisalTemplate,
    AppraisalTemplateKRA,
    AppraisalTemplateProfile,
)
from app.models.people.perf.competency_assessment import CompetencyAssessment
from app.models.people.perf.contract_amendment import ContractAmendmentWorkflow
from app.models.people.perf.department_template import DepartmentPerformanceTemplate
from app.models.people.perf.institutional_performance import (
    InstitutionalCriteriaTemplate,
    InstitutionalPerformance,
)
from app.models.people.perf.kpi import KPI, KPIStatus
from app.models.people.perf.kra import KRA
from app.models.people.perf.monthly_review import MonthlyReview
from app.models.people.perf.performance_contract import PerformanceContract
from app.models.people.perf.pip import PerformanceImprovementPlan
from app.models.people.perf.pms_governance import (
    InstitutionalGovernanceAction,
    PMSGovernanceGrievance,
    PMSStakeholderFeedback,
)
from app.models.people.perf.pms_enums import (
    AppealDecision,
    AppealStatus,
    CommitteeDecision,
    ConfirmationRecommendation,
    ContractStatus,
    ContractType,
    InstitutionalPerfStatus,
    InstitutionType,
    MonthlyReviewStatus,
    OutcomeActionStatus,
    OutcomeActionType,
    PIPCauseCategory,
    PIPOutcome,
    PIPStatus,
)
from app.models.people.perf.scorecard import Scorecard, ScorecardItem
from app.models.people.perf.strategic_objective import StrategicObjective
from app.models.people.perf.weekly_meeting_report import (
    MeetingActionStatus,
    MeetingAttendanceStatus,
    MeetingParticipantSource,
    ReportEmailStatus,
    WeeklyMeetingActionItem,
    WeeklyMeetingParticipant,
    WeeklyMeetingReport,
    WeeklyMeetingReportStatus,
)

__all__ = [
    # Existing
    "AppraisalCycle",
    "AppraisalCycleStatus",
    "AppraisalTemplate",
    "AppraisalTemplateKRA",
    "AppraisalTemplateProfile",
    "KRA",
    "KPI",
    "KPIStatus",
    "Appraisal",
    "AppraisalStatus",
    "AppraisalKRAScore",
    "AppraisalFeedback",
    "Scorecard",
    "ScorecardItem",
    # New PMS models
    "AppraisalAppeal",
    "AppraisalOutcomeAction",
    "CompetencyAssessment",
    "ContractAmendmentWorkflow",
    "DepartmentPerformanceTemplate",
    "InstitutionalCriteriaTemplate",
    "InstitutionalPerformance",
    "MonthlyReview",
    "PerformanceContract",
    "PerformanceImprovementPlan",
    "InstitutionalGovernanceAction",
    "PMSGovernanceGrievance",
    "PMSStakeholderFeedback",
    "StrategicObjective",
    # PMS enums
    "AppealDecision",
    "AppealStatus",
    "CommitteeDecision",
    "ConfirmationRecommendation",
    "ContractStatus",
    "ContractType",
    "InstitutionalPerfStatus",
    "InstitutionType",
    "MonthlyReviewStatus",
    "OutcomeActionStatus",
    "OutcomeActionType",
    "PIPCauseCategory",
    "PIPOutcome",
    "PIPStatus",
    "WeeklyMeetingReport",
    "WeeklyMeetingParticipant",
    "WeeklyMeetingActionItem",
    "WeeklyMeetingReportStatus",
    "MeetingAttendanceStatus",
    "MeetingParticipantSource",
    "MeetingActionStatus",
    "ReportEmailStatus",
]
