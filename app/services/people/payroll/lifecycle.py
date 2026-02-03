"""
Payroll Lifecycle Orchestrator.

Centralizes state transition logic for salary slips and payroll runs.
This is the single authority for "can this transition happen?" and
"what events should be emitted after it does?"

State Machines:
    Slip: DRAFT → SUBMITTED → APPROVED → POSTED → PAID (CANCELLED from any)
    Run:  DRAFT → (PENDING) → SLIPS_CREATED → SUBMITTED → APPROVED → POSTED (CANCELLED from any)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Set, TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.people.payroll.salary_slip import SalarySlip, SalarySlipStatus, SalarySlipDeduction
from app.models.people.payroll.payroll_entry import PayrollEntry, PayrollEntryStatus
from app.services.common import coerce_uuid

if TYPE_CHECKING:
    from app.services.people.payroll.payroll_gl_adapter import PayrollPostingResult
from app.services.people.payroll.events import (
    PayrollEventDispatcher,
    SlipSubmitted,
    SlipApproved,
    SlipPosted,
    SlipPaid,
    SlipCancelled,
    SlipRejected,
    RunSubmitted,
    RunApproved,
    RunPosted,
    RunSlipsCreated,
    RunCancelled,
    payroll_dispatcher,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State Transition Definitions
# ---------------------------------------------------------------------------


SLIP_TRANSITIONS: dict[SalarySlipStatus, Set[SalarySlipStatus]] = {
    SalarySlipStatus.DRAFT: {SalarySlipStatus.SUBMITTED, SalarySlipStatus.CANCELLED},
    SalarySlipStatus.SUBMITTED: {SalarySlipStatus.APPROVED, SalarySlipStatus.DRAFT, SalarySlipStatus.CANCELLED},
    SalarySlipStatus.APPROVED: {SalarySlipStatus.POSTED, SalarySlipStatus.SUBMITTED, SalarySlipStatus.CANCELLED},
    SalarySlipStatus.POSTED: {SalarySlipStatus.PAID, SalarySlipStatus.CANCELLED},
    SalarySlipStatus.PAID: {SalarySlipStatus.CANCELLED},  # Very limited - requires reversal
    SalarySlipStatus.CANCELLED: set(),  # Terminal state
}


RUN_TRANSITIONS: dict[PayrollEntryStatus, Set[PayrollEntryStatus]] = {
    PayrollEntryStatus.DRAFT: {
        PayrollEntryStatus.PENDING,
        PayrollEntryStatus.SLIPS_CREATED,
        PayrollEntryStatus.CANCELLED,
    },
    PayrollEntryStatus.PENDING: {PayrollEntryStatus.SLIPS_CREATED, PayrollEntryStatus.CANCELLED},
    PayrollEntryStatus.SLIPS_CREATED: {PayrollEntryStatus.SUBMITTED, PayrollEntryStatus.CANCELLED},
    PayrollEntryStatus.SUBMITTED: {PayrollEntryStatus.APPROVED, PayrollEntryStatus.SLIPS_CREATED, PayrollEntryStatus.CANCELLED},
    PayrollEntryStatus.APPROVED: {PayrollEntryStatus.POSTED, PayrollEntryStatus.SUBMITTED, PayrollEntryStatus.CANCELLED},
    PayrollEntryStatus.POSTED: {PayrollEntryStatus.CANCELLED},  # Very limited - requires reversal
    PayrollEntryStatus.CANCELLED: set(),  # Terminal state
}


@dataclass
class TransitionResult:
    """Result of a lifecycle transition attempt."""

    success: bool
    previous_status: str
    new_status: str
    message: str


class PayrollLifecycleError(Exception):
    """Raised when a lifecycle transition is invalid."""

    def __init__(self, message: str, current_status: str, target_status: str):
        super().__init__(message)
        self.current_status = current_status
        self.target_status = target_status


# ---------------------------------------------------------------------------
# Lifecycle Orchestrator
# ---------------------------------------------------------------------------


class PayrollLifecycle:
    """
    Orchestrates payroll lifecycle transitions.

    This class is the single authority for state transitions. It validates
    transitions, updates status, and emits domain events for side effects.

    Usage:
        lifecycle = PayrollLifecycle(db, dispatcher)
        result = lifecycle.submit_slip(org_id, slip_id, user_id)
    """

    def __init__(
        self,
        db: Session,
        dispatcher: Optional[PayrollEventDispatcher] = None,
    ):
        self.db = db
        self.dispatcher = dispatcher or payroll_dispatcher

    # ---------------------------------------------------------------------------
    # Validation Helpers
    # ---------------------------------------------------------------------------

    def can_transition_slip(
        self,
        current: SalarySlipStatus,
        target: SalarySlipStatus,
    ) -> bool:
        """Check if a slip status transition is allowed."""
        allowed = SLIP_TRANSITIONS.get(current, set())
        return target in allowed

    def can_transition_run(
        self,
        current: PayrollEntryStatus,
        target: PayrollEntryStatus,
    ) -> bool:
        """Check if a run status transition is allowed."""
        allowed = RUN_TRANSITIONS.get(current, set())
        return target in allowed

    def validate_slip_transition(
        self,
        current: SalarySlipStatus,
        target: SalarySlipStatus,
    ) -> None:
        """Validate a slip transition or raise an error."""
        if not self.can_transition_slip(current, target):
            raise PayrollLifecycleError(
                f"Cannot transition slip from {current.value} to {target.value}",
                current.value,
                target.value,
            )

    def validate_run_transition(
        self,
        current: PayrollEntryStatus,
        target: PayrollEntryStatus,
    ) -> None:
        """Validate a run transition or raise an error."""
        if not self.can_transition_run(current, target):
            raise PayrollLifecycleError(
                f"Cannot transition run from {current.value} to {target.value}",
                current.value,
                target.value,
            )

    # ---------------------------------------------------------------------------
    # Salary Slip Transitions
    # ---------------------------------------------------------------------------

    def _get_slip(self, organization_id: UUID, slip_id: UUID) -> SalarySlip:
        """Get a salary slip or raise 404."""
        org_id = coerce_uuid(organization_id)
        s_id = coerce_uuid(slip_id)
        slip = self.db.get(SalarySlip, s_id)
        if not slip or slip.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Salary slip not found")
        return slip

    def _update_slip_status(
        self,
        slip: SalarySlip,
        new_status: SalarySlipStatus,
        changed_by_id: UUID,
    ) -> SalarySlipStatus:
        """Update slip status and tracking fields."""
        previous = slip.status
        slip.status = new_status
        slip.status_changed_at = datetime.now(timezone.utc)
        slip.status_changed_by_id = changed_by_id
        return previous

    def submit_slip(
        self,
        organization_id: UUID,
        slip_id: UUID,
        submitted_by_id: UUID,
    ) -> TransitionResult:
        """
        Submit a salary slip for approval.

        Transition: DRAFT → SUBMITTED
        """
        slip = self._get_slip(organization_id, slip_id)
        user_id = coerce_uuid(submitted_by_id)

        self.validate_slip_transition(slip.status, SalarySlipStatus.SUBMITTED)

        previous = self._update_slip_status(slip, SalarySlipStatus.SUBMITTED, user_id)
        self.db.flush()

        logger.info(
            "Slip %s submitted: %s → %s by user %s",
            slip.slip_number,
            previous.value,
            SalarySlipStatus.SUBMITTED.value,
            user_id,
        )

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import fire_workflow_event
            fire_workflow_event(
                db=self.db, organization_id=slip.organization_id,
                entity_type="SALARY_SLIP", entity_id=slip.slip_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": previous.value},
                new_values={"status": "SUBMITTED"}, user_id=user_id,
            )
        except Exception:
            pass

        # Commit before emitting event so handlers see committed state
        event_org_id = slip.organization_id
        event_slip_id = slip.slip_id
        event_slip_number = slip.slip_number
        self.db.commit()

        # Emit event
        self.dispatcher.dispatch(
            SlipSubmitted(
                organization_id=event_org_id,
                triggered_by_id=user_id,
                slip_id=event_slip_id,
                slip_number=event_slip_number,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=SalarySlipStatus.SUBMITTED.value,
            message=f"Slip {slip.slip_number} submitted for approval",
        )

    def approve_slip(
        self,
        organization_id: UUID,
        slip_id: UUID,
        approved_by_id: UUID,
    ) -> TransitionResult:
        """
        Approve a submitted salary slip.

        Transition: SUBMITTED → APPROVED
        Includes segregation of duties check.
        """
        slip = self._get_slip(organization_id, slip_id)
        user_id = coerce_uuid(approved_by_id)

        self.validate_slip_transition(slip.status, SalarySlipStatus.APPROVED)

        # Segregation of duties: creator cannot approve
        if slip.created_by_id == user_id:
            raise HTTPException(
                status_code=403,
                detail="Segregation of duties: creator cannot approve their own slip",
            )

        previous = self._update_slip_status(slip, SalarySlipStatus.APPROVED, user_id)
        self.db.flush()

        logger.info(
            "Slip %s approved: %s → %s by user %s (gross: %s, net: %s)",
            slip.slip_number,
            previous.value,
            SalarySlipStatus.APPROVED.value,
            user_id,
            slip.gross_pay,
            slip.net_pay,
        )

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import fire_workflow_event
            fire_workflow_event(
                db=self.db, organization_id=slip.organization_id,
                entity_type="SALARY_SLIP", entity_id=slip.slip_id,
                event="ON_APPROVAL",
                old_values={"status": previous.value},
                new_values={"status": "APPROVED"}, user_id=user_id,
            )
        except Exception:
            pass

        # Commit before emitting event so handlers see committed state
        event_org_id = slip.organization_id
        event_slip_id = slip.slip_id
        event_slip_number = slip.slip_number
        self.db.commit()

        # Emit event
        self.dispatcher.dispatch(
            SlipApproved(
                organization_id=event_org_id,
                triggered_by_id=user_id,
                slip_id=event_slip_id,
                slip_number=event_slip_number,
                approved_by_id=user_id,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=SalarySlipStatus.APPROVED.value,
            message=f"Slip {slip.slip_number} approved",
        )

    def post_slip_to_gl(
        self,
        organization_id: UUID,
        slip_id: UUID,
        posting_date: date,
        posted_by_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> TransitionResult:
        """
        Post a salary slip to GL - unified orchestration.

        This is the preferred method for posting slips. It orchestrates:
        1. Validates APPROVED → POSTED transition
        2. Calls PayrollGLAdapter to create journal and post to ledger
        3. Updates slip status to POSTED
        4. Commits the transaction
        5. Emits SlipPosted event

        Args:
            organization_id: Organization scope
            slip_id: Salary slip to post
            posting_date: Date for GL posting
            posted_by_id: User performing the posting
            idempotency_key: Optional key for idempotent posting

        Returns:
            TransitionResult with posting outcome
        """
        from app.services.people.payroll.payroll_gl_adapter import PayrollGLAdapter

        slip = self._get_slip(organization_id, slip_id)
        user_id = coerce_uuid(posted_by_id)

        # 1. Validate transition
        self.validate_slip_transition(slip.status, SalarySlipStatus.POSTED)

        # 2. Call GL adapter for journal creation and ledger posting
        gl_result = PayrollGLAdapter.create_slip_journal(
            self.db,
            organization_id=organization_id,
            slip=slip,
            posting_date=posting_date,
            posted_by_user_id=user_id,
        )

        if not gl_result.success:
            logger.warning(
                "GL posting failed for slip %s: %s",
                slip.slip_number,
                gl_result.message,
            )
            return TransitionResult(
                success=False,
                previous_status=slip.status.value,
                new_status=slip.status.value,
                message=gl_result.message,
            )

        # 3. Update status
        previous = self._update_slip_status(slip, SalarySlipStatus.POSTED, user_id)
        slip.journal_entry_id = gl_result.journal_entry_id
        slip.posted_at = datetime.now(timezone.utc)
        slip.posted_by_id = user_id

        # 4. Commit
        self.db.commit()

        logger.info(
            "Slip %s posted to GL: %s → %s by user %s (journal: %s)",
            slip.slip_number,
            previous.value,
            SalarySlipStatus.POSTED.value,
            user_id,
            gl_result.journal_entry_id,
        )

        # 5. Emit event
        self.dispatcher.dispatch(
            SlipPosted(
                organization_id=slip.organization_id,
                triggered_by_id=user_id,
                slip_id=slip.slip_id,
                slip_number=slip.slip_number,
                journal_entry_id=gl_result.journal_entry_id,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=SalarySlipStatus.POSTED.value,
            message=f"Slip {slip.slip_number} posted to GL (journal: {gl_result.journal_entry_id})",
        )

    def pay_slip(
        self,
        organization_id: UUID,
        slip_id: UUID,
        paid_by_id: UUID,
        payment_reference: Optional[str] = None,
    ) -> TransitionResult:
        """
        Mark a salary slip as paid.

        Transition: POSTED → PAID
        """
        slip = self._get_slip(organization_id, slip_id)
        user_id = coerce_uuid(paid_by_id)

        self.validate_slip_transition(slip.status, SalarySlipStatus.PAID)

        previous = self._update_slip_status(slip, SalarySlipStatus.PAID, user_id)

        # Set payment tracking fields
        slip.paid_at = datetime.now(timezone.utc)
        slip.paid_by_id = user_id
        if payment_reference:
            slip.payment_reference = payment_reference

        self.db.flush()

        logger.info(
            "Slip %s paid: %s → %s by user %s (ref: %s)",
            slip.slip_number,
            previous.value,
            SalarySlipStatus.PAID.value,
            user_id,
            payment_reference,
        )

        # Emit event
        self.dispatcher.dispatch(
            SlipPaid(
                organization_id=slip.organization_id,
                triggered_by_id=user_id,
                slip_id=slip.slip_id,
                slip_number=slip.slip_number,
                payment_reference=payment_reference,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=SalarySlipStatus.PAID.value,
            message=f"Slip {slip.slip_number} marked as paid",
        )

    def cancel_slip(
        self,
        organization_id: UUID,
        slip_id: UUID,
        cancelled_by_id: UUID,
        reason: Optional[str] = None,
    ) -> TransitionResult:
        """
        Cancel a salary slip.

        Can transition from any non-terminal state to CANCELLED.
        """
        slip = self._get_slip(organization_id, slip_id)
        user_id = coerce_uuid(cancelled_by_id)

        self.validate_slip_transition(slip.status, SalarySlipStatus.CANCELLED)

        previous = self._update_slip_status(slip, SalarySlipStatus.CANCELLED, user_id)
        self.db.flush()

        logger.info(
            "Slip %s cancelled: %s → %s by user %s (reason: %s)",
            slip.slip_number,
            previous.value,
            SalarySlipStatus.CANCELLED.value,
            user_id,
            reason,
        )

        # Emit event
        self.dispatcher.dispatch(
            SlipCancelled(
                organization_id=slip.organization_id,
                triggered_by_id=user_id,
                slip_id=slip.slip_id,
                slip_number=slip.slip_number,
                reason=reason,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=SalarySlipStatus.CANCELLED.value,
            message=f"Slip {slip.slip_number} cancelled",
        )

    def reject_slip(
        self,
        organization_id: UUID,
        slip_id: UUID,
        rejected_by_id: UUID,
    ) -> TransitionResult:
        """
        Reject a submitted slip back to draft.

        Transition: SUBMITTED → DRAFT
        """
        slip = self._get_slip(organization_id, slip_id)
        user_id = coerce_uuid(rejected_by_id)

        self.validate_slip_transition(slip.status, SalarySlipStatus.DRAFT)

        previous = self._update_slip_status(slip, SalarySlipStatus.DRAFT, user_id)
        self.db.flush()

        logger.info(
            "Slip %s rejected: %s → %s by user %s",
            slip.slip_number,
            previous.value,
            SalarySlipStatus.DRAFT.value,
            user_id,
        )

        # Emit event
        self.dispatcher.dispatch(
            SlipRejected(
                organization_id=slip.organization_id,
                triggered_by_id=user_id,
                slip_id=slip.slip_id,
                slip_number=slip.slip_number,
                rejected_by_id=user_id,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=SalarySlipStatus.DRAFT.value,
            message=f"Slip {slip.slip_number} rejected, returned to draft",
        )

    # ---------------------------------------------------------------------------
    # Payroll Run Transitions
    # ---------------------------------------------------------------------------

    def _get_run(self, organization_id: UUID, run_id: UUID) -> PayrollEntry:
        """Get a payroll run or raise 404."""
        org_id = coerce_uuid(organization_id)
        r_id = coerce_uuid(run_id)
        run = self.db.get(PayrollEntry, r_id)
        if not run or run.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Payroll run not found")
        return run

    def _update_run_status(
        self,
        run: PayrollEntry,
        new_status: PayrollEntryStatus,
        changed_by_id: UUID,
    ) -> PayrollEntryStatus:
        """Update run status and tracking fields."""
        previous = run.status
        run.status = new_status
        run.status_changed_at = datetime.now(timezone.utc)
        run.status_changed_by_id = changed_by_id
        return previous

    def mark_slips_created(
        self,
        organization_id: UUID,
        run_id: UUID,
        created_by_id: UUID,
        slip_count: int,
    ) -> TransitionResult:
        """
        Mark that slips have been created for a run.

        Transition: DRAFT/PENDING → SLIPS_CREATED
        """
        run = self._get_run(organization_id, run_id)
        user_id = coerce_uuid(created_by_id)

        self.validate_run_transition(run.status, PayrollEntryStatus.SLIPS_CREATED)

        previous = self._update_run_status(run, PayrollEntryStatus.SLIPS_CREATED, user_id)
        self.db.flush()

        logger.info(
            "Run %s slips created: %s → %s (%d slips)",
            run.entry_number,
            previous.value,
            PayrollEntryStatus.SLIPS_CREATED.value,
            slip_count,
        )

        # Emit event
        self.dispatcher.dispatch(
            RunSlipsCreated(
                organization_id=run.organization_id,
                triggered_by_id=user_id,
                run_id=run.entry_id,
                run_number=run.entry_number,
                slip_count=slip_count,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=PayrollEntryStatus.SLIPS_CREATED.value,
            message=f"Run {run.entry_number}: {slip_count} slips created",
        )

    def submit_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        submitted_by_id: UUID,
    ) -> TransitionResult:
        """
        Submit a payroll run for approval.

        Transition: SLIPS_CREATED → SUBMITTED
        """
        run = self._get_run(organization_id, run_id)
        user_id = coerce_uuid(submitted_by_id)

        self.validate_run_transition(run.status, PayrollEntryStatus.SUBMITTED)

        previous = self._update_run_status(run, PayrollEntryStatus.SUBMITTED, user_id)
        self.db.flush()

        logger.info(
            "Run %s submitted: %s → %s by user %s",
            run.entry_number,
            previous.value,
            PayrollEntryStatus.SUBMITTED.value,
            user_id,
        )

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import fire_workflow_event
            fire_workflow_event(
                db=self.db, organization_id=run.organization_id,
                entity_type="PAYROLL_RUN", entity_id=run.entry_id,
                event="ON_STATUS_CHANGE",
                old_values={"status": previous.value},
                new_values={"status": "SUBMITTED"}, user_id=user_id,
            )
        except Exception:
            pass

        # Commit before emitting event so handlers see committed state
        event_org_id = run.organization_id
        event_run_id = run.entry_id
        event_run_number = run.entry_number
        self.db.commit()

        # Emit event
        self.dispatcher.dispatch(
            RunSubmitted(
                organization_id=event_org_id,
                triggered_by_id=user_id,
                run_id=event_run_id,
                run_number=event_run_number,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=PayrollEntryStatus.SUBMITTED.value,
            message=f"Run {run.entry_number} submitted for approval",
        )

    def approve_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        approved_by_id: UUID,
    ) -> TransitionResult:
        """
        Approve a submitted payroll run.

        Transition: SUBMITTED → APPROVED
        Includes segregation of duties check.
        """
        run = self._get_run(organization_id, run_id)
        user_id = coerce_uuid(approved_by_id)

        self.validate_run_transition(run.status, PayrollEntryStatus.APPROVED)

        # Segregation of duties: creator cannot approve
        if run.created_by_id == user_id:
            raise HTTPException(
                status_code=403,
                detail="Segregation of duties: creator cannot approve their own run",
            )

        previous = self._update_run_status(run, PayrollEntryStatus.APPROVED, user_id)
        self.db.flush()

        logger.info(
            "Run %s approved: %s → %s by user %s",
            run.entry_number,
            previous.value,
            PayrollEntryStatus.APPROVED.value,
            user_id,
        )

        # Fire workflow automation event
        try:
            from app.services.finance.automation.event_dispatcher import fire_workflow_event
            fire_workflow_event(
                db=self.db, organization_id=run.organization_id,
                entity_type="PAYROLL_RUN", entity_id=run.entry_id,
                event="ON_APPROVAL",
                old_values={"status": previous.value},
                new_values={"status": "APPROVED"}, user_id=user_id,
            )
        except Exception:
            pass

        # Commit before emitting event so handlers see committed state
        event_org_id = run.organization_id
        event_run_id = run.entry_id
        event_run_number = run.entry_number
        self.db.commit()

        # Emit event
        self.dispatcher.dispatch(
            RunApproved(
                organization_id=event_org_id,
                triggered_by_id=user_id,
                run_id=event_run_id,
                run_number=event_run_number,
                approved_by_id=user_id,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=PayrollEntryStatus.APPROVED.value,
            message=f"Run {run.entry_number} approved",
        )

    def post_run_to_gl(
        self,
        organization_id: UUID,
        run_id: UUID,
        posting_date: date,
        posted_by_id: UUID,
        idempotency_key: Optional[str] = None,
    ) -> TransitionResult:
        """
        Post a payroll run to GL - unified orchestration.

        This is the preferred method for posting runs. It orchestrates:
        1. Validates APPROVED → POSTED transition
        2. Loads approved slips for the run
        3. Calls PayrollGLAdapter to create consolidated journal and post
        4. Updates all slip statuses to POSTED
        5. Updates run status to POSTED
        6. Commits the transaction
        7. Emits RunPosted event (no per-slip events)

        Args:
            organization_id: Organization scope
            run_id: Payroll entry ID to post
            posting_date: Date for GL posting
            posted_by_id: User performing the posting
            idempotency_key: Optional key for idempotent posting

        Returns:
            TransitionResult with posting outcome
        """
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        from app.services.people.payroll.payroll_gl_adapter import PayrollGLAdapter

        run = self._get_run(organization_id, run_id)
        user_id = coerce_uuid(posted_by_id)

        # 1. Validate transition
        self.validate_run_transition(run.status, PayrollEntryStatus.POSTED)

        # 2. Load slips with deductions
        slips = list(
            self.db.scalars(
                select(SalarySlip)
                .options(
                    selectinload(SalarySlip.deductions).selectinload(SalarySlipDeduction.component)
                )
                .where(SalarySlip.payroll_entry_id == run.entry_id)
            ).all()
        )

        if not slips:
            return TransitionResult(
                success=False,
                previous_status=run.status.value,
                new_status=run.status.value,
                message="No salary slips found in payroll entry",
            )

        not_approved = [s for s in slips if s.status != SalarySlipStatus.APPROVED]
        if not_approved:
            return TransitionResult(
                success=False,
                previous_status=run.status.value,
                new_status=run.status.value,
                message=(
                    f"All slips must be APPROVED to post a run "
                    f"(found {len(not_approved)} non-approved)"
                ),
            )

        # Enforce single currency/exchange rate per run
        from decimal import Decimal
        currency_codes = {s.currency_code for s in slips}
        exchange_rates = {s.exchange_rate or Decimal("1.0") for s in slips}
        if len(currency_codes) > 1 or len(exchange_rates) > 1:
            return TransitionResult(
                success=False,
                previous_status=run.status.value,
                new_status=run.status.value,
                message="Mixed currency or exchange rate in payroll run; split runs by currency",
            )

        # 3. Call GL adapter for journal creation and ledger posting
        gl_result = PayrollGLAdapter.create_run_journal(
            self.db,
            organization_id=organization_id,
            entry=run,
            slips=slips,
            posting_date=posting_date,
            posted_by_user_id=user_id,
        )

        if not gl_result.success:
            logger.warning(
                "GL posting failed for run %s: %s",
                run.entry_number,
                gl_result.message,
            )
            return TransitionResult(
                success=False,
                previous_status=run.status.value,
                new_status=run.status.value,
                message=gl_result.message,
            )

        # 4. Update all slips to POSTED
        now = datetime.now(timezone.utc)
        for slip in slips:
            slip.status = SalarySlipStatus.POSTED
            slip.journal_entry_id = gl_result.journal_entry_id
            slip.posted_at = now
            slip.posted_by_id = user_id

        # 5. Update run status
        previous = self._update_run_status(run, PayrollEntryStatus.POSTED, user_id)
        run.journal_entry_id = gl_result.journal_entry_id

        # 6. Commit
        self.db.commit()

        logger.info(
            "Run %s posted to GL: %s → %s by user %s (journal: %s, %d slips)",
            run.entry_number,
            previous.value,
            PayrollEntryStatus.POSTED.value,
            user_id,
            gl_result.journal_entry_id,
            len(slips),
        )

        # 7. Emit event
        self.dispatcher.dispatch(
            RunPosted(
                organization_id=run.organization_id,
                triggered_by_id=user_id,
                run_id=run.entry_id,
                run_number=run.entry_number,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=PayrollEntryStatus.POSTED.value,
            message=f"Run {run.entry_number} posted to GL ({len(slips)} slips)",
        )

    def cancel_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        cancelled_by_id: UUID,
        reason: Optional[str] = None,
    ) -> TransitionResult:
        """
        Cancel a payroll run.

        Can transition from any non-terminal state to CANCELLED.
        """
        run = self._get_run(organization_id, run_id)
        user_id = coerce_uuid(cancelled_by_id)

        self.validate_run_transition(run.status, PayrollEntryStatus.CANCELLED)

        previous = self._update_run_status(run, PayrollEntryStatus.CANCELLED, user_id)
        self.db.flush()

        logger.info(
            "Run %s cancelled: %s → %s by user %s (reason: %s)",
            run.entry_number,
            previous.value,
            PayrollEntryStatus.CANCELLED.value,
            user_id,
            reason,
        )

        # Emit event
        self.dispatcher.dispatch(
            RunCancelled(
                organization_id=run.organization_id,
                triggered_by_id=user_id,
                run_id=run.entry_id,
                run_number=run.entry_number,
                reason=reason,
            )
        )

        return TransitionResult(
            success=True,
            previous_status=previous.value,
            new_status=PayrollEntryStatus.CANCELLED.value,
            message=f"Run {run.entry_number} cancelled",
        )
