"""
Payroll Event Handlers.

Subscribes to payroll domain events and triggers side effects like
notifications and emails. This decouples notification logic from
the core payroll services.

Usage:
    # At application startup
    from app.services.people.payroll.event_handlers import register_payroll_handlers
    register_payroll_handlers()

    # Now when events are dispatched, handlers will be called automatically
    payroll_dispatcher.dispatch(SlipPosted(...))
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.models.people.hr.employee import Employee
from app.models.people.payroll.payroll_entry import PayrollEntry
from app.models.people.payroll.salary_slip import SalarySlip
from app.services.people.payroll.events import (
    PayrollEventDispatcher,
    RunApproved,
    RunCancelled,
    RunPosted,
    RunSubmitted,
    SlipPaid,
    SlipPosted,
    payroll_dispatcher,
)
from app.services.people.payroll.payroll_notifications import PayrollNotificationService

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Handler Registry
# ---------------------------------------------------------------------------


class PayrollEventHandlers:
    """
    Collection of event handlers for payroll domain events.

    Handlers are registered with the dispatcher and called when
    corresponding events are dispatched. Each handler receives
    the event and a database session factory.

    This class is stateless - it uses a session factory to create
    database sessions as needed for each event.
    """

    def __init__(self, session_factory: Callable[[], Session]):
        """
        Initialize handlers with a session factory.

        Args:
            session_factory: Callable that returns a new database session
        """
        self._session_factory = session_factory

    def handle_slip_posted(self, event: SlipPosted) -> None:
        """
        Handle SlipPosted event - notify employee their payslip is ready.

        Args:
            event: The SlipPosted event
        """
        logger.debug("Handling SlipPosted event for slip %s", event.slip_number)

        try:
            with self._session_factory() as db:
                slip = db.get(SalarySlip, event.slip_id)
                if not slip:
                    logger.warning(
                        "SlipPosted handler: slip %s not found",
                        event.slip_id,
                    )
                    return
                if slip.organization_id != event.organization_id:
                    logger.warning(
                        "SlipPosted handler: org mismatch for slip %s",
                        event.slip_id,
                    )
                    return

                employee = db.get(Employee, slip.employee_id)
                if not employee:
                    logger.warning(
                        "SlipPosted handler: employee %s not found for slip %s",
                        slip.employee_id,
                        event.slip_number,
                    )
                    return

                notification_service = PayrollNotificationService(db)
                notification_service.notify_payslip_posted(slip, employee)

                db.commit()

                logger.info(
                    "SlipPosted handler: notified employee %s for slip %s",
                    employee.employee_id,
                    event.slip_number,
                )

        except Exception as e:
            logger.exception(
                "SlipPosted handler failed for slip %s: %s",
                event.slip_number,
                e,
            )

    def handle_slip_paid(self, event: SlipPaid) -> None:
        """
        Handle SlipPaid event - notify employee their salary has been paid.

        Args:
            event: The SlipPaid event
        """
        logger.debug("Handling SlipPaid event for slip %s", event.slip_number)

        try:
            with self._session_factory() as db:
                slip = db.get(SalarySlip, event.slip_id)
                if not slip:
                    logger.warning(
                        "SlipPaid handler: slip %s not found",
                        event.slip_id,
                    )
                    return
                if slip.organization_id != event.organization_id:
                    logger.warning(
                        "SlipPaid handler: org mismatch for slip %s",
                        event.slip_id,
                    )
                    return

                # Store payment reference if provided
                if event.payment_reference and not slip.payment_reference:
                    slip.payment_reference = event.payment_reference

                employee = db.get(Employee, slip.employee_id)
                if not employee:
                    logger.warning(
                        "SlipPaid handler: employee %s not found for slip %s",
                        slip.employee_id,
                        event.slip_number,
                    )
                    return

                notification_service = PayrollNotificationService(db)
                notification_service.notify_payslip_paid(slip, employee)

                db.commit()

                logger.info(
                    "SlipPaid handler: notified employee %s for slip %s",
                    employee.employee_id,
                    event.slip_number,
                )

        except Exception as e:
            logger.exception(
                "SlipPaid handler failed for slip %s: %s",
                event.slip_number,
                e,
            )

    def handle_run_posted(self, event: RunPosted) -> None:
        """
        Handle RunPosted event - notify employees for all slips in the run.

        This is an optimization for bulk payroll runs - instead of
        handling individual SlipPosted events, we process all slips
        in a single database transaction.

        Args:
            event: The RunPosted event
        """
        logger.debug("Handling RunPosted event for run %s", event.run_number)

        try:
            with self._session_factory() as db:
                run = db.get(PayrollEntry, event.run_id)
                if not run:
                    logger.warning(
                        "RunPosted handler: run %s not found",
                        event.run_id,
                    )
                    return
                if run.organization_id != event.organization_id:
                    logger.warning(
                        "RunPosted handler: org mismatch for run %s",
                        event.run_id,
                    )
                    return

                notification_service = PayrollNotificationService(db)
                notified_count = 0

                # Process each slip in the run
                for slip in run.salary_slips:
                    try:
                        employee = db.get(Employee, slip.employee_id)
                        if employee:
                            notification_service.notify_payslip_posted(
                                slip, employee, queue_email=True
                            )
                            notified_count += 1
                    except Exception as slip_err:
                        logger.warning(
                            "RunPosted handler: failed to notify for slip %s: %s",
                            slip.slip_number,
                            slip_err,
                        )

                if not getattr(run, "payslips_email_status", None):
                    run.payslips_email_status = "QUEUED"
                    run.payslips_email_queued_at = datetime.now(UTC)

                db.commit()

                logger.info(
                    "RunPosted handler: notified %d employees for run %s",
                    notified_count,
                    event.run_number,
                )

        except Exception as e:
            logger.exception(
                "RunPosted handler failed for run %s: %s",
                event.run_number,
                e,
            )

    def handle_run_submitted(self, event: RunSubmitted) -> None:
        """
        Handle RunSubmitted event - notify approvers.

        Args:
            event: The RunSubmitted event
        """
        logger.debug("Handling RunSubmitted event for run %s", event.run_number)

        try:
            with self._session_factory() as db:
                from sqlalchemy import select

                from app.models.person import Person
                from app.models.rbac import PersonRole, Role

                run = db.get(PayrollEntry, event.run_id)
                if not run:
                    logger.warning(
                        "RunSubmitted handler: run %s not found",
                        event.run_id,
                    )
                    return
                if run.organization_id != event.organization_id:
                    logger.warning(
                        "RunSubmitted handler: org mismatch for run %s",
                        event.run_id,
                    )
                    return

                # Find approvers (users with payroll approval permission)
                approver_ids = list(
                    db.scalars(
                        select(PersonRole.person_id)
                        .join(Role, PersonRole.role_id == Role.id)
                        .join(Person, PersonRole.person_id == Person.id)
                        .where(
                            Person.organization_id == event.organization_id,
                            Role.name.in_(
                                ["payroll_approver", "hr_manager", "finance_manager"]
                            ),
                            Role.is_active.is_(True),
                            Person.is_active.is_(True),
                        )
                    ).all()
                )

                if not approver_ids:
                    logger.warning(
                        "RunSubmitted handler: no approvers found for org %s",
                        event.organization_id,
                    )
                    return

                # Calculate totals
                slip_count = len(run.salary_slips) if run.salary_slips else 0
                total_net_pay = sum(
                    float(s.net_pay or 0) for s in (run.salary_slips or [])
                )
                currency = (
                    run.salary_slips[0].currency_code if run.salary_slips else "NGN"
                )

                notification_service = PayrollNotificationService(db)
                notified = notification_service.notify_run_submitted(
                    run_id=event.run_id,
                    org_id=event.organization_id,
                    run_number=event.run_number,
                    slip_count=slip_count,
                    total_net_pay=total_net_pay,
                    currency_code=currency,
                    approver_ids=approver_ids,
                )

                db.commit()

                logger.info(
                    "RunSubmitted handler: notified %d approvers for run %s",
                    notified,
                    event.run_number,
                )

        except Exception as e:
            logger.exception(
                "RunSubmitted handler failed for run %s: %s",
                event.run_number,
                e,
            )

    def handle_run_approved(self, event: RunApproved) -> None:
        """
        Handle RunApproved event - notify submitter and payroll team.

        Args:
            event: The RunApproved event
        """
        logger.debug("Handling RunApproved event for run %s", event.run_number)

        try:
            with self._session_factory() as db:
                from app.models.person import Person

                run = db.get(PayrollEntry, event.run_id)
                if not run:
                    logger.warning(
                        "RunApproved handler: run %s not found",
                        event.run_id,
                    )
                    return
                if run.organization_id != event.organization_id:
                    logger.warning(
                        "RunApproved handler: org mismatch for run %s",
                        event.run_id,
                    )
                    return

                # Get approver name
                approver = db.get(Person, event.approved_by_id)
                approver_name = approver.name if approver else "Unknown"

                # Get submitter (created_by)
                submitter_id = getattr(run, "created_by_id", None)
                slip_count = len(run.salary_slips) if run.salary_slips else 0

                notification_service = PayrollNotificationService(db)
                notified = notification_service.notify_run_approved(
                    run_id=event.run_id,
                    org_id=event.organization_id,
                    run_number=event.run_number,
                    approved_by_name=approver_name,
                    slip_count=slip_count,
                    submitter_id=submitter_id,
                )

                db.commit()

                logger.info(
                    "RunApproved handler: notified %d recipients for run %s",
                    notified,
                    event.run_number,
                )

        except Exception as e:
            logger.exception(
                "RunApproved handler failed for run %s: %s",
                event.run_number,
                e,
            )

    def handle_run_cancelled(self, event: RunCancelled) -> None:
        """
        Handle RunCancelled event - notify payroll team.

        Args:
            event: The RunCancelled event
        """
        logger.debug("Handling RunCancelled event for run %s", event.run_number)

        try:
            with self._session_factory() as db:
                from sqlalchemy import select

                from app.models.person import Person
                from app.models.rbac import PersonRole, Role

                run = db.get(PayrollEntry, event.run_id)
                if not run:
                    logger.warning(
                        "RunCancelled handler: run %s not found",
                        event.run_id,
                    )
                    return
                if run.organization_id != event.organization_id:
                    logger.warning(
                        "RunCancelled handler: org mismatch for run %s",
                        event.run_id,
                    )
                    return

                # Get canceller name
                canceller = db.get(Person, event.triggered_by_id)
                canceller_name = canceller.name if canceller else "Unknown"

                # Find payroll team
                payroll_team_ids = list(
                    db.scalars(
                        select(PersonRole.person_id)
                        .join(Role, PersonRole.role_id == Role.id)
                        .join(Person, PersonRole.person_id == Person.id)
                        .where(
                            Person.organization_id == event.organization_id,
                            Role.name.in_(
                                ["payroll_admin", "hr_manager", "payroll_approver"]
                            ),
                            Role.is_active.is_(True),
                            Person.is_active.is_(True),
                        )
                    ).all()
                )

                notification_service = PayrollNotificationService(db)
                notified = notification_service.notify_run_cancelled(
                    run_id=event.run_id,
                    org_id=event.organization_id,
                    run_number=event.run_number,
                    cancelled_by_name=canceller_name,
                    reason=event.reason,
                    payroll_team_ids=payroll_team_ids,
                )

                db.commit()

                logger.info(
                    "RunCancelled handler: notified %d recipients for run %s",
                    notified,
                    event.run_number,
                )

        except Exception as e:
            logger.exception(
                "RunCancelled handler failed for run %s: %s",
                event.run_number,
                e,
            )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


_handlers_registered = False
_handlers_instance: PayrollEventHandlers | None = None


def register_payroll_handlers(
    dispatcher: PayrollEventDispatcher | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> PayrollEventHandlers:
    """
    Register payroll event handlers with the dispatcher.

    This should be called once at application startup.

    Args:
        dispatcher: Event dispatcher (defaults to global payroll_dispatcher)
        session_factory: Database session factory (defaults to SessionLocal)

    Returns:
        The PayrollEventHandlers instance (useful for testing)
    """
    global _handlers_registered, _handlers_instance

    if dispatcher is None:
        dispatcher = payroll_dispatcher

    if session_factory is None:
        from app.db import SessionLocal

        session_factory = SessionLocal

    if _handlers_registered:
        logger.warning("Payroll event handlers already registered")
        if _handlers_instance:
            return _handlers_instance
        return PayrollEventHandlers(session_factory)

    handlers = PayrollEventHandlers(session_factory)

    # Register handlers
    dispatcher.register(SlipPosted, handlers.handle_slip_posted)
    dispatcher.register(SlipPaid, handlers.handle_slip_paid)
    dispatcher.register(RunPosted, handlers.handle_run_posted)
    dispatcher.register(RunSubmitted, handlers.handle_run_submitted)
    dispatcher.register(RunApproved, handlers.handle_run_approved)
    dispatcher.register(RunCancelled, handlers.handle_run_cancelled)

    _handlers_registered = True
    _handlers_instance = handlers

    logger.info("Payroll event handlers registered")

    return handlers


def unregister_payroll_handlers(
    dispatcher: PayrollEventDispatcher | None = None,
    handlers: PayrollEventHandlers | None = None,
) -> None:
    """
    Unregister payroll event handlers (useful for testing).

    Args:
        dispatcher: Event dispatcher
        handlers: Handlers instance to unregister
    """
    global _handlers_registered

    if dispatcher is None:
        dispatcher = payroll_dispatcher

    global _handlers_instance

    if handlers is None and _handlers_instance:
        handlers = _handlers_instance

    if handlers:
        dispatcher.unregister(SlipPosted, handlers.handle_slip_posted)
        dispatcher.unregister(SlipPaid, handlers.handle_slip_paid)
        dispatcher.unregister(RunPosted, handlers.handle_run_posted)
        dispatcher.unregister(RunSubmitted, handlers.handle_run_submitted)
        dispatcher.unregister(RunApproved, handlers.handle_run_approved)
        dispatcher.unregister(RunCancelled, handlers.handle_run_cancelled)

    _handlers_registered = False
    _handlers_instance = None


def are_handlers_registered() -> bool:
    """Check if handlers are registered."""
    return _handlers_registered
