"""
Discipline Module Background Tasks - Celery tasks for discipline reminder workflows.

Handles:
- Response due reminders (multi-level: 3 days, 1 day, on due date, overdue)
- Hearing reminders (3 days before, 1 day before)
- Appeal deadline reminders (7 days, 3 days, 1 day before)
"""

import logging
from datetime import date, datetime, timedelta, timezone
from typing import TypedDict

from celery import shared_task

from app.db import SessionLocal
from app.models.notification import EntityType, NotificationType, NotificationChannel

logger = logging.getLogger(__name__)


class ResponseReminderResults(TypedDict):
    pending_reminders_sent: int
    overdue_reminders_sent: int
    errors: list[str]


class HearingReminderResults(TypedDict):
    reminders_sent: int
    errors: list[str]


class AppealReminderResults(TypedDict):
    reminders_sent: int
    errors: list[str]


@shared_task
def process_discipline_response_reminders() -> ResponseReminderResults:
    """Send reminders for pending query responses.

    Multi-level reminder schedule:
    - 3 days before due: First reminder
    - 1 day before due: Second reminder
    - On due date: Final reminder
    - Overdue: Daily until 7 days past due

    Returns:
        Dict with processing statistics
    """
    logger.info("Processing discipline response reminders")

    results: ResponseReminderResults = {
        "pending_reminders_sent": 0,
        "overdue_reminders_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        from app.services.people.discipline import DisciplineService
        from app.services.notification import notification_service

        service = DisciplineService(db)

        # Get cases with responses due soon (3 days window)
        pending_cases = service.get_cases_with_pending_responses(days_before=3)

        for case in pending_cases:
            try:
                if not case.employee or not case.employee.person_id:
                    continue
                if not case.response_due_date:
                    continue

                days_until_due = (case.response_due_date - date.today()).days

                # Send reminder based on days remaining
                if days_until_due in (3, 1, 0):
                    _send_response_reminder(
                        db,
                        notification_service,
                        case,
                        days_until_due,
                    )
                    results["pending_reminders_sent"] += 1

            except Exception as e:
                logger.exception(
                    "Failed to send response reminder for case %s",
                    case.case_number,
                )
                results["errors"].append(f"{case.case_number}: {str(e)}")

        # Get overdue cases
        overdue_cases = service.get_cases_with_overdue_responses()

        for case in overdue_cases:
            try:
                if not case.employee or not case.employee.person_id:
                    continue
                if not case.response_due_date:
                    continue

                days_overdue = (date.today() - case.response_due_date).days

                # Send daily overdue reminder
                _send_overdue_response_reminder(
                    db,
                    notification_service,
                    case,
                    days_overdue,
                )
                results["overdue_reminders_sent"] += 1

            except Exception as e:
                logger.exception(
                    "Failed to send overdue reminder for case %s",
                    case.case_number,
                )
                results["errors"].append(f"{case.case_number}: {str(e)}")

        db.commit()

    logger.info(
        "Response reminders completed: %d pending, %d overdue, %d errors",
        results["pending_reminders_sent"],
        results["overdue_reminders_sent"],
        len(results["errors"]),
    )
    return results


@shared_task
def process_discipline_hearing_reminders() -> HearingReminderResults:
    """Send reminders for upcoming disciplinary hearings.

    Reminder schedule:
    - 3 days before: Initial notice
    - 1 day before: Final reminder

    Returns:
        Dict with processing statistics
    """
    logger.info("Processing discipline hearing reminders")

    results: HearingReminderResults = {
        "reminders_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        from app.services.people.discipline import DisciplineService
        from app.services.notification import notification_service

        service = DisciplineService(db)

        # Get cases with hearings within 3 days
        cases = service.get_cases_with_upcoming_hearings(days_before=3)

        for case in cases:
            try:
                if not case.employee or not case.employee.person_id:
                    continue

                if not case.hearing_date:
                    continue

                now = datetime.now(timezone.utc)
                time_until_hearing = case.hearing_date - now
                days_until = time_until_hearing.days

                # Send reminder at 3 days and 1 day before
                if days_until in (3, 1, 0):
                    _send_hearing_reminder(
                        db,
                        notification_service,
                        case,
                        days_until,
                    )
                    results["reminders_sent"] += 1

            except Exception as e:
                logger.exception(
                    "Failed to send hearing reminder for case %s",
                    case.case_number,
                )
                results["errors"].append(f"{case.case_number}: {str(e)}")

        db.commit()

    logger.info(
        "Hearing reminders completed: %d sent, %d errors",
        results["reminders_sent"],
        len(results["errors"]),
    )
    return results


@shared_task
def process_discipline_appeal_deadline_reminders() -> AppealReminderResults:
    """Send reminders for expiring appeal deadlines.

    Reminder schedule:
    - 7 days before: First notice
    - 3 days before: Second notice
    - 1 day before: Final reminder

    Returns:
        Dict with processing statistics
    """
    logger.info("Processing discipline appeal deadline reminders")

    results: AppealReminderResults = {
        "reminders_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        from app.services.people.discipline import DisciplineService
        from app.services.notification import notification_service

        service = DisciplineService(db)

        # Get cases with appeal deadlines within 7 days
        cases = service.get_cases_with_expiring_appeals(days_before=7)

        for case in cases:
            try:
                if not case.employee or not case.employee.person_id:
                    continue

                if not case.appeal_deadline:
                    continue

                days_until_deadline = (case.appeal_deadline - date.today()).days

                # Send reminder at 7, 3, and 1 days before
                if days_until_deadline in (7, 3, 1, 0):
                    _send_appeal_deadline_reminder(
                        db,
                        notification_service,
                        case,
                        days_until_deadline,
                    )
                    results["reminders_sent"] += 1

            except Exception as e:
                logger.exception(
                    "Failed to send appeal deadline reminder for case %s",
                    case.case_number,
                )
                results["errors"].append(f"{case.case_number}: {str(e)}")

        db.commit()

    logger.info(
        "Appeal deadline reminders completed: %d sent, %d errors",
        results["reminders_sent"],
        len(results["errors"]),
    )
    return results


@shared_task
def process_all_discipline_reminders() -> dict:
    """Master task that runs all discipline reminder tasks.

    This can be scheduled once daily to handle all reminder types.

    Returns:
        Dict with combined processing statistics
    """
    logger.info("Processing all discipline reminders")

    results: dict[str, dict[str, object]] = {
        "response_reminders": {},
        "hearing_reminders": {},
        "appeal_reminders": {},
    }

    try:
        results["response_reminders"] = process_discipline_response_reminders()
    except Exception as e:
        logger.exception("Failed to process response reminders")
        results["response_reminders"] = {"error": str(e)}

    try:
        results["hearing_reminders"] = process_discipline_hearing_reminders()
    except Exception as e:
        logger.exception("Failed to process hearing reminders")
        results["hearing_reminders"] = {"error": str(e)}

    try:
        results["appeal_reminders"] = process_discipline_appeal_deadline_reminders()
    except Exception as e:
        logger.exception("Failed to process appeal deadline reminders")
        results["appeal_reminders"] = {"error": str(e)}

    logger.info("All discipline reminders completed")
    return results


# =============================================================================
# Helper Functions
# =============================================================================


def _send_response_reminder(
    db,
    notification_service,
    case,
    days_until_due: int,
) -> None:
    """Send a reminder for pending response."""
    if days_until_due == 0:
        title = f"Response Due Today - {case.case_number}"
        message = (
            f"Your response to disciplinary case {case.case_number} is due TODAY. "
            "Please submit your response immediately."
        )
        notification_type = NotificationType.DUE_SOON
    elif days_until_due == 1:
        title = f"Response Due Tomorrow - {case.case_number}"
        message = (
            f"Your response to disciplinary case {case.case_number} is due tomorrow. "
            "Please ensure your response is submitted on time."
        )
        notification_type = NotificationType.DUE_SOON
    else:
        title = f"Response Due in {days_until_due} Days - {case.case_number}"
        message = (
            f"This is a reminder that your response to disciplinary case "
            f"{case.case_number} is due on {case.response_due_date.strftime('%B %d, %Y')}."
        )
        notification_type = NotificationType.REMINDER

    notification_service.create(
        db,
        organization_id=case.organization_id,
        recipient_id=case.employee.person_id,
        entity_type=EntityType.DISCIPLINE,
        entity_id=case.case_id,
        notification_type=notification_type,
        title=title,
        message=message,
        channel=NotificationChannel.BOTH,
        action_url=f"/people/self-service/discipline/{case.case_id}",
    )


def _send_overdue_response_reminder(
    db,
    notification_service,
    case,
    days_overdue: int,
) -> None:
    """Send a reminder for overdue response."""
    title = f"Response Overdue - {case.case_number}"
    message = (
        f"Your response to disciplinary case {case.case_number} is {days_overdue} "
        f"day(s) overdue. Please submit your response immediately to avoid "
        "further action being taken."
    )

    notification_service.create(
        db,
        organization_id=case.organization_id,
        recipient_id=case.employee.person_id,
        entity_type=EntityType.DISCIPLINE,
        entity_id=case.case_id,
        notification_type=NotificationType.OVERDUE,
        title=title,
        message=message,
        channel=NotificationChannel.BOTH,
        action_url=f"/people/self-service/discipline/{case.case_id}",
    )


def _send_hearing_reminder(
    db,
    notification_service,
    case,
    days_until: int,
) -> None:
    """Send a reminder for upcoming hearing."""
    hearing_date_str = case.hearing_date.strftime("%B %d, %Y at %I:%M %p")
    location_str = f" at {case.hearing_location}" if case.hearing_location else ""

    if days_until == 0:
        title = f"Hearing Today - {case.case_number}"
        message = (
            f"Your disciplinary hearing for case {case.case_number} is scheduled "
            f"for today{location_str}. Please ensure you arrive on time."
        )
    elif days_until == 1:
        title = f"Hearing Tomorrow - {case.case_number}"
        message = (
            f"Your disciplinary hearing for case {case.case_number} is scheduled "
            f"for tomorrow ({hearing_date_str}){location_str}."
        )
    else:
        title = f"Hearing in {days_until} Days - {case.case_number}"
        message = (
            f"This is a reminder that your disciplinary hearing for case "
            f"{case.case_number} is scheduled for {hearing_date_str}{location_str}."
        )

    notification_service.create(
        db,
        organization_id=case.organization_id,
        recipient_id=case.employee.person_id,
        entity_type=EntityType.DISCIPLINE,
        entity_id=case.case_id,
        notification_type=NotificationType.REMINDER,
        title=title,
        message=message,
        channel=NotificationChannel.BOTH,
        action_url=f"/people/self-service/discipline/{case.case_id}",
    )


def _send_appeal_deadline_reminder(
    db,
    notification_service,
    case,
    days_until_deadline: int,
) -> None:
    """Send a reminder for expiring appeal deadline."""
    deadline_str = case.appeal_deadline.strftime("%B %d, %Y")

    if days_until_deadline == 0:
        title = f"Appeal Deadline Today - {case.case_number}"
        message = (
            f"Today is the last day to file an appeal for disciplinary case "
            f"{case.case_number}. If you wish to appeal, please do so before "
            "end of business today."
        )
        notification_type = NotificationType.DUE_SOON
    elif days_until_deadline == 1:
        title = f"Appeal Deadline Tomorrow - {case.case_number}"
        message = (
            f"The deadline to file an appeal for disciplinary case "
            f"{case.case_number} is tomorrow ({deadline_str}). If you wish to "
            "appeal the decision, please submit your appeal before the deadline."
        )
        notification_type = NotificationType.DUE_SOON
    else:
        title = f"Appeal Deadline in {days_until_deadline} Days - {case.case_number}"
        message = (
            f"This is a reminder that the deadline to file an appeal for "
            f"disciplinary case {case.case_number} is {deadline_str}. You have "
            f"{days_until_deadline} days remaining to submit an appeal if you "
            "wish to contest the decision."
        )
        notification_type = NotificationType.REMINDER

    notification_service.create(
        db,
        organization_id=case.organization_id,
        recipient_id=case.employee.person_id,
        entity_type=EntityType.DISCIPLINE,
        entity_id=case.case_id,
        notification_type=notification_type,
        title=title,
        message=message,
        channel=NotificationChannel.BOTH,
        action_url=f"/people/self-service/discipline/{case.case_id}",
    )
