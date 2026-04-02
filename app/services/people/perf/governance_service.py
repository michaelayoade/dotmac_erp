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

logger = logging.getLogger(__name__)

WORKFLOW_STAGES = (
    "DRAFT",
    "INTERNAL_REVIEW",
    "CENTRAL_REVIEW",
    "APPROVED",
    "RETURNED",
    "FINAL_SIGNOFF",
)

ROLE_MDA_PRS = "MDA_PRS"
ROLE_MDA_HRM = "MDA_HRM"
ROLE_FMFBNP = "FMFBNP"
ROLE_OHCSF_PMD = "OHCSF_PMD"
ROLE_CDCU_OSGF = "CDCU_OSGF"
ROLE_FCSC_OMBUDSMAN = "FCSC_OMBUDSMAN"
ROLE_SERVICOM_NODAL = "SERVICOM_NODAL"

_ROLE_ALIASES: dict[str, str] = {
    "HRM": ROLE_MDA_HRM,
    "OHCSF_PMS": ROLE_OHCSF_PMD,
}

_STAGE_ROLE_OWNERS: dict[str, set[str]] = {
    "INTERNAL_REVIEW": {ROLE_MDA_PRS, ROLE_MDA_HRM},
    "CENTRAL_REVIEW": {ROLE_FMFBNP},
    "APPROVED": {ROLE_OHCSF_PMD},
    "FINAL_SIGNOFF": {ROLE_CDCU_OSGF},
    "RETURNED": {ROLE_FMFBNP, ROLE_OHCSF_PMD, ROLE_CDCU_OSGF},
}

_STAGE_ACTION_TYPES: dict[str, str] = {
    "INTERNAL_REVIEW": "MDA_INTERNAL_SUBMISSION",
    "CENTRAL_REVIEW": "FMFBNP_CENTRAL_REVIEW",
    "APPROVED": "OHCSF_POLICY_APPROVAL",
    "FINAL_SIGNOFF": "CDCU_OSGF_FINAL_SIGNOFF",
    "RETURNED": "CENTRAL_RETURN_FOR_REWORK",
}

_ALLOWED_STAGE_TRANSITIONS: dict[str, set[str]] = {
    "DRAFT": {"INTERNAL_REVIEW"},
    "INTERNAL_REVIEW": {"CENTRAL_REVIEW", "RETURNED"},
    "CENTRAL_REVIEW": {"APPROVED", "RETURNED"},
    "APPROVED": {"FINAL_SIGNOFF", "RETURNED"},
    "RETURNED": {"INTERNAL_REVIEW"},
    "FINAL_SIGNOFF": set(),
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
        normalized_role = self._normalize_actor_role(actor_role)
        if normalized_role not in {ROLE_MDA_HRM, ROLE_OHCSF_PMD}:
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
            action_type="OHCSF_GOVERNANCE_ROLE_ASSIGNMENT",
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
        raised_on = date.today()
        due_date: date | None = None
        committee_level = "HR"

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
            if filing_days_elapsed > 5:
                raise GovernanceValidationError(
                    "Appraisal grievance must be filed within 5 working days "
                    f"of completion (elapsed: {filing_days_elapsed})"
                )

            cycle = self.db.scalar(
                select(AppraisalCycle).where(
                    AppraisalCycle.organization_id == org_id,
                    AppraisalCycle.cycle_id == appraisal.cycle_id,
                )
            )
            if cycle is not None and cycle.end_date is not None:
                due_date = date(cycle.end_date.year + 1, 2, 28)
            else:
                due_date = date(raised_on.year + 1, 2, 28)

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
        grievance = self.get_grievance(org_id, grievance_id)
        grievance.escalated_to_fcsc = True
        grievance.escalated_date = date.today()
        grievance.status = "ESCALATED"
        grievance.committee_level = "FCSC"
        if escalation_notes:
            grievance.resolution_notes = escalation_notes.strip()
        if grievance.inst_perf_id is not None:
            self._log_action(
                org_id,
                inst_perf_id=grievance.inst_perf_id,
                actor_employee_id=None,
                actor_role=ROLE_FCSC_OMBUDSMAN,
                action_type="FCSC_GRIEVANCE_ESCALATION",
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
        return date(base_year + 1, 2, 28)

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
        normalized_source = source_type.strip().upper()
        if normalized_source not in {"SERVICOM", "CITIZEN", "STAKEHOLDER"}:
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
                actor_role=ROLE_SERVICOM_NODAL,
                action_type="SERVICOM_STAKEHOLDER_FEEDBACK_CAPTURED",
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
