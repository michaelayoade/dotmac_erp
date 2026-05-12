"""
PMS Background Tasks — Celery tasks for OHCSF Performance Management System.

Handles:
- Monthly review reminders for supervisors
- Quarterly appraisal reminders for employees and supervisors
- Contract signing deadline checks
- Underperformance detection (quarterly and annual triggers)
- Probation milestone checks
- Appeal deadline checks
- PIP review interval reminders
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from celery import shared_task

from app.db import SessionLocal

logger = logging.getLogger(__name__)


def _window_start(day: date) -> datetime:
    """Return the start-of-day timestamp for reminder de-duplication."""
    return datetime.combine(day, datetime.min.time())


def _resolve_person_id(
    db,
    organization_id: UUID,
    employee_id: UUID | None,
) -> UUID | None:
    """Map an HR employee ID to the linked person ID for notifications."""
    if employee_id is None:
        return None

    from sqlalchemy import select

    from app.models.people.hr.employee import Employee, EmployeeStatus

    employee = db.scalar(
        select(Employee).where(
            Employee.organization_id == organization_id,
            Employee.employee_id == employee_id,
            Employee.status != EmployeeStatus.TERMINATED,
        )
    )
    return employee.person_id if employee is not None else None


@shared_task
def pms_monthly_review_reminder() -> dict[str, Any]:
    """
    Remind supervisors to complete monthly reviews for the previous month.

    Runs on the 1st of each month. Finds employees with active
    PerformanceContracts that are missing a MonthlyReview for the
    previous calendar month and notifies their supervisors.

    Returns:
        Dict with processing statistics.
    """
    logger.info("Starting pms_monthly_review_reminder")

    results: dict[str, Any] = {
        "orgs_checked": 0,
        "reminders_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.finance.core_org.organization import Organization
        from app.models.notification import (
            EntityType,
            NotificationChannel,
            NotificationType,
        )
        from app.models.people.perf.monthly_review import MonthlyReview
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus
        from app.services.notification import NotificationService

        today = date.today()
        # First day of the current month = day after last month ends
        first_of_this_month = today.replace(day=1)
        # Previous month: go back one day from the 1st to get last month's last day,
        # then take the 1st of that month
        last_month_end = first_of_this_month - timedelta(days=1)
        review_month = last_month_end.replace(day=1)

        notification_service = NotificationService()

        orgs = db.scalars(
            select(Organization).where(Organization.pms_ohcsf_enabled == True)  # noqa: E712
        ).all()

        for org in orgs:
            results["orgs_checked"] += 1
            try:
                # Active contracts for this org
                active_contracts = db.scalars(
                    select(PerformanceContract).where(
                        PerformanceContract.organization_id == org.organization_id,
                        PerformanceContract.status == ContractStatus.ACTIVE,
                    )
                ).all()

                for contract in active_contracts:
                    try:
                        # Check if monthly review already exists for last month
                        existing = db.scalar(
                            select(MonthlyReview).where(
                                MonthlyReview.organization_id == org.organization_id,
                                MonthlyReview.employee_id == contract.employee_id,
                                MonthlyReview.review_month == review_month,
                            )
                        )
                        if existing:
                            continue

                        supervisor_person_id = _resolve_person_id(
                            db, org.organization_id, contract.supervisor_id
                        )
                        if supervisor_person_id is None:
                            logger.warning(
                                "Skipping monthly review reminder for contract %s: supervisor person not found",
                                contract.contract_id,
                            )
                            continue

                        # Notify supervisor to complete the review
                        created = notification_service.create_if_not_sent_since(
                            db,
                            organization_id=org.organization_id,
                            recipient_id=supervisor_person_id,
                            entity_type=EntityType.EMPLOYEE,
                            entity_id=contract.contract_id,
                            notification_type=NotificationType.REMINDER,
                            title="Monthly Review Pending",
                            message=(
                                f"Monthly performance review for "
                                f"{review_month.strftime('%B %Y')} has not been completed. "
                                "Please complete the review."
                            ),
                            since=_window_start(first_of_this_month),
                            channel=NotificationChannel.BOTH,
                            action_url="/people/perf/pms/reviews",
                        )
                        if created is not None:
                            results["reminders_sent"] += 1

                    except Exception as e:
                        logger.exception(
                            "Failed to process monthly review reminder for contract %s: %s",
                            contract.contract_id,
                            e,
                        )
                        results["errors"].append(str(e))

            except Exception as e:
                logger.exception(
                    "Failed to process org %s for monthly review reminders: %s",
                    org.organization_id,
                    e,
                )
                results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Completed pms_monthly_review_reminder: %d reminders sent across %d orgs",
        results["reminders_sent"],
        results["orgs_checked"],
    )
    return results


@shared_task
def pms_quarterly_appraisal_reminder() -> dict[str, Any]:
    """
    Remind employees to begin self-assessment and supervisors to prepare for
    quarterly appraisals.

    Runs in the 1st week of Apr, Jul, Oct, and Dec (start of each appraisal
    quarter window). Targets active contracts in PMS-enabled orgs.

    Returns:
        Dict with processing statistics.
    """
    logger.info("Starting pms_quarterly_appraisal_reminder")

    results: dict[str, Any] = {
        "orgs_checked": 0,
        "employee_reminders_sent": 0,
        "supervisor_reminders_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.finance.core_org.organization import Organization
        from app.models.notification import (
            EntityType,
            NotificationChannel,
            NotificationType,
        )
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus
        from app.services.notification import NotificationService

        today = date.today()
        month = today.month
        # Only send during the 1st week of Apr(4), Jul(7), Oct(10), Dec(12)
        if month not in (4, 7, 10, 12) or today.day > 7:
            logger.info(
                "pms_quarterly_appraisal_reminder: not a quarterly reminder month/week (%s), skipping",
                today,
            )
            return results

        quarter_labels = {4: "Q1", 7: "Q2", 10: "Q3", 12: "Q4/Year-End"}
        quarter_label = quarter_labels.get(month, "Quarterly")

        notification_service = NotificationService()

        orgs = db.scalars(
            select(Organization).where(Organization.pms_ohcsf_enabled == True)  # noqa: E712
        ).all()

        for org in orgs:
            results["orgs_checked"] += 1
            try:
                active_contracts = db.scalars(
                    select(PerformanceContract).where(
                        PerformanceContract.organization_id == org.organization_id,
                        PerformanceContract.status == ContractStatus.ACTIVE,
                    )
                ).all()

                for contract in active_contracts:
                    try:
                        employee_person_id = _resolve_person_id(
                            db, org.organization_id, contract.employee_id
                        )
                        supervisor_person_id = _resolve_person_id(
                            db, org.organization_id, contract.supervisor_id
                        )

                        # Notify employee to begin self-assessment
                        if employee_person_id is not None:
                            created = notification_service.create_if_not_sent_since(
                                db,
                                organization_id=org.organization_id,
                                recipient_id=employee_person_id,
                                entity_type=EntityType.EMPLOYEE,
                                entity_id=contract.contract_id,
                                notification_type=NotificationType.DUE_SOON,
                                title=f"{quarter_label} Appraisal — Self-Assessment Due",
                                message=(
                                    f"Your {quarter_label} performance appraisal self-assessment "
                                    "is now open. Please complete it promptly."
                                ),
                                since=_window_start(today.replace(day=1)),
                                channel=NotificationChannel.BOTH,
                                action_url="/people/perf/pms/dashboard",
                            )
                            if created is not None:
                                results["employee_reminders_sent"] += 1

                        # Notify supervisor to prepare
                        if supervisor_person_id is not None:
                            created = notification_service.create_if_not_sent_since(
                                db,
                                organization_id=org.organization_id,
                                recipient_id=supervisor_person_id,
                                entity_type=EntityType.EMPLOYEE,
                                entity_id=contract.contract_id,
                                notification_type=NotificationType.DUE_SOON,
                                title=f"{quarter_label} Appraisal — Supervisor Review Upcoming",
                                message=(
                                    f"The {quarter_label} appraisal period has begun. "
                                    "Prepare to review your direct reports."
                                ),
                                since=_window_start(today.replace(day=1)),
                                channel=NotificationChannel.BOTH,
                                action_url="/people/perf/pms/dashboard",
                            )
                            if created is not None:
                                results["supervisor_reminders_sent"] += 1

                    except Exception as e:
                        logger.exception(
                            "Failed to send quarterly appraisal reminder for contract %s: %s",
                            contract.contract_id,
                            e,
                        )
                        results["errors"].append(str(e))

            except Exception as e:
                logger.exception(
                    "Failed to process org %s for quarterly appraisal reminders: %s",
                    org.organization_id,
                    e,
                )
                results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Completed pms_quarterly_appraisal_reminder: %d employee + %d supervisor reminders sent",
        results["employee_reminders_sent"],
        results["supervisor_reminders_sent"],
    )
    return results


@shared_task
def pms_contract_deadline_check() -> dict[str, Any]:
    """
    Flag employees without signed performance contracts past the 3rd week of January.

    Runs daily in January. Employees are expected to have their contracts
    signed (ACTIVE) by the end of the 3rd week. Notifies HR for each
    employee still on DRAFT or PENDING_SIGNATURE status.

    Returns:
        Dict with processing statistics.
    """
    logger.info("Starting pms_contract_deadline_check")

    results: dict[str, Any] = {
        "orgs_checked": 0,
        "contracts_flagged": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.finance.core_org.organization import Organization
        from app.models.notification import (
            EntityType,
            NotificationChannel,
            NotificationType,
        )
        from app.models.people.perf.performance_contract import PerformanceContract
        from app.models.people.perf.pms_enums import ContractStatus
        from app.services.notification import NotificationService

        today = date.today()
        # Only run in January after the 3rd week (day > 21)
        if today.month != 1 or today.day <= 21:
            logger.info(
                "pms_contract_deadline_check: not in January deadline window (%s), skipping",
                today,
            )
            return results

        notification_service = NotificationService()
        unsigned_statuses = [ContractStatus.DRAFT, ContractStatus.PENDING_SIGNATURE]

        orgs = db.scalars(
            select(Organization).where(Organization.pms_ohcsf_enabled == True)  # noqa: E712
        ).all()

        for org in orgs:
            results["orgs_checked"] += 1
            try:
                # Find contracts not yet signed/active
                unsigned_contracts = db.scalars(
                    select(PerformanceContract).where(
                        PerformanceContract.organization_id == org.organization_id,
                        PerformanceContract.status.in_(unsigned_statuses),
                    )
                ).all()

                for contract in unsigned_contracts:
                    try:
                        supervisor_person_id = _resolve_person_id(
                            db, org.organization_id, contract.supervisor_id
                        )
                        if supervisor_person_id is None:
                            logger.warning(
                                "Skipping unsigned contract alert for %s: supervisor person not found",
                                contract.contract_id,
                            )
                            continue

                        # Get HR officer — notify supervisor as proxy for HR alert
                        created = notification_service.create_if_not_sent_since(
                            db,
                            organization_id=org.organization_id,
                            recipient_id=supervisor_person_id,
                            entity_type=EntityType.EMPLOYEE,
                            entity_id=contract.contract_id,
                            notification_type=NotificationType.OVERDUE,
                            title="Performance Contract Unsigned — Deadline Passed",
                            message=(
                                f"Contract {contract.contract_code} has not been signed. "
                                "The 3rd-week-of-January deadline has passed. "
                                "Please ensure the contract is finalised immediately."
                            ),
                            since=_window_start(date(today.year, 1, 22)),
                            channel=NotificationChannel.BOTH,
                            action_url=f"/people/perf/pms/contracts/{contract.contract_id}",
                        )
                        if created is not None:
                            results["contracts_flagged"] += 1

                    except Exception as e:
                        logger.exception(
                            "Failed to flag unsigned contract %s: %s",
                            contract.contract_id,
                            e,
                        )
                        results["errors"].append(str(e))

            except Exception as e:
                logger.exception(
                    "Failed to process org %s for contract deadline check: %s",
                    org.organization_id,
                    e,
                )
                results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Completed pms_contract_deadline_check: %d contracts flagged across %d orgs",
        results["contracts_flagged"],
        results["orgs_checked"],
    )
    return results


@shared_task
def pms_underperformance_detection() -> dict[str, Any]:
    """
    Detect and flag underperforming employees after each quarter closes.

    Runs in the 1st week of Apr, Jul, Oct, and the 2nd week of Jan (after
    Q4/year-end). Calls UnderperformanceService.detect_quarterly_trigger()
    for all active cycles, and detect_annual_trigger() in January.

    Returns:
        Dict with processing statistics.
    """
    logger.info("Starting pms_underperformance_detection")

    results: dict[str, Any] = {
        "orgs_checked": 0,
        "quarterly_flagged": 0,
        "annual_flagged": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.finance.core_org.organization import Organization
        from app.models.people.perf.appraisal_cycle import (
            AppraisalCycle,
            AppraisalCycleStatus,
        )
        from app.services.people.perf.underperformance_service import (
            UnderperformanceService,
        )

        today = date.today()
        month = today.month
        day = today.day

        # Quarterly: 1st week of Apr(4), Jul(7), Oct(10); Annual: 2nd week of Jan(1)
        run_quarterly = month in (4, 7, 10) and day <= 7
        run_annual = month == 1 and 8 <= day <= 14

        if not run_quarterly and not run_annual:
            logger.info(
                "pms_underperformance_detection: not in a detection window (%s), skipping",
                today,
            )
            return results

        orgs = db.scalars(
            select(Organization).where(Organization.pms_ohcsf_enabled == True)  # noqa: E712
        ).all()

        for org in orgs:
            results["orgs_checked"] += 1
            try:
                service = UnderperformanceService(db)

                # Find active appraisal cycles for this org
                active_cycles = db.scalars(
                    select(AppraisalCycle).where(
                        AppraisalCycle.organization_id == org.organization_id,
                        AppraisalCycle.status == AppraisalCycleStatus.ACTIVE,
                    )
                ).all()

                for cycle in active_cycles:
                    try:
                        if run_quarterly:
                            flagged = service.detect_quarterly_trigger(
                                org_id=org.organization_id,
                                cycle_id=cycle.cycle_id,
                            )
                            results["quarterly_flagged"] += len(flagged)
                            logger.info(
                                "Quarterly detection: org=%s cycle=%s flagged=%d",
                                org.organization_id,
                                cycle.cycle_id,
                                len(flagged),
                            )

                        if run_annual:
                            flagged = service.detect_annual_trigger(
                                org_id=org.organization_id,
                                cycle_id=cycle.cycle_id,
                            )
                            results["annual_flagged"] += len(flagged)
                            logger.info(
                                "Annual detection: org=%s cycle=%s flagged=%d",
                                org.organization_id,
                                cycle.cycle_id,
                                len(flagged),
                            )

                    except Exception as e:
                        logger.exception(
                            "Failed to run underperformance detection for cycle %s: %s",
                            cycle.cycle_id,
                            e,
                        )
                        results["errors"].append(str(e))

            except Exception as e:
                logger.exception(
                    "Failed to process org %s for underperformance detection: %s",
                    org.organization_id,
                    e,
                )
                results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Completed pms_underperformance_detection: quarterly=%d annual=%d",
        results["quarterly_flagged"],
        results["annual_flagged"],
    )
    return results


@shared_task
def pms_probation_check() -> dict[str, Any]:
    """
    Check probation milestones for employees in PMS-enabled organisations.

    Runs monthly. Calls UnderperformanceService.check_probation_milestones()
    and notifies line managers for employees approaching the 18, 20, and
    21-month service milestones who have not yet been confirmed.

    Returns:
        Dict with processing statistics.
    """
    logger.info("Starting pms_probation_check")

    results: dict[str, Any] = {
        "orgs_checked": 0,
        "notifications_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.finance.core_org.organization import Organization
        from app.models.notification import (
            EntityType,
            NotificationChannel,
            NotificationType,
        )
        from app.models.people.hr.employee import Employee, EmployeeStatus
        from app.services.notification import NotificationService
        from app.services.people.perf.underperformance_service import (
            UnderperformanceService,
        )

        today = date.today()
        notification_service = NotificationService()
        first_of_month = today.replace(day=1)

        orgs = db.scalars(
            select(Organization).where(Organization.pms_ohcsf_enabled == True)  # noqa: E712
        ).all()

        for org in orgs:
            results["orgs_checked"] += 1
            try:
                service = UnderperformanceService(db)
                milestones = service.check_probation_milestones(
                    org_id=org.organization_id
                )

                for milestone in milestones:
                    try:
                        employee_id = milestone.get("employee_id")
                        months_served = milestone.get("months_of_service", 0)

                        if not employee_id:
                            continue

                        employee = db.get(Employee, employee_id)
                        if (
                            employee is None
                            or employee.organization_id != org.organization_id
                            or employee.status == EmployeeStatus.TERMINATED
                        ):
                            logger.warning(
                                "Skipping probation milestone alert for %s: employee not found in org",
                                employee_id,
                            )
                            continue

                        recipient_person_id = _resolve_person_id(
                            db, org.organization_id, employee.reports_to_id
                        )
                        if recipient_person_id is None:
                            logger.warning(
                                "Skipping probation milestone alert for %s: supervisor person not found",
                                employee_id,
                            )
                            continue

                        # Determine milestone label
                        if months_served >= 21:
                            label = "21-month (final probation milestone)"
                        elif months_served >= 20:
                            label = "20-month probation milestone"
                        else:
                            label = "18-month probation milestone"

                        created = notification_service.create_if_not_sent_since(
                            db,
                            organization_id=org.organization_id,
                            recipient_id=recipient_person_id,
                            entity_type=EntityType.EMPLOYEE,
                            entity_id=employee_id,
                            notification_type=NotificationType.ALERT,
                            title=f"Probation Milestone: {label}",
                            message=(
                                f"Your direct report has reached the {label} and has not yet "
                                "been confirmed. A Progress Report is required. "
                                "Please take action immediately."
                            ),
                            since=_window_start(first_of_month),
                            channel=NotificationChannel.BOTH,
                            action_url="/people/perf/pms/dashboard",
                        )
                        if created is not None:
                            results["notifications_sent"] += 1

                    except Exception as e:
                        logger.exception(
                            "Failed to notify probation milestone for employee %s: %s",
                            milestone.get("employee_id"),
                            e,
                        )
                        results["errors"].append(str(e))

            except Exception as e:
                logger.exception(
                    "Failed to process org %s for probation check: %s",
                    org.organization_id,
                    e,
                )
                results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Completed pms_probation_check: %d notifications sent across %d orgs",
        results["notifications_sent"],
        results["orgs_checked"],
    )
    return results


@shared_task
def pms_appeal_deadline_check() -> dict[str, Any]:
    """
    Flag unresolved appeals approaching the February 28 OHCSF deadline.

    Runs weekly in January and February. Identifies appeals with status
    FILED, UNDER_MEDIATION, or REFERRED_TO_COMMITTEE that are not yet
    resolved, and notifies the relevant parties.

    Returns:
        Dict with processing statistics.
    """
    logger.info("Starting pms_appeal_deadline_check")

    results: dict[str, Any] = {
        "orgs_checked": 0,
        "appeals_flagged": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.finance.core_org.organization import Organization
        from app.models.notification import (
            EntityType,
            NotificationChannel,
            NotificationType,
        )
        from app.models.people.perf.appraisal_appeal import AppraisalAppeal
        from app.models.people.perf.pms_enums import AppealStatus
        from app.services.notification import NotificationService

        today = date.today()
        # Only run in January and February
        if today.month not in (1, 2):
            logger.info(
                "pms_appeal_deadline_check: not in Jan/Feb (%s), skipping",
                today,
            )
            return results

        # OHCSF appeal deadline: February 28 of the current year
        appeal_deadline = date(today.year, 2, 28)
        days_to_deadline = (appeal_deadline - today).days

        if days_to_deadline < 0:
            logger.info(
                "pms_appeal_deadline_check: appeal deadline already passed for %d, skipping",
                today.year,
            )
            return results

        notification_service = NotificationService()
        open_appeal_statuses = [
            AppealStatus.FILED,
            AppealStatus.UNDER_MEDIATION,
            AppealStatus.REFERRED_TO_COMMITTEE,
        ]

        orgs = db.scalars(
            select(Organization).where(Organization.pms_ohcsf_enabled == True)  # noqa: E712
        ).all()

        for org in orgs:
            results["orgs_checked"] += 1
            try:
                open_appeals = db.scalars(
                    select(AppraisalAppeal).where(
                        AppraisalAppeal.organization_id == org.organization_id,
                        AppraisalAppeal.status.in_(open_appeal_statuses),
                    )
                ).all()

                for appeal in open_appeals:
                    try:
                        employee_person_id = _resolve_person_id(
                            db, org.organization_id, appeal.employee_id
                        )
                        if employee_person_id is None:
                            logger.warning(
                                "Skipping appeal deadline alert for %s: employee person not found",
                                appeal.appeal_id,
                            )
                            continue

                        created = notification_service.create_if_not_sent_since(
                            db,
                            organization_id=org.organization_id,
                            recipient_id=employee_person_id,
                            entity_type=EntityType.EMPLOYEE,
                            entity_id=appeal.appeal_id,
                            notification_type=NotificationType.DUE_SOON,
                            title="Unresolved Appeal — Deadline Approaching",
                            message=(
                                f"Your performance appraisal appeal is unresolved. "
                                f"The OHCSF deadline is 28 February "
                                f"({days_to_deadline} days remaining). "
                                "Please ensure it is resolved before the deadline."
                            ),
                            since=_window_start(today - timedelta(days=7)),
                            channel=NotificationChannel.BOTH,
                            action_url=f"/people/perf/pms/appeals/{appeal.appeal_id}",
                        )
                        if created is not None:
                            results["appeals_flagged"] += 1

                    except Exception as e:
                        logger.exception(
                            "Failed to notify appeal deadline for appeal %s: %s",
                            appeal.appeal_id,
                            e,
                        )
                        results["errors"].append(str(e))

            except Exception as e:
                logger.exception(
                    "Failed to process org %s for appeal deadline check: %s",
                    org.organization_id,
                    e,
                )
                results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Completed pms_appeal_deadline_check: %d appeals flagged across %d orgs",
        results["appeals_flagged"],
        results["orgs_checked"],
    )
    return results


@shared_task
def pms_pip_review_reminder() -> dict[str, Any]:
    """
    Remind supervisors and HR officers of upcoming PIP review intervals.

    Runs weekly. Checks the review_intervals JSON field on active PIPs
    for any review dates falling within the next 7 days and sends
    notifications to both the supervisor and HR officer.

    Returns:
        Dict with processing statistics.
    """
    logger.info("Starting pms_pip_review_reminder")

    results: dict[str, Any] = {
        "orgs_checked": 0,
        "reminders_sent": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        from sqlalchemy import select

        from app.models.finance.core_org.organization import Organization
        from app.models.notification import (
            EntityType,
            NotificationChannel,
            NotificationType,
        )
        from app.models.people.perf.pip import PerformanceImprovementPlan
        from app.models.people.perf.pms_enums import PIPStatus
        from app.services.notification import NotificationService

        today = date.today()
        window_end = today + timedelta(days=7)
        notification_service = NotificationService()

        active_pip_statuses = [
            PIPStatus.ACTIVE,
            PIPStatus.UNDER_REVIEW,
            PIPStatus.EXTENDED,
        ]

        orgs = db.scalars(
            select(Organization).where(Organization.pms_ohcsf_enabled == True)  # noqa: E712
        ).all()

        for org in orgs:
            results["orgs_checked"] += 1
            try:
                active_pips = db.scalars(
                    select(PerformanceImprovementPlan).where(
                        PerformanceImprovementPlan.organization_id
                        == org.organization_id,
                        PerformanceImprovementPlan.status.in_(active_pip_statuses),
                    )
                ).all()

                for pip in active_pips:
                    try:
                        review_intervals = pip.review_intervals or []
                        if not isinstance(review_intervals, list):
                            continue

                        for interval in review_intervals:
                            try:
                                # Expect each interval to have a "review_date" key (ISO format string)
                                if not isinstance(interval, dict):
                                    continue
                                interval_date_str = interval.get("review_date")
                                if not interval_date_str:
                                    continue

                                interval_date = date.fromisoformat(
                                    str(interval_date_str)
                                )
                                if not (today <= interval_date <= window_end):
                                    continue

                                days_until = (interval_date - today).days
                                interval_label = (
                                    f"Review on {interval_date.strftime('%d %b %Y')}"
                                )

                                supervisor_person_id = _resolve_person_id(
                                    db, org.organization_id, pip.supervisor_id
                                )
                                hr_person_id = _resolve_person_id(
                                    db, org.organization_id, pip.hr_officer_id
                                )

                                # Notify supervisor
                                since = _window_start(today - timedelta(days=7))
                                if supervisor_person_id is not None:
                                    created = notification_service.create_if_not_sent_since(
                                        db,
                                        organization_id=org.organization_id,
                                        recipient_id=supervisor_person_id,
                                        entity_type=EntityType.EMPLOYEE,
                                        entity_id=pip.pip_id,
                                        notification_type=NotificationType.DUE_SOON,
                                        title=f"PIP Review Due: {interval_label}",
                                        message=(
                                            f"A PIP review checkpoint ({interval_label}) is due "
                                            f"in {days_until} day(s) on "
                                            f"{interval_date.strftime('%d %b %Y')}. "
                                            "Please prepare for the review meeting."
                                        ),
                                        since=since,
                                        channel=NotificationChannel.BOTH,
                                        action_url=f"/people/perf/pms/pips/{pip.pip_id}",
                                    )
                                    if created is not None:
                                        results["reminders_sent"] += 1

                                # Notify HR officer
                                if hr_person_id is not None:
                                    created = notification_service.create_if_not_sent_since(
                                        db,
                                        organization_id=org.organization_id,
                                        recipient_id=hr_person_id,
                                        entity_type=EntityType.EMPLOYEE,
                                        entity_id=pip.pip_id,
                                        notification_type=NotificationType.DUE_SOON,
                                        title=f"PIP Review Due: {interval_label}",
                                        message=(
                                            f"PIP {pip.pip_code} has a review checkpoint "
                                            f"({interval_label}) due in {days_until} day(s) on "
                                            f"{interval_date.strftime('%d %b %Y')}."
                                        ),
                                        since=since,
                                        channel=NotificationChannel.IN_APP,
                                        action_url=f"/people/perf/pms/pips/{pip.pip_id}",
                                    )
                                    if created is not None:
                                        results["reminders_sent"] += 1

                            except (ValueError, TypeError) as e:
                                logger.warning(
                                    "Invalid review interval entry in PIP %s: %s",
                                    pip.pip_id,
                                    e,
                                )

                    except Exception as e:
                        logger.exception(
                            "Failed to process PIP review reminders for pip %s: %s",
                            pip.pip_id,
                            e,
                        )
                        results["errors"].append(str(e))

            except Exception as e:
                logger.exception(
                    "Failed to process org %s for PIP review reminders: %s",
                    org.organization_id,
                    e,
                )
                results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Completed pms_pip_review_reminder: %d reminders sent across %d orgs",
        results["reminders_sent"],
        results["orgs_checked"],
    )
    return results
