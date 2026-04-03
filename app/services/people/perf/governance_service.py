"""
PMS Governance Service.

Implements institutional workflow stage transitions, governance action logs,
grievance tracking, and stakeholder feedback capture.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.perf.appraisal import Appraisal
from app.models.people.perf.appraisal_cycle import AppraisalCycle
from app.models.people.perf.institutional_performance import InstitutionalPerformance
from app.models.people.perf.pms_enums import InstitutionalPerfStatus
from app.models.people.perf.pms_governance import (
    InstitutionalGovernanceAction,
    PMSGovernanceGrievance,
    PMSStakeholderFeedback,
)
from app.services.common import PaginatedResult, PaginationParams, paginate
from app.services.people.common import calculate_workdays
from app.services.people.perf.performance_policy import GOVERNMENT_PMS_POLICY
from app.services.people.perf.performance_mode_policy import enforce_pms_write_mode

logger = logging.getLogger(__name__)

WORKFLOW_STAGES = tuple(GOVERNMENT_PMS_POLICY.governance_stages)

_ROLE_ALIASES: dict[str, str] = dict(GOVERNMENT_PMS_POLICY.governance_role_aliases)

_STAGE_ROLE_OWNERS: dict[str, set[str]] = {
    stage: set(roles)
    for stage, roles in GOVERNMENT_PMS_POLICY.governance_stage_role_owners.items()
}

_STAGE_ACTION_TYPES: dict[str, str] = dict(
    GOVERNMENT_PMS_POLICY.governance_stage_action_types
)

_ALLOWED_STAGE_TRANSITIONS: dict[str, set[str]] = {
    stage: set(next_stages)
    for stage, next_stages in GOVERNMENT_PMS_POLICY.governance_stage_transitions.items()
}


class GovernanceServiceError(Exception):
    """Base error for governance services."""


class GovernanceValidationError(GovernanceServiceError):
    """Validation/business-rule failure."""


class GovernanceNotFoundError(GovernanceServiceError):
    """Record not found."""


class PMSGovernanceService:
    """Service for PMS governance operations."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._policy = GOVERNMENT_PMS_POLICY

    def _ensure_pms_write_mode(self, org_id: UUID) -> None:
        try:
            enforce_pms_write_mode(self.db, org_id)
        except ValueError as exc:
            raise GovernanceValidationError(str(exc)) from exc

    def _get_inst_or_404(
        self, org_id: UUID, inst_perf_id: UUID
    ) -> InstitutionalPerformance:
        record = self.db.scalar(
            select(InstitutionalPerformance).where(
                InstitutionalPerformance.organization_id == org_id,
                InstitutionalPerformance.inst_perf_id == inst_perf_id,
            )
        )
        if record is None:
            raise GovernanceNotFoundError(
                f"Institutional performance record {inst_perf_id} not found"
            )
        return record

    def _log_action(
        self,
        org_id: UUID,
        *,
        inst_perf_id: UUID,
        actor_employee_id: UUID | None,
        actor_role: str,
        action_type: str,
        from_stage: str | None = None,
        to_stage: str | None = None,
        comment: str | None = None,
    ) -> InstitutionalGovernanceAction:
        action = InstitutionalGovernanceAction(
            organization_id=org_id,
            inst_perf_id=inst_perf_id,
            actor_employee_id=actor_employee_id,
            actor_role=actor_role,
            action_type=action_type,
            from_stage=from_stage,
            to_stage=to_stage,
            comment=comment,
        )
        self.db.add(action)
        self.db.flush()
        return action

    @staticmethod
    def _normalize_actor_role(actor_role: str) -> str:
        normalized = actor_role.strip().upper()
        return _ROLE_ALIASES.get(normalized, normalized)

    def _ensure_stage_role_allowed(self, *, target_stage: str, actor_role: str) -> str:
        normalized = self._normalize_actor_role(actor_role)
        allowed = _STAGE_ROLE_OWNERS.get(target_stage)
        if not allowed:
            return normalized
        if normalized not in allowed:
            allowed_text = ", ".join(sorted(allowed))
            raise GovernanceValidationError(
                f"Role {normalized} cannot transition to {target_stage}; "
                f"allowed roles: {allowed_text}"
            )
        return normalized

    @staticmethod
    def _map_status_for_stage(stage: str) -> InstitutionalPerfStatus:
        if stage in {"INTERNAL_REVIEW", "CENTRAL_REVIEW"}:
            return InstitutionalPerfStatus.UNDER_REVIEW
        if stage == "APPROVED":
            return InstitutionalPerfStatus.APPRAISED
        if stage == "FINAL_SIGNOFF":
            return InstitutionalPerfStatus.COMPLETED
        return InstitutionalPerfStatus.DRAFT

    def assign_governance_roles(
        self,
        org_id: UUID,
        *,
        inst_perf_id: UUID,
        actor_employee_id: UUID | None,
        actor_role: str,
        owner_id: UUID | None,
        reviewer_id: UUID | None,
        approver_id: UUID | None,
        note: str | None = None,
    ) -> InstitutionalPerformance:
        self._ensure_pms_write_mode(org_id)
        normalized_role = self._normalize_actor_role(actor_role)
        if normalized_role not in self._policy.governance_assign_roles_allowed:
            raise GovernanceValidationError(
                "Only MDA_HRM or OHCSF_PMD can assign institutional governance roles"
            )
        record = self._get_inst_or_404(org_id, inst_perf_id)
        record.owner_id = owner_id
        record.reviewer_id = reviewer_id
        record.approver_id = approver_id
        if note:
            record.workflow_note = note

        self._log_action(
            org_id,
            inst_perf_id=record.inst_perf_id,
            actor_employee_id=actor_employee_id,
            actor_role=normalized_role,
            action_type=self._policy.governance_action_types.get(
                "role_assignment", "OHCSF_GOVERNANCE_ROLE_ASSIGNMENT"
            ),
            from_stage=record.workflow_stage,
            to_stage=record.workflow_stage,
            comment=note,
        )
        logger.info(
            "Assigned governance roles for institutional record %s", inst_perf_id
        )
        return record

    def transition_stage(
        self,
        org_id: UUID,
        *,
        inst_perf_id: UUID,
        target_stage: str,
        actor_employee_id: UUID | None,
        actor_role: str,
        note: str | None = None,
    ) -> InstitutionalPerformance:
        self._ensure_pms_write_mode(org_id)
        if target_stage not in WORKFLOW_STAGES:
            raise GovernanceValidationError(f"Invalid target stage: {target_stage}")

        record = self._get_inst_or_404(org_id, inst_perf_id)
        current_stage = record.workflow_stage or "DRAFT"

        allowed = _ALLOWED_STAGE_TRANSITIONS.get(current_stage, set())
        if target_stage not in allowed:
            raise GovernanceValidationError(
                f"Cannot transition workflow from {current_stage} to {target_stage}"
            )
        normalized_role = self._ensure_stage_role_allowed(
            target_stage=target_stage,
            actor_role=actor_role,
        )
        action_type = _STAGE_ACTION_TYPES.get(target_stage, "WORKFLOW_STAGE_TRANSITION")

        record.workflow_stage = target_stage
        record.status = self._map_status_for_stage(target_stage)
        record.workflow_note = note
        today = date.today()
        if target_stage == "INTERNAL_REVIEW":
            record.submitted_for_review_date = today
        elif target_stage == "CENTRAL_REVIEW":
            record.central_review_date = today
        elif target_stage == "APPROVED":
            record.approved_date = today
        elif target_stage == "RETURNED":
            record.returned_date = today
        elif target_stage == "FINAL_SIGNOFF":
            record.final_signoff_date = today

        self._log_action(
            org_id,
            inst_perf_id=record.inst_perf_id,
            actor_employee_id=actor_employee_id,
            actor_role=normalized_role,
            action_type=action_type,
            from_stage=current_stage,
            to_stage=target_stage,
            comment=note,
        )
        self.db.flush()
        logger.info(
            "Transitioned institutional record %s from %s to %s",
            inst_perf_id,
            current_stage,
            target_stage,
        )
        return record

    def list_governance_actions(
        self,
        org_id: UUID,
        *,
        inst_perf_id: UUID,
    ) -> list[InstitutionalGovernanceAction]:
        return list(
            self.db.scalars(
                select(InstitutionalGovernanceAction)
                .where(
                    InstitutionalGovernanceAction.organization_id == org_id,
                    InstitutionalGovernanceAction.inst_perf_id == inst_perf_id,
                )
                .order_by(InstitutionalGovernanceAction.created_at.desc())
            ).all()
        )

    def list_grievances(
        self,
        org_id: UUID,
        *,
        status: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[PMSGovernanceGrievance]:
        stmt = (
            select(PMSGovernanceGrievance)
            .where(PMSGovernanceGrievance.organization_id == org_id)
            .order_by(PMSGovernanceGrievance.created_at.desc())
        )
        if status:
            stmt = stmt.where(PMSGovernanceGrievance.status == status)
        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=PMSGovernanceGrievance.grievance_id,
        )

    def get_grievance(self, org_id: UUID, grievance_id: UUID) -> PMSGovernanceGrievance:
        grievance = self.db.scalar(
            select(PMSGovernanceGrievance).where(
                PMSGovernanceGrievance.organization_id == org_id,
                PMSGovernanceGrievance.grievance_id == grievance_id,
            )
        )
        if grievance is None:
            raise GovernanceNotFoundError(f"Grievance {grievance_id} not found")
        return grievance

    def create_grievance(
        self,
        org_id: UUID,
        *,
        raised_by_employee_id: UUID,
        title: str,
        description: str,
        channel: str = "INTERNAL",
        appraisal_id: UUID | None = None,
        inst_perf_id: UUID | None = None,
    ) -> PMSGovernanceGrievance:
        self._ensure_pms_write_mode(org_id)
        raised_on = date.today()
        due_date: date | None = None
        committee_level = self._policy.grievance_default_committee_level

        if appraisal_id is not None:
            appraisal = self.db.scalar(
                select(Appraisal).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.appraisal_id == appraisal_id,
                )
            )
            if appraisal is None:
                raise GovernanceValidationError("Referenced appraisal was not found")
            if appraisal.completed_on is None:
                raise GovernanceValidationError(
                    "Cannot file appraisal grievance before appraisal completion"
                )

            filing_days_elapsed = calculate_workdays(appraisal.completed_on, raised_on)
            if filing_days_elapsed > self._policy.grievance_filing_window_workdays:
                raise GovernanceValidationError(
                    "Appraisal grievance must be filed within "
                    f"{self._policy.grievance_filing_window_workdays} working days "
                    f"of completion (elapsed: {filing_days_elapsed})"
                )

            cycle = self.db.scalar(
                select(AppraisalCycle).where(
                    AppraisalCycle.organization_id == org_id,
                    AppraisalCycle.cycle_id == appraisal.cycle_id,
                )
            )
            if cycle is not None and cycle.end_date is not None:
                due_date = date(
                    cycle.end_date.year + 1,
                    self._policy.resolution_deadline_month,
                    self._policy.resolution_deadline_day,
                )
            else:
                due_date = date(
                    raised_on.year + 1,
                    self._policy.resolution_deadline_month,
                    self._policy.resolution_deadline_day,
                )

        grievance = PMSGovernanceGrievance(
            organization_id=org_id,
            raised_by_employee_id=raised_by_employee_id,
            title=title,
            description=description,
            channel=channel,
            status="OPEN",
            committee_level=committee_level,
            appraisal_id=appraisal_id,
            inst_perf_id=inst_perf_id,
            raised_date=raised_on,
            due_date=due_date,
        )
        self.db.add(grievance)
        self.db.flush()
        logger.info("Created PMS grievance %s", grievance.grievance_id)
        return grievance

    def assign_grievance(
        self,
        org_id: UUID,
        *,
        grievance_id: UUID,
        assigned_to_employee_id: UUID,
        due_date: date | None = None,
    ) -> PMSGovernanceGrievance:
        self._ensure_pms_write_mode(org_id)
        grievance = self.get_grievance(org_id, grievance_id)
        grievance.assigned_to_employee_id = assigned_to_employee_id
        if due_date is not None:
            grievance.due_date = due_date
        if grievance.status == "OPEN":
            grievance.status = "UNDER_REVIEW"
        self.db.flush()
        logger.info(
            "Assigned grievance %s to employee %s",
            grievance_id,
            assigned_to_employee_id,
        )
        return grievance

    def resolve_grievance(
        self,
        org_id: UUID,
        *,
        grievance_id: UUID,
        resolution_notes: str,
    ) -> PMSGovernanceGrievance:
        self._ensure_pms_write_mode(org_id)
        grievance = self.get_grievance(org_id, grievance_id)
        if not resolution_notes.strip():
            raise GovernanceValidationError("Resolution notes are required")
        grievance.status = "RESOLVED"
        grievance.resolution_notes = resolution_notes.strip()
        grievance.resolved_date = date.today()
        self.db.flush()
        logger.info("Resolved grievance %s", grievance_id)
        return grievance

    def escalate_grievance_to_fcsc(
        self,
        org_id: UUID,
        *,
        grievance_id: UUID,
        escalation_notes: str | None = None,
    ) -> PMSGovernanceGrievance:
        self._ensure_pms_write_mode(org_id)
        grievance = self.get_grievance(org_id, grievance_id)
        grievance.escalated_to_fcsc = True
        grievance.escalated_date = date.today()
        grievance.status = "ESCALATED"
        grievance.committee_level = self._policy.grievance_escalation_committee_level
        if escalation_notes:
            grievance.resolution_notes = escalation_notes.strip()
        if grievance.inst_perf_id is not None:
            self._log_action(
                org_id,
                inst_perf_id=grievance.inst_perf_id,
                actor_employee_id=None,
                actor_role=self._policy.governance_fcsc_actor_role,
                action_type=self._policy.governance_action_types.get(
                    "grievance_escalation_fcsc", "FCSC_GRIEVANCE_ESCALATION"
                ),
                comment=escalation_notes,
            )
        self.db.flush()
        logger.info("Escalated grievance %s to FCSC", grievance_id)
        return grievance

    def get_overdue_grievances(self, org_id: UUID) -> list[PMSGovernanceGrievance]:
        """Unresolved grievances past end-Feb following appraisal/cycle year."""
        unresolved = list(
            self.db.scalars(
                select(PMSGovernanceGrievance).where(
                    PMSGovernanceGrievance.organization_id == org_id,
                    PMSGovernanceGrievance.status.in_(
                        ["OPEN", "UNDER_REVIEW", "ESCALATED"]
                    ),
                )
            ).all()
        )

        overdue: list[PMSGovernanceGrievance] = []
        today = date.today()
        for grievance in unresolved:
            deadline = self._grievance_resolution_deadline(org_id, grievance)
            if today > deadline:
                overdue.append(grievance)
        return overdue

    def _grievance_resolution_deadline(
        self,
        org_id: UUID,
        grievance: PMSGovernanceGrievance,
    ) -> date:
        cycle_year: int | None = None
        if grievance.appraisal_id is not None:
            appraisal = self.db.scalar(
                select(Appraisal).where(
                    Appraisal.organization_id == org_id,
                    Appraisal.appraisal_id == grievance.appraisal_id,
                )
            )
            if appraisal is not None:
                cycle = self.db.scalar(
                    select(AppraisalCycle).where(
                        AppraisalCycle.organization_id == org_id,
                        AppraisalCycle.cycle_id == appraisal.cycle_id,
                    )
                )
                if cycle is not None and cycle.end_date is not None:
                    cycle_year = cycle.end_date.year

        base_year = cycle_year if cycle_year is not None else grievance.raised_date.year
        return date(
            base_year + 1,
            self._policy.resolution_deadline_month,
            self._policy.resolution_deadline_day,
        )

    def list_stakeholder_feedback(
        self,
        org_id: UUID,
        *,
        status: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[PMSStakeholderFeedback]:
        stmt = (
            select(PMSStakeholderFeedback)
            .where(PMSStakeholderFeedback.organization_id == org_id)
            .order_by(PMSStakeholderFeedback.created_at.desc())
        )
        if status:
            stmt = stmt.where(PMSStakeholderFeedback.status == status)
        return paginate(
            self.db,
            stmt,
            pagination,
            count_column=PMSStakeholderFeedback.feedback_id,
        )

    def create_stakeholder_feedback(
        self,
        org_id: UUID,
        *,
        title: str,
        feedback_text: str,
        source_type: str = "SERVICOM",
        channel: str = "PORTAL",
        submitted_by_name: str | None = None,
        submitted_by_contact: str | None = None,
        inst_perf_id: UUID | None = None,
    ) -> PMSStakeholderFeedback:
        self._ensure_pms_write_mode(org_id)
        normalized_source = source_type.strip().upper()
        if normalized_source not in set(self._policy.stakeholder_allowed_sources):
            raise GovernanceValidationError(
                "source_type must be one of SERVICOM, CITIZEN, or STAKEHOLDER"
            )
        feedback = PMSStakeholderFeedback(
            organization_id=org_id,
            title=title,
            feedback_text=feedback_text,
            source_type=normalized_source,
            channel=channel,
            submitted_by_name=submitted_by_name,
            submitted_by_contact=submitted_by_contact,
            inst_perf_id=inst_perf_id,
            status="RECEIVED",
            received_date=date.today(),
        )
        self.db.add(feedback)
        self.db.flush()
        if inst_perf_id is not None:
            self._log_action(
                org_id,
                inst_perf_id=inst_perf_id,
                actor_employee_id=None,
                actor_role=self._policy.governance_servicom_actor_role,
                action_type=self._policy.governance_action_types.get(
                    "stakeholder_feedback_captured",
                    "SERVICOM_STAKEHOLDER_FEEDBACK_CAPTURED",
                ),
                comment=title,
            )
        logger.info("Created stakeholder feedback %s", feedback.feedback_id)
        return feedback

    def governance_compliance_summary(self, org_id: UUID) -> dict[str, int]:
        inst_records = list(
            self.db.scalars(
                select(InstitutionalPerformance).where(
                    InstitutionalPerformance.organization_id == org_id
                )
            ).all()
        )
        overdue_approvals = 0
        for record in inst_records:
            if (
                record.workflow_stage == "CENTRAL_REVIEW"
                and record.central_review_date
                and (date.today() - record.central_review_date).days > 14
            ):
                overdue_approvals += 1

        # Portable count query.
        grievance_rows = list(
            self.db.scalars(
                select(PMSGovernanceGrievance.grievance_id).where(
                    PMSGovernanceGrievance.organization_id == org_id,
                    PMSGovernanceGrievance.status.in_(["OPEN", "UNDER_REVIEW"]),
                )
            ).all()
        )
        open_grievances = len(grievance_rows)
        escalated_grievances = len(
            list(
                self.db.scalars(
                    select(PMSGovernanceGrievance.grievance_id).where(
                        PMSGovernanceGrievance.organization_id == org_id,
                        PMSGovernanceGrievance.escalated_to_fcsc.is_(True),
                    )
                ).all()
            )
        )
        overdue_grievances = len(self.get_overdue_grievances(org_id))

        return {
            "institutional_records": len(inst_records),
            "overdue_approvals": overdue_approvals,
            "open_grievances": open_grievances,
            "overdue_grievances": overdue_grievances,
            "escalated_grievances": escalated_grievances,
        }
