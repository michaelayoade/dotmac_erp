"""
HR Module Background Tasks - Celery tasks for HR workflows.

Handles:
- Probation period ending notifications
- Contract expiry notifications
- Work anniversary notifications
- Employee birthday notifications
- Performance review due reminders
- Certification expiry warnings
"""

import logging
from datetime import date, timedelta
from typing import Any, Optional
import uuid

from celery import shared_task
from sqlalchemy import and_, func, or_, select

from app.db import SessionLocal
from app.models.finance.core_org.organization import Organization
from app.models.people.hr.employee import Employee, EmployeeStatus

logger = logging.getLogger(__name__)


@shared_task
def process_probation_ending_notifications() -> dict:
    """
    Send notifications for employees whose probation period is ending soon.

    Sends notifications:
    - 14 days before probation ends
    - 7 days before probation ends
    - On the day probation ends

    Returns:
        Dict with notification statistics
    """
    from app.services.hr_notifications import HRNotificationService

    FIRST_NOTICE_DAYS = 14
    SECOND_NOTICE_DAYS = 7
    FINAL_NOTICE_DAYS = 0

    logger.info("Processing probation ending notifications")

    results: dict[str, Any] = {
        "first_notices_sent": 0,
        "second_notices_sent": 0,
        "final_notices_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        today = date.today()

        # Find employees on probation with probation end dates
        probation_employees = db.scalars(
            select(Employee)
            .where(
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.probation_end_date.isnot(None),
            )
        ).all()

        notification_service = HRNotificationService(db)

        for employee in probation_employees:
            try:
                probation_end = employee.probation_end_date
                if probation_end is None:
                    continue
                days_remaining = (probation_end - today).days

                # Skip if probation already ended or too far away
                if days_remaining < 0 or days_remaining > FIRST_NOTICE_DAYS:
                    continue

                # Determine notice type
                if days_remaining == FINAL_NOTICE_DAYS:
                    notice_type = "final"
                elif days_remaining <= SECOND_NOTICE_DAYS:
                    notice_type = "second"
                elif days_remaining <= FIRST_NOTICE_DAYS:
                    notice_type = "first"
                else:
                    continue

                # Get manager
                manager = None
                if employee.reports_to_id:
                    manager = db.get(Employee, employee.reports_to_id)

                # Send notification to manager
                if manager:
                    success = notification_service.send_probation_ending_notification(
                        employee,
                        manager,
                        days_remaining=days_remaining,
                    )

                    if success:
                        if notice_type == "first":
                            results["first_notices_sent"] += 1
                        elif notice_type == "second":
                            results["second_notices_sent"] += 1
                        else:
                            results["final_notices_sent"] += 1

            except Exception as e:
                logger.error(
                    "Failed to process probation notification for employee %s: %s",
                    employee.employee_id,
                    e,
                )
                results["errors"].append({
                    "employee_id": str(employee.employee_id),
                    "error": str(e),
                })

    total_sent = (
        results["first_notices_sent"] +
        results["second_notices_sent"] +
        results["final_notices_sent"]
    )
    logger.info("Probation notifications complete: %d sent", total_sent)

    return results


@shared_task
def process_contract_expiry_notifications() -> dict:
    """
    Send notifications for employees whose contracts are expiring soon.

    Sends notifications:
    - 30 days before contract expires
    - 14 days before contract expires
    - 7 days before contract expires

    Returns:
        Dict with notification statistics
    """
    from app.services.hr_notifications import HRNotificationService

    FIRST_NOTICE_DAYS = 30
    SECOND_NOTICE_DAYS = 14
    FINAL_NOTICE_DAYS = 7

    logger.info("Processing contract expiry notifications")

    results: dict[str, Any] = {
        "notifications_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        today = date.today()

        contract_end_attr = getattr(Employee, "contract_end_date", None)
        if contract_end_attr is None:
            logger.info("Employee.contract_end_date not available; skipping contract expiry notifications")
            return results

        # Find employees with contract end dates
        contract_employees = db.scalars(
            select(Employee)
            .where(
                Employee.status == EmployeeStatus.ACTIVE,
                contract_end_attr.isnot(None),
            )
        ).all()

        notification_service = HRNotificationService(db)

        for employee in contract_employees:
            try:
                contract_end = getattr(employee, "contract_end_date", None)
                if contract_end is None:
                    continue
                days_remaining = (contract_end - today).days

                # Skip if contract already ended or too far away
                if days_remaining < 0 or days_remaining > FIRST_NOTICE_DAYS:
                    continue

                # Only send on specific days
                if days_remaining not in [FIRST_NOTICE_DAYS, SECOND_NOTICE_DAYS, FINAL_NOTICE_DAYS]:
                    continue

                # Get manager
                manager = None
                if employee.reports_to_id:
                    manager = db.get(Employee, employee.reports_to_id)

                # Send notification to manager and HR
                if manager:
                    success = notification_service.send_contract_expiry_notification(
                        employee,
                        manager,
                        days_remaining=days_remaining,
                    )

                    if success:
                        results["notifications_sent"] += 1

            except Exception as e:
                logger.error(
                    "Failed to process contract expiry notification for employee %s: %s",
                    employee.employee_id,
                    e,
                )
                results["errors"].append({
                    "employee_id": str(employee.employee_id),
                    "error": str(e),
                })

    logger.info("Contract expiry notifications complete: %d sent", results["notifications_sent"])

    return results


@shared_task
def process_work_anniversary_notifications() -> dict:
    """
    Send notifications for employee work anniversaries.

    Sends notifications for employees with work anniversaries this week.

    Returns:
        Dict with notification statistics
    """
    from app.services.hr_notifications import HRNotificationService

    logger.info("Processing work anniversary notifications")

    results: dict[str, Any] = {
        "notifications_sent": 0,
        "milestone_notifications": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        today = date.today()
        week_end = today + timedelta(days=7)

        # Find active employees
        active_employees = db.scalars(
            select(Employee)
            .where(
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.date_of_joining.isnot(None),
            )
        ).all()

        notification_service = HRNotificationService(db)

        for employee in active_employees:
            try:
                joining_date = employee.date_of_joining

                # Calculate this year's anniversary
                this_year_anniversary = joining_date.replace(year=today.year)

                # Check if anniversary is within this week
                if not (today <= this_year_anniversary <= week_end):
                    continue

                # Calculate years of service
                years_of_service = today.year - joining_date.year

                # Determine if it's a milestone year (5, 10, 15, 20, 25, etc.)
                is_milestone = years_of_service > 0 and years_of_service % 5 == 0

                # Get manager
                manager = None
                if employee.reports_to_id:
                    manager = db.get(Employee, employee.reports_to_id)

                # Send notification
                success = notification_service.send_work_anniversary_notification(
                    employee,
                    manager,
                    years_of_service=years_of_service,
                    is_milestone=is_milestone,
                )

                if success:
                    results["notifications_sent"] += 1
                    if is_milestone:
                        results["milestone_notifications"] += 1

            except Exception as e:
                logger.error(
                    "Failed to process anniversary notification for employee %s: %s",
                    employee.employee_id,
                    e,
                )
                results["errors"].append({
                    "employee_id": str(employee.employee_id),
                    "error": str(e),
                })

    logger.info(
        "Work anniversary notifications complete: %d sent (%d milestones)",
        results["notifications_sent"],
        results["milestone_notifications"],
    )

    return results


@shared_task
def process_birthday_notifications() -> dict:
    """
    Send notifications for employee birthdays.

    Sends notifications for employees with birthdays today or tomorrow.

    Returns:
        Dict with notification statistics
    """
    from app.services.hr_notifications import HRNotificationService

    logger.info("Processing birthday notifications")

    results: dict[str, Any] = {
        "notifications_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        today = date.today()
        tomorrow = today + timedelta(days=1)

        # Find active employees
        active_employees = db.scalars(
            select(Employee)
            .where(
                Employee.status == EmployeeStatus.ACTIVE,
                Employee.date_of_birth.isnot(None),
            )
        ).all()

        notification_service = HRNotificationService(db)

        for employee in active_employees:
            try:
                birthday = employee.date_of_birth
                if birthday is None:
                    continue

                # Check if birthday is today or tomorrow
                this_year_birthday = birthday.replace(year=today.year)

                if this_year_birthday == today:
                    notification_type = "today"
                elif this_year_birthday == tomorrow:
                    notification_type = "tomorrow"
                else:
                    continue

                # Get manager
                manager = None
                if employee.reports_to_id:
                    manager = db.get(Employee, employee.reports_to_id)

                # Send notification to manager
                if manager and notification_type == "tomorrow":
                    success = notification_service.send_birthday_notification(
                        employee,
                        manager,
                        is_advance_notice=True,
                    )

                    if success:
                        results["notifications_sent"] += 1

            except Exception as e:
                logger.error(
                    "Failed to process birthday notification for employee %s: %s",
                    employee.employee_id,
                    e,
                )
                results["errors"].append({
                    "employee_id": str(employee.employee_id),
                    "error": str(e),
                })

    logger.info("Birthday notifications complete: %d sent", results["notifications_sent"])

    return results


@shared_task
def process_performance_review_reminders() -> dict:
    """
    Send reminders for upcoming performance reviews.

    Checks appraisal cycles and sends reminders:
    - Self-assessment due reminders
    - Manager review due reminders
    - Calibration deadline reminders

    Returns:
        Dict with reminder statistics
    """
    from app.models.people.perf.appraisal import Appraisal, AppraisalStatus
    from app.models.people.perf.appraisal_cycle import AppraisalCycle, AppraisalCycleStatus
    from app.services.hr_notifications import HRNotificationService

    logger.info("Processing performance review reminders")

    results: dict[str, Any] = {
        "self_assessment_reminders": 0,
        "manager_review_reminders": 0,
        "calibration_reminders": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        today = date.today()

        # Find active cycles
        active_cycles = db.scalars(
            select(AppraisalCycle)
            .where(
                AppraisalCycle.status.in_([
                    AppraisalCycleStatus.ACTIVE,
                    AppraisalCycleStatus.REVIEW,
                    AppraisalCycleStatus.CALIBRATION,
                ]),
            )
        ).all()

        notification_service = HRNotificationService(db)

        for cycle in active_cycles:
            try:
                # Check self-assessment deadline
                if cycle.self_assessment_deadline:
                    days_to_deadline = (cycle.self_assessment_deadline - today).days
                    if 0 <= days_to_deadline <= 7:
                        # Find employees with pending self-assessment
                        pending_appraisals = db.scalars(
                            select(Appraisal)
                            .where(
                                Appraisal.cycle_id == cycle.cycle_id,
                                Appraisal.status.in_([
                                    AppraisalStatus.DRAFT,
                                    AppraisalStatus.SELF_ASSESSMENT,
                                ]),
                            )
                        ).all()

                        for appraisal in pending_appraisals:
                            employee = db.get(Employee, appraisal.employee_id)
                            if employee:
                                success = notification_service.send_self_assessment_reminder(
                                    employee,
                                    cycle,
                                    days_remaining=days_to_deadline,
                                )
                                if success:
                                    results["self_assessment_reminders"] += 1

                # Check manager review deadline
                if cycle.manager_review_deadline:
                    days_to_deadline = (cycle.manager_review_deadline - today).days
                    if 0 <= days_to_deadline <= 7:
                        # Find appraisals pending manager review
                        pending_reviews = db.scalars(
                            select(Appraisal)
                            .where(
                                Appraisal.cycle_id == cycle.cycle_id,
                                Appraisal.status.in_([
                                    AppraisalStatus.PENDING_REVIEW,
                                    AppraisalStatus.UNDER_REVIEW,
                                ]),
                            )
                        ).all()

                        for appraisal in pending_reviews:
                            manager = db.get(Employee, appraisal.manager_id)
                            employee = db.get(Employee, appraisal.employee_id)
                            if manager and employee:
                                success = notification_service.send_manager_review_reminder(
                                    manager,
                                    employee,
                                    cycle,
                                    days_remaining=days_to_deadline,
                                )
                                if success:
                                    results["manager_review_reminders"] += 1

            except Exception as e:
                logger.error(
                    "Failed to process review reminders for cycle %s: %s",
                    cycle.cycle_id,
                    e,
                )
                results["errors"].append({
                    "cycle_id": str(cycle.cycle_id),
                    "error": str(e),
                })

    total_sent = (
        results["self_assessment_reminders"] +
        results["manager_review_reminders"] +
        results["calibration_reminders"]
    )
    logger.info("Performance review reminders complete: %d sent", total_sent)

    return results


@shared_task
def process_certification_expiry_notifications() -> dict:
    """
    Send notifications for expiring employee certifications.

    Sends notifications:
    - 60 days before expiry
    - 30 days before expiry
    - 7 days before expiry

    Returns:
        Dict with notification statistics
    """
    from app.models.people.hr.employee_ext import EmployeeCertification
    from app.services.hr_notifications import HRNotificationService

    FIRST_NOTICE_DAYS = 60
    SECOND_NOTICE_DAYS = 30
    FINAL_NOTICE_DAYS = 7

    logger.info("Processing certification expiry notifications")

    results: dict[str, Any] = {
        "notifications_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        today = date.today()

        # Find certifications with expiry dates
        expiring_certs = db.scalars(
            select(EmployeeCertification)
            .where(
                EmployeeCertification.valid_until.isnot(None),
                EmployeeCertification.valid_until >= today,
                EmployeeCertification.valid_until <= today + timedelta(days=FIRST_NOTICE_DAYS),
            )
        ).all()

        notification_service = HRNotificationService(db)

        for cert in expiring_certs:
            try:
                valid_until = cert.valid_until
                if valid_until is None:
                    continue
                days_remaining = (valid_until - today).days

                # Only send on specific days
                if days_remaining not in [FIRST_NOTICE_DAYS, SECOND_NOTICE_DAYS, FINAL_NOTICE_DAYS]:
                    continue

                employee = db.get(Employee, cert.employee_id)
                if not employee:
                    continue

                # Send notification to employee
                success = notification_service.send_certification_expiry_notification(
                    employee,
                    cert,
                    days_remaining=days_remaining,
                )

                if success:
                    results["notifications_sent"] += 1

            except Exception as e:
                logger.error(
                    "Failed to process certification expiry notification for cert %s: %s",
                    cert.certification_id,
                    e,
                )
                results["errors"].append({
                    "certification_id": str(cert.certification_id),
                    "error": str(e),
                })

    logger.info("Certification expiry notifications complete: %d sent", results["notifications_sent"])

    return results


@shared_task
def calculate_hr_analytics(organization_id: str) -> dict:
    """
    Calculate HR analytics for reporting dashboards.

    Generates aggregate statistics for:
    - Headcount by department
    - Attrition rates
    - Average tenure
    - Upcoming reviews and expirations

    Args:
        organization_id: UUID of the organization

    Returns:
        Dict with calculated analytics
    """
    logger.info("Calculating HR analytics for org %s", organization_id)

    with SessionLocal() as db:
        try:
            org_id = uuid.UUID(organization_id)
            today = date.today()

            # Get active employee count
            active_count = db.scalar(
                select(func.count(Employee.employee_id))
                .where(
                    Employee.organization_id == org_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                )
            ) or 0

            # Get employees on probation
            on_probation = db.scalar(
                select(func.count(Employee.employee_id))
                .where(
                    Employee.organization_id == org_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                    Employee.probation_end_date.isnot(None),
                    Employee.probation_end_date >= today,
                )
            ) or 0

            # Get employees with expiring contracts (next 90 days)
            expiring_contracts = 0
            contract_end_attr = getattr(Employee, "contract_end_date", None)
            if contract_end_attr is not None:
                expiring_contracts = db.scalar(
                    select(func.count(Employee.employee_id))
                    .where(
                        Employee.organization_id == org_id,
                        Employee.status == EmployeeStatus.ACTIVE,
                        contract_end_attr.isnot(None),
                        contract_end_attr >= today,
                        contract_end_attr <= today + timedelta(days=90),
                    )
                ) or 0

            # Calculate average tenure
            employees_with_joining = db.scalars(
                select(Employee)
                .where(
                    Employee.organization_id == org_id,
                    Employee.status == EmployeeStatus.ACTIVE,
                    Employee.date_of_joining.isnot(None),
                )
            ).all()

            total_tenure_days = 0
            for emp in employees_with_joining:
                if emp.date_of_joining:
                    total_tenure_days += (today - emp.date_of_joining).days

            avg_tenure_years = (
                (total_tenure_days / len(employees_with_joining) / 365)
                if employees_with_joining else 0
            )

            return {
                "success": True,
                "organization_id": organization_id,
                "date": str(today),
                "headcount": {
                    "active_employees": active_count,
                    "on_probation": on_probation,
                    "expiring_contracts_90d": expiring_contracts,
                },
                "tenure": {
                    "avg_years": round(avg_tenure_years, 1),
                    "employees_counted": len(employees_with_joining),
                },
            }

        except Exception as e:
            logger.exception("HR analytics calculation failed: %s", e)
            return {
                "success": False,
                "error": str(e),
            }


# ==============================================================================
# Onboarding Tasks
# ==============================================================================


@shared_task
def process_onboarding_overdue_activities() -> dict:
    """
    Update overdue flags for onboarding activities across all organizations.

    Scans all pending onboarding activities and marks those past their due date
    as overdue. This task should run daily.

    Returns:
        Dict with processing statistics
    """
    from app.services.people.hr.onboarding import OnboardingService

    logger.info("Processing onboarding overdue activities")

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "activities_marked_overdue": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        # Get all organizations
        organizations = db.scalars(select(Organization)).all()

        for org in organizations:
            try:
                service = OnboardingService(db)
                count = service.update_overdue_flags(org.organization_id)

                results["organizations_processed"] += 1
                results["activities_marked_overdue"] += count

            except Exception as e:
                logger.error(
                    "Failed to process overdue activities for org %s: %s",
                    org.organization_id,
                    e,
                )
                results["errors"].append({
                    "organization_id": str(org.organization_id),
                    "error": str(e),
                })

        db.commit()

    logger.info(
        "Onboarding overdue processing complete: %d activities marked in %d orgs",
        results["activities_marked_overdue"],
        results["organizations_processed"],
    )

    return results


@shared_task
def process_onboarding_reminders() -> dict:
    """
    Send reminder notifications for onboarding activities.

    Sends notifications for:
    - Activities due within 2 days
    - Overdue activities (daily reminder until completed)

    Avoids duplicate reminders within 24 hours.

    Returns:
        Dict with notification statistics
    """
    from app.models.notification import EntityType, NotificationChannel, NotificationType
    from app.services.notification import NotificationService
    from app.services.people.hr.onboarding import OnboardingService
    from app.models.people.hr.lifecycle import EmployeeOnboarding

    logger.info("Processing onboarding reminders")

    results: dict[str, Any] = {
        "due_soon_reminders": 0,
        "overdue_reminders": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        notification_service = NotificationService()
        organizations = db.scalars(select(Organization)).all()

        for org in organizations:
            try:
                onboarding_service = OnboardingService(db)

                # Get activities needing reminders
                activities = onboarding_service.get_activities_needing_reminder(
                    org.organization_id,
                    days_before_due=2,
                    remind_if_overdue=True,
                    hours_since_last_reminder=24,
                )

                for activity in activities:
                    try:
                        # Determine recipient
                        recipient_id = activity.assignee_id

                        # If no specific assignee, get from onboarding record
                        if not recipient_id:
                            onboarding = db.get(EmployeeOnboarding, activity.onboarding_id)
                            if onboarding:
                                # For self-service tasks, notify the employee via their person_id
                                if activity.assigned_to_employee:
                                    employee = db.get(Employee, onboarding.employee_id)
                                    if employee:
                                        recipient_id = employee.person_id
                                # For manager tasks
                                elif activity.assignee_role == "MANAGER" and onboarding.manager_id:
                                    manager = db.get(Employee, onboarding.manager_id)
                                    if manager:
                                        recipient_id = manager.person_id
                                # For buddy tasks
                                elif activity.assignee_role == "BUDDY" and onboarding.buddy_employee_id:
                                    buddy = db.get(Employee, onboarding.buddy_employee_id)
                                    if buddy:
                                        recipient_id = buddy.person_id

                        if not recipient_id:
                            logger.warning(
                                "No recipient found for activity %s",
                                activity.activity_id,
                            )
                            continue

                        # Determine notification type
                        is_overdue = activity.is_overdue
                        notif_type = NotificationType.OVERDUE if is_overdue else NotificationType.DUE_SOON

                        # Build notification message
                        if is_overdue:
                            title = f"Overdue: {activity.activity_name}"
                            message = f"The onboarding task '{activity.activity_name}' is overdue. Please complete it as soon as possible."
                        else:
                            days_remaining = (activity.due_date - date.today()).days if activity.due_date else 0
                            title = f"Task Due Soon: {activity.activity_name}"
                            message = f"The onboarding task '{activity.activity_name}' is due in {days_remaining} day{'s' if days_remaining != 1 else ''}."

                        # Send notification
                        notification_service.create(
                            db,
                            organization_id=org.organization_id,
                            recipient_id=recipient_id,
                            entity_type=EntityType.SYSTEM,
                            entity_id=activity.activity_id,
                            notification_type=notif_type,
                            title=title,
                            message=message,
                            channel=NotificationChannel.BOTH,
                            action_url="/people/hr/onboarding",
                        )

                        # Mark reminder as sent
                        onboarding_service.mark_reminder_sent(activity.activity_id)

                        if is_overdue:
                            results["overdue_reminders"] += 1
                        else:
                            results["due_soon_reminders"] += 1

                    except Exception as e:
                        logger.error(
                            "Failed to send reminder for activity %s: %s",
                            activity.activity_id,
                            e,
                        )
                        results["errors"].append({
                            "activity_id": str(activity.activity_id),
                            "error": str(e),
                        })

            except Exception as e:
                logger.error(
                    "Failed to process reminders for org %s: %s",
                    org.organization_id,
                    e,
                )
                results["errors"].append({
                    "organization_id": str(org.organization_id),
                    "error": str(e),
                })

        db.commit()

    total_sent = results["due_soon_reminders"] + results["overdue_reminders"]
    logger.info(
        "Onboarding reminders complete: %d sent (%d due soon, %d overdue)",
        total_sent,
        results["due_soon_reminders"],
        results["overdue_reminders"],
    )

    return results


@shared_task
def send_welcome_email(onboarding_id: str) -> dict:
    """
    Send welcome email to a new hire with self-service portal link.

    Args:
        onboarding_id: UUID of the onboarding record

    Returns:
        Dict with result status
    """
    import os
    from app.services.email import send_email
    from app.services.people.hr.onboarding import OnboardingService
    from app.models.people.hr.lifecycle import EmployeeOnboarding

    logger.info("Sending welcome email for onboarding %s", onboarding_id)

    with SessionLocal() as db:
        try:
            onboarding = db.get(EmployeeOnboarding, uuid.UUID(onboarding_id))
            if not onboarding:
                return {"success": False, "error": "Onboarding not found"}

            if onboarding.self_service_email_sent:
                return {"success": True, "message": "Email already sent"}

            # Get employee details
            employee = db.get(Employee, onboarding.employee_id)
            if not employee or not employee.person:
                return {"success": False, "error": "Employee or person not found"}

            person = employee.person
            if not person.email:
                return {"success": False, "error": "No email address"}

            # Get organization
            org = db.get(Organization, onboarding.organization_id)
            if not org:
                return {"success": False, "error": "Organization not found"}

            # Generate a fresh token for the URL
            # SECURITY: Token is stored as hash, so we regenerate to get the raw token
            service = OnboardingService(db)
            raw_token = service.regenerate_self_service_token(
                onboarding.organization_id, onboarding.onboarding_id
            )

            # Build portal URL with raw token
            app_url = os.getenv("APP_URL", "http://localhost:8000")
            portal_url = f"{app_url.rstrip('/')}/onboarding/start/{raw_token}"

            # Build email content
            employee_name = person.display_name or f"{person.first_name} {person.last_name}"
            org_name = org.legal_name
            start_date = onboarding.date_of_joining.strftime("%B %d, %Y") if onboarding.date_of_joining else "TBD"

            subject = f"Welcome to {org_name} - Complete Your Onboarding"
            body_html = f"""
            <p>Dear {employee_name},</p>

            <p>Welcome to <strong>{org_name}</strong>! We're excited to have you join our team.</p>

            <p>Your start date is <strong>{start_date}</strong>.</p>

            <p>To complete your onboarding tasks, please access your personal onboarding portal:</p>

            <p><a href="{portal_url}" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">Access Onboarding Portal</a></p>

            <p>Through the portal, you'll be able to:</p>
            <ul>
                <li>Complete required forms and documents</li>
                <li>Upload necessary paperwork</li>
                <li>View your onboarding checklist and progress</li>
                <li>Access company information</li>
            </ul>

            <p>If you have any questions, please don't hesitate to reach out to HR.</p>

            <p>We look forward to seeing you soon!</p>

            <p>Best regards,<br>
            Human Resources<br>
            {org_name}</p>
            """

            body_text = f"""
Dear {employee_name},

Welcome to {org_name}! We're excited to have you join our team.

Your start date is {start_date}.

To complete your onboarding tasks, please access your personal onboarding portal:
{portal_url}

Through the portal, you'll be able to:
- Complete required forms and documents
- Upload necessary paperwork
- View your onboarding checklist and progress
- Access company information

If you have any questions, please don't hesitate to reach out to HR.

We look forward to seeing you soon!

Best regards,
Human Resources
{org_name}
            """

            # Send email
            success = send_email(
                db=db,
                to_email=person.email,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
            )

            if success:
                # Mark email as sent
                onboarding_service = OnboardingService(db)
                onboarding_service.mark_welcome_email_sent(
                    onboarding.organization_id,
                    onboarding.onboarding_id,
                )
                db.commit()

                logger.info(
                    "Welcome email sent to %s for onboarding %s",
                    person.email,
                    onboarding_id,
                )

                return {"success": True, "email": person.email}

            return {"success": False, "error": "Failed to send email"}

        except Exception as e:
            logger.exception("Failed to send welcome email: %s", e)
            return {"success": False, "error": str(e)}
