"""
PMS Dispute SLA Service.

Automates end-of-February dispute SLA enforcement and deadline watchlists
for appeals, grievances, and PIPs.
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.people.perf.appraisal import Appraisal
from app.models.people.perf.appraisal_appeal import AppraisalAppeal
from app.models.people.perf.appraisal_cycle import AppraisalCycle
from app.models.people.perf.pip import PerformanceImprovementPlan
from app.models.people.perf.pms_enums import AppealStatus, PIPStatus
from app.models.people.perf.pms_governance import PMSGovernanceGrievance
from app.services.people.perf.governance_service import PMSGovernanceService

logger = logging.getLogger(__name__)


class PMSDisputeSLAService:
    """Service for automated PMS dispute SLA enforcement and reminders."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _appeal_deadline(self, appeal: AppraisalAppeal) -> date:
        cycle_year: int | None = None
        appraisal = self.db.scalar(
            select(Appraisal).where(
                Appraisal.organization_id == appeal.organization_id,
                Appraisal.appraisal_id == appeal.appraisal_id,
            )
        )
        if appraisal is not None:
            cycle = self.db.scalar(
                select(AppraisalCycle).where(
                    AppraisalCycle.organization_id == appeal.organization_id,
                    AppraisalCycle.cycle_id == appraisal.cycle_id,
                )
            )
            if cycle is not None and cycle.end_date is not None:
                cycle_year = cycle.end_date.year

        base_year = cycle_year if cycle_year is not None else appeal.filed_date.year
        return date(base_year + 1, 2, 28)

    def enforce_overdue_appeals(self, today: date | None = None) -> dict:
        """Escalate overdue unresolved appeals to committee queue."""
        today = today or date.today()
        unresolved = list(
            self.db.scalars(
                select(AppraisalAppeal).where(
                    AppraisalAppeal.status.notin_(
                        [AppealStatus.RESOLVED, AppealStatus.DISMISSED]
                    )
                )
            ).all()
        )

        auto_referred = 0
        already_referred = 0
        for appeal in unresolved:
            if today <= self._appeal_deadline(appeal):
                continue

            if appeal.status in {AppealStatus.FILED, AppealStatus.UNDER_MEDIATION}:
                appeal.status = AppealStatus.REFERRED_TO_COMMITTEE
                if appeal.committee_referral_date is None:
                    appeal.committee_referral_date = today
                note = "Auto-referred by SLA job after end-Feb deadline breach."
                existing = (appeal.committee_notes or "").strip()
                appeal.committee_notes = (
                    f"{existing}\n{note}".strip() if existing else note
                )
                auto_referred += 1
            elif appeal.status == AppealStatus.REFERRED_TO_COMMITTEE:
                already_referred += 1

        self.db.flush()
        return {
            "overdue_total": auto_referred + already_referred,
            "auto_referred": auto_referred,
            "already_referred": already_referred,
        }

    def enforce_overdue_grievances(self, today: date | None = None) -> dict:
        """Escalate overdue unresolved grievances to FCSC."""
        _ = today or date.today()
        gov = PMSGovernanceService(self.db)
        unresolved = list(
            self.db.scalars(
                select(PMSGovernanceGrievance).where(
                    PMSGovernanceGrievance.status.in_(
                        ["OPEN", "UNDER_REVIEW", "ESCALATED"]
                    )
                )
            ).all()
        )
        org_ids: set[UUID] = {g.organization_id for g in unresolved}
        overdue_ids: set[UUID] = set()
        for org_id in org_ids:
            overdue_ids.update(
                {
                    g.grievance_id
                    for g in gov.get_overdue_grievances(org_id)
                }
            )
        overdue = [g for g in unresolved if g.grievance_id in overdue_ids]

        auto_escalated = 0
        already_escalated = 0
        for grievance in overdue:
            if grievance.escalated_to_fcsc:
                already_escalated += 1
                continue
            gov.escalate_grievance_to_fcsc(
                grievance.organization_id,
                grievance_id=grievance.grievance_id,
                escalation_notes=(
                    "Auto-escalated by SLA job after end-Feb resolution deadline breach."
                ),
            )
            auto_escalated += 1

        self.db.flush()
        return {
            "overdue_total": len(overdue),
            "auto_escalated": auto_escalated,
            "already_escalated": already_escalated,
        }

    @staticmethod
    def _pip_effective_end_date(pip: PerformanceImprovementPlan) -> date:
        if pip.extension_granted and pip.extension_end_date is not None:
            return pip.extension_end_date
        return pip.end_date

    def enforce_overdue_pips(self, today: date | None = None) -> dict:
        """Escalate overdue active/extended PIPs to committee."""
        today = today or date.today()
        active = list(
            self.db.scalars(
                select(PerformanceImprovementPlan).where(
                    PerformanceImprovementPlan.status.in_(
                        [PIPStatus.ACTIVE, PIPStatus.EXTENDED, PIPStatus.UNDER_REVIEW]
                    )
                )
            ).all()
        )

        auto_escalated = 0
        for pip in active:
            if self._pip_effective_end_date(pip) >= today:
                continue
            pip.status = PIPStatus.ESCALATED
            if pip.committee_referral_date is None:
                pip.committee_referral_date = today
            if not pip.committee_decision:
                pip.committee_decision = (
                    "AUTO_ESCALATED_SLA: PIP period elapsed without closure."
                )
            if not pip.escalation_action:
                pip.escalation_action = "DISCIPLINARY_REVIEW"
            auto_escalated += 1

        self.db.flush()
        return {
            "overdue_total": auto_escalated,
            "auto_escalated": auto_escalated,
        }

    def enforce_all_overdue(self, today: date | None = None) -> dict:
        """Run SLA enforcement across appeals, grievances, and PIPs."""
        today = today or date.today()
        appeals = self.enforce_overdue_appeals(today=today)
        grievances = self.enforce_overdue_grievances(today=today)
        pips = self.enforce_overdue_pips(today=today)
        return {"appeals": appeals, "grievances": grievances, "pips": pips}

    def collect_upcoming_deadline_reminders(
        self, *, days_ahead: int = 7, today: date | None = None
    ) -> dict:
        """Collect upcoming deadline watchlist for reminder jobs."""
        today = today or date.today()
        horizon = date.fromordinal(today.toordinal() + days_ahead)

        grievance_items: list[dict] = []
        unresolved_grievances = list(
            self.db.scalars(
                select(PMSGovernanceGrievance).where(
                    PMSGovernanceGrievance.status.in_(
                        ["OPEN", "UNDER_REVIEW", "ESCALATED"]
                    ),
                    PMSGovernanceGrievance.due_date.isnot(None),
                )
            ).all()
        )
        for grievance in unresolved_grievances:
            if grievance.due_date is None:
                continue
            if today <= grievance.due_date <= horizon:
                grievance_items.append(
                    {
                        "grievance_id": str(grievance.grievance_id),
                        "organization_id": str(grievance.organization_id),
                        "due_date": grievance.due_date.isoformat(),
                        "days_remaining": (grievance.due_date - today).days,
                    }
                )

        pip_items: list[dict] = []
        unresolved_pips = list(
            self.db.scalars(
                select(PerformanceImprovementPlan).where(
                    PerformanceImprovementPlan.status.in_(
                        [PIPStatus.ACTIVE, PIPStatus.EXTENDED, PIPStatus.UNDER_REVIEW]
                    )
                )
            ).all()
        )
        for pip in unresolved_pips:
            due = self._pip_effective_end_date(pip)
            if today <= due <= horizon:
                pip_items.append(
                    {
                        "pip_id": str(pip.pip_id),
                        "organization_id": str(pip.organization_id),
                        "due_date": due.isoformat(),
                        "days_remaining": (due - today).days,
                    }
                )

        logger.info(
            "Collected PMS dispute reminders: grievances=%d pips=%d horizon=%d",
            len(grievance_items),
            len(pip_items),
            days_ahead,
        )
        return {
            "days_ahead": days_ahead,
            "grievances": grievance_items,
            "pips": pip_items,
        }
