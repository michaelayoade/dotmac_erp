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
