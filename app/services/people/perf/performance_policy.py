"""Policy profiles for performance and PMS behavior (Phase 6.5)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from app.models.people.perf.appraisal import AppraisalStatus


class PerformancePolicyError(ValueError):
    """Raised when a policy profile is missing or invalid."""


@dataclass(frozen=True)
class ObjectivePolicy:
    min_count: int
    max_count: int
    required_total_weight: int


@dataclass(frozen=True)
class CompetencyPolicy:
    required_count: int
    required_development_focus_count: int
    rating_min: int
    rating_max: int
    evidence_required: bool = True


@dataclass(frozen=True)
class AppraisalWeightPolicy:
    objectives_pct: Decimal
    process_pct: Decimal
    competencies_pct: Decimal


@dataclass(frozen=True)
class RatingScaleBand:
    rating: int
    label: str
    min_pct: Decimal


@dataclass(frozen=True)
class PerformancePolicyProfile:
    name: str
    objective: ObjectivePolicy
    competency: CompetencyPolicy
    appraisal_weights: AppraisalWeightPolicy
    rating_scale: tuple[RatingScaleBand, ...]
    kpi_band_percentages: Mapping[str, Decimal]
    ohcsf_status_transitions: Mapping[AppraisalStatus, set[AppraisalStatus]]
    governance_stages: tuple[str, ...]
    governance_stage_transitions: Mapping[str, set[str]]
    governance_role_aliases: Mapping[str, str]
    governance_stage_role_owners: Mapping[str, set[str]]
    governance_stage_action_types: Mapping[str, str]
    governance_assign_roles_allowed: set[str]
    governance_fcsc_actor_role: str
    governance_servicom_actor_role: str
    appeal_filing_window_workdays: int
    resolution_deadline_month: int
    resolution_deadline_day: int
    grievance_filing_window_workdays: int
    active_cycle_status: str
    active_cycle_type: str
    mandatory_report_pack: tuple[tuple[str, str], ...]
    appeal_decisions_requiring_adjusted_rating: frozenset[str]
    committee_decisions_requiring_adjusted_rating: frozenset[str]
    governance_action_types: Mapping[str, str]
    grievance_default_committee_level: str
    grievance_escalation_committee_level: str
    stakeholder_allowed_sources: tuple[str, ...]
    ohcsf_seed_competencies: Mapping[str, tuple[tuple[str, str], ...]]
    ohcsf_institutional_weights: Mapping[str, tuple[tuple[str, int], ...]]
    shared_pagination_defaults: Mapping[str, int]
    ui_labels: Mapping[str, str]
    ui_messages: Mapping[str, str]


def _validate_policy(policy: PerformancePolicyProfile) -> None:
    if policy.objective.min_count <= 0:
        raise PerformancePolicyError(
            f"{policy.name}: objective.min_count must be > 0"
        )
    if policy.objective.max_count < policy.objective.min_count:
        raise PerformancePolicyError(
            f"{policy.name}: objective.max_count must be >= objective.min_count"
        )
    if policy.objective.required_total_weight <= 0:
        raise PerformancePolicyError(
            f"{policy.name}: objective.required_total_weight must be > 0"
        )
    if policy.competency.required_count < 0:
        raise PerformancePolicyError(
            f"{policy.name}: competency.required_count must be >= 0"
        )
    if (
        policy.competency.required_development_focus_count < 0
        or policy.competency.required_development_focus_count
        > policy.competency.required_count
    ):
        raise PerformancePolicyError(
            f"{policy.name}: competency.required_development_focus_count must be "
            "between 0 and competency.required_count"
        )
    if policy.competency.rating_min > policy.competency.rating_max:
        raise PerformancePolicyError(
            f"{policy.name}: competency.rating_min must be <= rating_max"
        )

    total_weight = (
        policy.appraisal_weights.objectives_pct
        + policy.appraisal_weights.process_pct
        + policy.appraisal_weights.competencies_pct
    )
    if total_weight != Decimal("1.00"):
        raise PerformancePolicyError(
            f"{policy.name}: appraisal weights must total 1.00 (got {total_weight})"
        )

    if not policy.rating_scale:
        raise PerformancePolicyError(f"{policy.name}: rating_scale cannot be empty")
    if sorted({band.rating for band in policy.rating_scale}, reverse=True) != [
        band.rating for band in policy.rating_scale
    ]:
        raise PerformancePolicyError(
            f"{policy.name}: rating_scale must be sorted by rating descending"
        )
    required_bands = {"outstanding", "excellent", "good", "fair", "poor"}
    if set(policy.kpi_band_percentages.keys()) != required_bands:
        raise PerformancePolicyError(
            f"{policy.name}: kpi_band_percentages must define {sorted(required_bands)}"
        )
    if policy.appeal_filing_window_workdays <= 0:
        raise PerformancePolicyError(
            f"{policy.name}: appeal_filing_window_workdays must be > 0"
        )
    if policy.grievance_filing_window_workdays <= 0:
        raise PerformancePolicyError(
            f"{policy.name}: grievance_filing_window_workdays must be > 0"
        )
    if policy.resolution_deadline_month < 1 or policy.resolution_deadline_month > 12:
        raise PerformancePolicyError(
            f"{policy.name}: resolution_deadline_month must be between 1 and 12"
        )
    if policy.resolution_deadline_day < 1 or policy.resolution_deadline_day > 31:
        raise PerformancePolicyError(
            f"{policy.name}: resolution_deadline_day must be between 1 and 31"
        )
    if not policy.active_cycle_status.strip() or not policy.active_cycle_type.strip():
        raise PerformancePolicyError(
            f"{policy.name}: active_cycle_status and active_cycle_type are required"
        )
    if not policy.mandatory_report_pack:
        raise PerformancePolicyError(
            f"{policy.name}: mandatory_report_pack cannot be empty"
        )
    if (
        policy.name == "GOVERNMENT_PMS"
        and not policy.appeal_decisions_requiring_adjusted_rating
    ):
        raise PerformancePolicyError(
            f"{policy.name}: appeal_decisions_requiring_adjusted_rating cannot be empty"
        )
    if (
        policy.name == "GOVERNMENT_PMS"
        and not policy.committee_decisions_requiring_adjusted_rating
    ):
        raise PerformancePolicyError(
            f"{policy.name}: committee_decisions_requiring_adjusted_rating cannot be empty"
        )
    if (
        policy.name == "GOVERNMENT_PMS"
        and not policy.ohcsf_seed_competencies
    ):
        raise PerformancePolicyError(
            f"{policy.name}: ohcsf_seed_competencies cannot be empty"
        )
    if (
        policy.name == "GOVERNMENT_PMS"
        and not policy.ohcsf_institutional_weights
    ):
        raise PerformancePolicyError(
            f"{policy.name}: ohcsf_institutional_weights cannot be empty"
        )
    if not policy.shared_pagination_defaults:
        raise PerformancePolicyError(
            f"{policy.name}: shared_pagination_defaults cannot be empty"
        )
    required_labels = {"performance_nav_label", "pms_nav_label"}
    if not required_labels.issubset(policy.ui_labels.keys()):
        raise PerformancePolicyError(
            f"{policy.name}: ui_labels must define {sorted(required_labels)}"
        )
    required_messages = {
        "mode_blocked_pms_write",
        "mode_blocked_private_write",
        "appraisal_progress_prefix",
    }
    if not required_messages.issubset(policy.ui_messages.keys()):
        raise PerformancePolicyError(
            f"{policy.name}: ui_messages must define {sorted(required_messages)}"
        )


GOVERNMENT_PMS_POLICY = PerformancePolicyProfile(
    name="GOVERNMENT_PMS",
    objective=ObjectivePolicy(min_count=3, max_count=7, required_total_weight=70),
    competency=CompetencyPolicy(
        required_count=5,
        required_development_focus_count=3,
        rating_min=1,
        rating_max=5,
        evidence_required=True,
    ),
    appraisal_weights=AppraisalWeightPolicy(
        objectives_pct=Decimal("0.70"),
        process_pct=Decimal("0.10"),
        competencies_pct=Decimal("0.20"),
    ),
    rating_scale=(
        RatingScaleBand(rating=5, label="Outstanding", min_pct=Decimal("90")),
        RatingScaleBand(rating=4, label="Excellent", min_pct=Decimal("80")),
        RatingScaleBand(rating=3, label="Good", min_pct=Decimal("70")),
        RatingScaleBand(rating=2, label="Fair", min_pct=Decimal("60")),
        RatingScaleBand(rating=1, label="Poor", min_pct=Decimal("0")),
    ),
    kpi_band_percentages=MappingProxyType(
        {
            "outstanding": Decimal("100"),
            "excellent": Decimal("90"),
            "good": Decimal("80"),
            "fair": Decimal("70"),
            "poor": Decimal("60"),
        }
    ),
    ohcsf_status_transitions=MappingProxyType(
        {
            AppraisalStatus.DRAFT: {
                AppraisalStatus.SELF_ASSESSMENT,
                AppraisalStatus.CANCELLED,
            },
            AppraisalStatus.SELF_ASSESSMENT: {
                AppraisalStatus.PENDING_REVIEW,
                AppraisalStatus.DRAFT,
            },
            AppraisalStatus.PENDING_REVIEW: {AppraisalStatus.UNDER_REVIEW},
            AppraisalStatus.UNDER_REVIEW: {
                AppraisalStatus.PENDING_COUNTERSIGN,
                AppraisalStatus.SELF_ASSESSMENT,
            },
            AppraisalStatus.PENDING_COUNTERSIGN: {AppraisalStatus.COUNTERSIGNED},
            AppraisalStatus.COUNTERSIGNED: {AppraisalStatus.PENDING_COMMITTEE},
            AppraisalStatus.PENDING_COMMITTEE: {AppraisalStatus.COMPLETED},
            AppraisalStatus.COMPLETED: set(),
            AppraisalStatus.CANCELLED: set(),
        }
    ),
    governance_stages=(
        "DRAFT",
        "INTERNAL_REVIEW",
        "CENTRAL_REVIEW",
        "APPROVED",
        "RETURNED",
        "FINAL_SIGNOFF",
    ),
    governance_stage_transitions=MappingProxyType(
        {
            "DRAFT": {"INTERNAL_REVIEW"},
            "INTERNAL_REVIEW": {"CENTRAL_REVIEW", "RETURNED"},
            "CENTRAL_REVIEW": {"APPROVED", "RETURNED"},
            "APPROVED": {"FINAL_SIGNOFF", "RETURNED"},
            "RETURNED": {"INTERNAL_REVIEW"},
            "FINAL_SIGNOFF": set(),
        }
    ),
    governance_role_aliases=MappingProxyType(
        {
            "HRM": "MDA_HRM",
            "OHCSF_PMS": "OHCSF_PMD",
        }
    ),
    governance_stage_role_owners=MappingProxyType(
        {
            "INTERNAL_REVIEW": {"MDA_PRS", "MDA_HRM"},
            "CENTRAL_REVIEW": {"FMFBNP"},
            "APPROVED": {"OHCSF_PMD"},
            "FINAL_SIGNOFF": {"CDCU_OSGF"},
            "RETURNED": {"FMFBNP", "OHCSF_PMD", "CDCU_OSGF"},
        }
    ),
    governance_stage_action_types=MappingProxyType(
        {
            "INTERNAL_REVIEW": "MDA_INTERNAL_SUBMISSION",
            "CENTRAL_REVIEW": "FMFBNP_CENTRAL_REVIEW",
            "APPROVED": "OHCSF_POLICY_APPROVAL",
            "FINAL_SIGNOFF": "CDCU_OSGF_FINAL_SIGNOFF",
            "RETURNED": "CENTRAL_RETURN_FOR_REWORK",
        }
    ),
    governance_assign_roles_allowed={"MDA_HRM", "OHCSF_PMD"},
    governance_fcsc_actor_role="FCSC_OMBUDSMAN",
    governance_servicom_actor_role="SERVICOM_NODAL",
    appeal_filing_window_workdays=5,
    resolution_deadline_month=2,
    resolution_deadline_day=28,
    grievance_filing_window_workdays=5,
    active_cycle_status="ACTIVE",
    active_cycle_type="ANNUAL",
    mandatory_report_pack=(
        ("rating-summary", "Rating Summary"),
        ("by-department", "Rating by Department"),
        ("by-grade", "Rating by Grade Level"),
        ("distribution", "Performance Distribution"),
        ("distribution-dept", "Distribution by Department"),
        ("distribution-grade", "Distribution by Grade"),
        ("top-performers", "Top Performers"),
        ("bottom-performers", "Bottom Performers"),
        ("development-needs", "Development Needs"),
        ("development-dept", "Development Needs by Department"),
        ("compliance", "Compliance Dashboard"),
    ),
    appeal_decisions_requiring_adjusted_rating=frozenset(
        {"UPHELD", "PARTIALLY_UPHELD"}
    ),
    committee_decisions_requiring_adjusted_rating=frozenset({"ADJUSTED"}),
    governance_action_types=MappingProxyType(
        {
            "role_assignment": "OHCSF_GOVERNANCE_ROLE_ASSIGNMENT",
            "grievance_escalation_fcsc": "FCSC_GRIEVANCE_ESCALATION",
            "stakeholder_feedback_captured": "SERVICOM_STAKEHOLDER_FEEDBACK_CAPTURED",
        }
    ),
    grievance_default_committee_level="HR",
    grievance_escalation_committee_level="FCSC",
    stakeholder_allowed_sources=("SERVICOM", "CITIZEN", "STAKEHOLDER"),
    ohcsf_seed_competencies=MappingProxyType(
        {
            "ETHICS_AND_VALUES": (
                ("OHCSF-COMMITMENT", "Commitment"),
                ("OHCSF-INTEGRITY", "Integrity"),
                ("OHCSF-INCLUSIVENESS", "Inclusiveness"),
                ("OHCSF-COURAGE", "Courage"),
            ),
            "PEOPLE": (
                ("OHCSF-COLLABORATING", "Collaborating & Partnering"),
                ("OHCSF-COMMUNICATION", "Effective Communication"),
                ("OHCSF-MNG-PEOPLE", "Managing & Developing People"),
            ),
            "EXECUTION": (
                ("OHCSF-DRIVE-RESULTS", "Drive for Results"),
                ("OHCSF-TRANSPARENCY", "Transparency and Accountability"),
                ("OHCSF-VALUE-MONEY", "Value for Money"),
            ),
            "VISION": (
                ("OHCSF-DECISION", "Effective Decision Making"),
                ("OHCSF-STRAT-THINK", "Strategic Thinking"),
                ("OHCSF-CHANGE-MGMT", "Embracing and Managing Change"),
            ),
            "EXPERTISE": (
                ("OHCSF-POLICY-MGMT", "Policy Management"),
                ("OHCSF-CITIZEN-FOCUS", "Citizen Focus"),
                ("OHCSF-INFO-RECORDS", "Information and Records Management"),
                ("OHCSF-TECHNOLOGY", "Adoption and Use of Technology"),
                ("OHCSF-SPECIALIST", "Specialist Competencies"),
            ),
        }
    ),
    ohcsf_institutional_weights=MappingProxyType(
        {
            "MINISTRY": (
                ("Government prioritized objectives", 25),
                ("MDA Operational Objectives", 25),
                ("Stakeholder Engagement", 10),
                ("Service Innovation and Improvement", 10),
                ("Automated Service Delivery", 10),
                ("Capacity Building & Talent Management", 5),
                ("Support for Service Delivery", 10),
                ("Staff Welfare", 5),
            ),
            "REGULATORY": (
                ("Government prioritized objectives", 25),
                ("MDA Operational Objectives", 25),
                ("Stakeholder Engagement", 10),
                ("Service Innovation and Improvement", 10),
                ("Automated Service Delivery", 10),
                ("Capacity Building & Talent Management", 5),
                ("Support for Service Delivery", 10),
                ("Staff Welfare", 5),
            ),
            "GENERAL_SERVICES": (
                ("Government prioritized objectives", 20),
                ("MDA Operational Objectives", 20),
                ("Stakeholder Engagement", 5),
                ("Service Innovation and Improvement", 20),
                ("Automated Service Delivery", 15),
                ("Capacity Building & Talent Management", 5),
                ("Support for Service Delivery", 10),
                ("Staff Welfare", 5),
            ),
            "INFRASTRUCTURE": (
                ("Government prioritized objectives", 25),
                ("MDA Operational Objectives", 20),
                ("Stakeholder Engagement", 5),
                ("Service Innovation and Improvement", 15),
                ("Automated Service Delivery", 15),
                ("Capacity Building & Talent Management", 5),
                ("Support for Service Delivery", 10),
                ("Staff Welfare", 5),
            ),
            "SECURITY": (
                ("Government prioritized objectives", 20),
                ("MDA Operational Objectives", 25),
                ("Stakeholder Engagement", 5),
                ("Service Innovation and Improvement", 10),
                ("Automated Service Delivery", 5),
                ("Capacity Building & Talent Management", 10),
                ("Support for Service Delivery", 20),
                ("Staff Welfare", 5),
            ),
            "GOVT_COMPANY": (
                ("Government prioritized objectives", 25),
                ("MDA Operational Objectives", 25),
                ("Stakeholder Engagement", 5),
                ("Service Innovation and Improvement", 10),
                ("Automated Service Delivery", 15),
                ("Capacity Building & Talent Management", 5),
                ("Support for Service Delivery", 10),
                ("Staff Welfare", 5),
            ),
        }
    ),
    shared_pagination_defaults=MappingProxyType(
        {
            "default_limit": 50,
            "list_page_size": 20,
            "picker_limit": 500,
        }
    ),
    ui_labels=MappingProxyType(
        {
            "performance_nav_label": "Performance (Private)",
            "pms_nav_label": "PMS (Government)",
        }
    ),
    ui_messages=MappingProxyType(
        {
            "mode_blocked_pms_write": (
                "PMS (Government) actions are blocked because this organization is "
                "in Private performance mode. Switch mode to Government PMS or "
                "Hybrid to use PMS features."
            ),
            "mode_blocked_private_write": (
                "Private Performance actions are blocked because this organization "
                "is in Government PMS mode. Switch mode to Private or Hybrid to "
                "use private performance features."
            ),
            "appraisal_progress_prefix": "Cannot progress appraisal",
        }
    ),
)

PRIVATE_POLICY = PerformancePolicyProfile(
    name="PRIVATE",
    objective=ObjectivePolicy(min_count=1, max_count=12, required_total_weight=100),
    competency=CompetencyPolicy(
        required_count=0,
        required_development_focus_count=0,
        rating_min=1,
        rating_max=5,
        evidence_required=False,
    ),
    appraisal_weights=AppraisalWeightPolicy(
        objectives_pct=Decimal("1.00"),
        process_pct=Decimal("0.00"),
        competencies_pct=Decimal("0.00"),
    ),
    rating_scale=(
        RatingScaleBand(rating=5, label="Outstanding", min_pct=Decimal("90")),
        RatingScaleBand(rating=4, label="Excellent", min_pct=Decimal("80")),
        RatingScaleBand(rating=3, label="Good", min_pct=Decimal("70")),
        RatingScaleBand(rating=2, label="Fair", min_pct=Decimal("60")),
        RatingScaleBand(rating=1, label="Poor", min_pct=Decimal("0")),
    ),
    kpi_band_percentages=MappingProxyType(
        {
            "outstanding": Decimal("100"),
            "excellent": Decimal("90"),
            "good": Decimal("80"),
            "fair": Decimal("70"),
            "poor": Decimal("60"),
        }
    ),
    ohcsf_status_transitions=MappingProxyType({}),
    governance_stages=(),
    governance_stage_transitions=MappingProxyType({}),
    governance_role_aliases=MappingProxyType({}),
    governance_stage_role_owners=MappingProxyType({}),
    governance_stage_action_types=MappingProxyType({}),
    governance_assign_roles_allowed=set(),
    governance_fcsc_actor_role="",
    governance_servicom_actor_role="",
    appeal_filing_window_workdays=5,
    resolution_deadline_month=2,
    resolution_deadline_day=28,
    grievance_filing_window_workdays=5,
    active_cycle_status="ACTIVE",
    active_cycle_type="ANNUAL",
    mandatory_report_pack=(("compliance", "Compliance Dashboard"),),
    appeal_decisions_requiring_adjusted_rating=frozenset(
        {"UPHELD", "PARTIALLY_UPHELD"}
    ),
    committee_decisions_requiring_adjusted_rating=frozenset({"ADJUSTED"}),
    governance_action_types=MappingProxyType(
        {
            "role_assignment": "OHCSF_GOVERNANCE_ROLE_ASSIGNMENT",
            "grievance_escalation_fcsc": "FCSC_GRIEVANCE_ESCALATION",
            "stakeholder_feedback_captured": "SERVICOM_STAKEHOLDER_FEEDBACK_CAPTURED",
        }
    ),
    grievance_default_committee_level="HR",
    grievance_escalation_committee_level="FCSC",
    stakeholder_allowed_sources=("SERVICOM", "CITIZEN", "STAKEHOLDER"),
    ohcsf_seed_competencies=MappingProxyType({}),
    ohcsf_institutional_weights=MappingProxyType({}),
    shared_pagination_defaults=MappingProxyType(
        {
            "default_limit": 50,
            "list_page_size": 20,
            "picker_limit": 500,
        }
    ),
    ui_labels=MappingProxyType(
        {
            "performance_nav_label": "Performance (Private)",
            "pms_nav_label": "PMS (Government)",
        }
    ),
    ui_messages=MappingProxyType(
        {
            "mode_blocked_pms_write": (
                "PMS (Government) actions are blocked because this organization is "
                "in Private performance mode. Switch mode to Government PMS or "
                "Hybrid to use PMS features."
            ),
            "mode_blocked_private_write": (
                "Private Performance actions are blocked because this organization "
                "is in Government PMS mode. Switch mode to Private or Hybrid to "
                "use private performance features."
            ),
            "appraisal_progress_prefix": "Cannot progress appraisal",
        }
    ),
)

_POLICIES: Mapping[str, PerformancePolicyProfile] = MappingProxyType(
    {
        GOVERNMENT_PMS_POLICY.name: GOVERNMENT_PMS_POLICY,
        PRIVATE_POLICY.name: PRIVATE_POLICY,
    }
)

for _profile in _POLICIES.values():
    _validate_policy(_profile)


def get_policy_profile(name: str) -> PerformancePolicyProfile:
    profile = _POLICIES.get((name or "").strip().upper())
    if profile is None:
        raise PerformancePolicyError(f"Unknown performance policy profile: {name}")
    return profile
