"""
Performance Review Background Tasks - Celery tasks for performance cycle automation.

Handles:
- Automatic cycle phase transitions based on deadlines
- Appraisal generation for eligible employees
- Progress tracking and reporting
- Deadline notification scheduling
"""

import logging
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from app.db.session_context import cross_org_session, session_for_org
from app.models.people.perf.appraisal_cycle import AppraisalCycle, AppraisalCycleStatus
from app.tenant_catalog import organization_ids

logger = logging.getLogger(__name__)


def _list_organization_ids() -> list[UUID]:
    return organization_ids(include_inactive=True)


@shared_task
def process_cycle_phase_transitions() -> dict:
    """
    Process automatic cycle phase transitions based on deadlines.

    Checks all active, review, and calibration cycles and advances them
    to the next phase when their respective deadlines have passed.

    Returns:
        Dict with transition statistics
    """
    from app.services.performance_automation import PerformanceAutomationService

    logger.info("Processing cycle phase transitions")

    results: dict[str, Any] = {
        "transitions": [],
        "errors": [],
    }

    with cross_org_session() as cross_db:
        transitions = [
            (cycle.cycle_id, cycle.organization_id, target_status)
            for cycle, target_status in PerformanceAutomationService(
                cross_db
            ).get_cycles_ready_for_transition()
        ]

    for cycle_id, org_id, target_status in transitions:
        with session_for_org(org_id) as db:
            service = PerformanceAutomationService(db)
            try:
                cycle = db.get(AppraisalCycle, cycle_id)
                if not cycle:
                    results["errors"].append(
                        {
                            "cycle_id": str(cycle_id),
                            "error": "Cycle not found",
                        }
                    )
                    continue

                old_status = cycle.status.value
                success = service.advance_cycle_phase(cycle, target_status)

                if success:
                    results["transitions"].append(
                        {
                            "cycle_id": str(cycle.cycle_id),
                            "cycle_name": cycle.cycle_name,
                            "from_status": old_status,
                            "to_status": target_status.value,
                        }
                    )

                db.commit()

            except Exception as e:
                logger.error(
                    "Failed to transition cycle %s: %s",
                    cycle_id,
                    e,
                )
                db.rollback()
                results["errors"].append(
                    {
                        "cycle_id": str(cycle_id),
                        "error": str(e),
                    }
                )

    if not transitions:
        logger.debug("No performance cycles ready for transition")

    logger.info(
        "Cycle phase transitions complete: %d transitions, %d errors",
        len(results["transitions"]),
        len(results["errors"]),
    )

    return results


@shared_task
def generate_cycle_appraisals(cycle_id: str, template_id: str | None = None) -> dict:
    """
    Generate appraisals for all eligible employees in a cycle.

    This task is typically triggered when a cycle moves from DRAFT to ACTIVE.

    Args:
        cycle_id: UUID of the appraisal cycle
        template_id: Optional UUID of the appraisal template to use

    Returns:
        Dict with generation statistics
    """
    from app.services.performance_automation import PerformanceAutomationService

    logger.info("Generating appraisals for cycle %s", cycle_id)

    results: dict[str, Any] = {
        "cycle_id": cycle_id,
        "appraisals_created": 0,
        "eligible_count": 0,
        "skipped_no_manager": 0,
        "errors": [],
    }

    # Mode 3 — chicken-and-egg: we have a cycle_id but not its org. Look
    # up the cycle's organization under cross-org bypass, then process
    # the cycle under a session primed for that org.
    cycle_uuid = UUID(cycle_id)
    with cross_org_session() as cross_db:
        cycle = cross_db.get(AppraisalCycle, cycle_uuid)
        if not cycle:
            results["errors"].append({"error": f"Cycle {cycle_id} not found"})
            return results
        owning_org_id = cycle.organization_id

    try:
        with session_for_org(owning_org_id) as db:
            # Re-fetch in the primed session so the row binds to this
            # session's identity map (not the cross-org lookup's).
            cycle = db.get(AppraisalCycle, cycle_uuid)
            if not cycle:
                results["errors"].append({"error": f"Cycle {cycle_id} not found"})
                return results

            if cycle.status != AppraisalCycleStatus.ACTIVE:
                results["errors"].append(
                    {
                        "error": f"Cycle is not ACTIVE (status: {cycle.status.value})",
                    }
                )
                return results

            service = PerformanceAutomationService(db)

            # Get eligible employees for reporting
            eligible = service.get_eligible_employees(cycle)
            results["eligible_count"] = len(eligible)

            # Generate appraisals
            parsed_template_id = UUID(template_id) if template_id else None
            appraisals = service.generate_appraisals_for_cycle(
                cycle,
                template_id=parsed_template_id,
            )

            results["appraisals_created"] = len(appraisals)
            results["skipped_no_manager"] = results["eligible_count"] - len(appraisals)

            db.commit()

    except Exception as e:
        logger.exception("Appraisal generation failed for cycle %s: %s", cycle_id, e)
        results["errors"].append({"error": str(e)})

    logger.info(
        "Appraisal generation complete for cycle %s: %d created, %d skipped",
        cycle_id,
        results["appraisals_created"],
        results["skipped_no_manager"],
    )

    return results


@shared_task
def calculate_cycle_progress(cycle_id: str) -> dict:
    """
    Calculate and return progress statistics for a cycle.

    Args:
        cycle_id: UUID of the appraisal cycle

    Returns:
        Dict with progress statistics
    """
    from app.services.performance_automation import PerformanceAutomationService

    logger.info("Calculating progress for cycle %s", cycle_id)

    # Mode 3 — resolve cycle's org under cross-org bypass, then run
    # progress calc under a session primed for that org.
    cycle_uuid = UUID(cycle_id)
    with cross_org_session() as cross_db:
        cycle = cross_db.get(AppraisalCycle, cycle_uuid)
        if not cycle:
            return {"error": f"Cycle {cycle_id} not found"}
        owning_org_id = cycle.organization_id

    try:
        with session_for_org(owning_org_id) as db:
            cycle = db.get(AppraisalCycle, cycle_uuid)
            if not cycle:
                return {"error": f"Cycle {cycle_id} not found"}

            service = PerformanceAutomationService(db)
            progress = service.get_cycle_progress(cycle)

            logger.info(
                "Cycle %s progress: %d total, %d%% completed",
                cycle_id,
                progress["total_appraisals"],
                progress["progress"].get("completed_pct", 0),
            )

            return progress

    except Exception as e:
        logger.exception("Progress calculation failed for cycle %s: %s", cycle_id, e)
        return {"error": str(e)}


@shared_task
def check_upcoming_deadlines(days_ahead: int = 7) -> dict:
    """
    Check for upcoming deadlines across all active cycles.

    This task can be scheduled daily to identify cycles approaching deadlines.

    Args:
        days_ahead: Number of days to look ahead

    Returns:
        Dict with upcoming deadlines
    """
    from app.services.performance_automation import PerformanceAutomationService

    logger.info("Checking upcoming deadlines within %d days", days_ahead)

    deadlines: list[dict[str, Any]] = []
    for org_id in _list_organization_ids():
        with session_for_org(org_id) as db:
            try:
                service = PerformanceAutomationService(db)
                deadlines.extend(
                    service.get_upcoming_deadlines(
                        days_ahead=days_ahead,
                        org_id=org_id,
                    )
                )

            except Exception as e:
                logger.exception("Deadline check failed for org %s: %s", org_id, e)
                return {"error": str(e)}

    results: dict[str, Any] = {
        "deadlines_found": len(deadlines),
        "deadlines": sorted(deadlines, key=lambda x: x["days_remaining"]),
    }

    logger.info("Found %d upcoming deadlines", len(deadlines))

    return results


@shared_task
def sync_all_cycle_progress() -> dict:
    """
    Calculate and log progress for all active cycles.

    This is a reporting task that can be run periodically to track
    overall performance review progress across the organization.

    Returns:
        Dict with progress for all active cycles
    """
    from app.services.performance_automation import PerformanceAutomationService

    logger.info("Syncing progress for all active cycles")

    results: dict[str, Any] = {
        "cycles_processed": 0,
        "cycle_progress": [],
        "errors": [],
    }

    with cross_org_session() as cross_db:
        cycle_meta = list(
            cross_db.execute(
                select(AppraisalCycle.cycle_id, AppraisalCycle.organization_id).where(
                    AppraisalCycle.status.in_(
                        [
                            AppraisalCycleStatus.ACTIVE,
                            AppraisalCycleStatus.REVIEW,
                            AppraisalCycleStatus.CALIBRATION,
                        ]
                    ),
                )
            ).all()
        )

    cycles_by_org: dict[UUID, list[UUID]] = {}
    for cycle_id, org_id in cycle_meta:
        cycles_by_org.setdefault(org_id, []).append(cycle_id)

    for org_id, cycle_ids in cycles_by_org.items():
        with session_for_org(org_id) as db:
            try:
                cycles = db.scalars(
                    select(AppraisalCycle).where(AppraisalCycle.cycle_id.in_(cycle_ids))
                ).all()
                service = PerformanceAutomationService(db)

                for cycle in cycles:
                    try:
                        progress = service.get_cycle_progress(cycle)
                        results["cycle_progress"].append(
                            {
                                "cycle_id": str(cycle.cycle_id),
                                "cycle_name": cycle.cycle_name,
                                "status": cycle.status.value,
                                "total_appraisals": progress["total_appraisals"],
                                "completed_pct": progress["progress"].get(
                                    "completed_pct", 0
                                ),
                            }
                        )
                        results["cycles_processed"] += 1

                    except Exception as e:
                        logger.error(
                            "Progress calc failed for cycle %s: %s", cycle.cycle_id, e
                        )
                        results["errors"].append(
                            {
                                "cycle_id": str(cycle.cycle_id),
                                "error": str(e),
                            }
                        )

            except Exception as e:
                logger.exception("Cycle progress sync failed for org %s: %s", org_id, e)
                results["errors"].append(
                    {"organization_id": str(org_id), "error": str(e)}
                )

    logger.info(
        "Cycle progress sync complete: %d cycles processed",
        results["cycles_processed"],
    )

    return results


@shared_task
def process_pms_dispute_sla_enforcement() -> dict:
    """
    Enforce PMS dispute SLA rules across appeals, grievances, and PIPs.

    Applies automatic escalation actions when unresolved cases exceed
    their SLA windows (including end-Feb post-appraisal deadlines).
    """
    from app.services.people.perf.dispute_sla_service import PMSDisputeSLAService

    logger.info("Processing PMS dispute SLA enforcement")
    results: dict[str, Any] = {
        "appeals": {},
        "grievances": {},
        "pips": {},
        "errors": [],
    }

    for org_id in _list_organization_ids():
        with session_for_org(org_id) as db:
            try:
                org_results = PMSDisputeSLAService(db).enforce_all_overdue()
                db.commit()
                results["appeals"][str(org_id)] = org_results["appeals"]
                results["grievances"][str(org_id)] = org_results["grievances"]
                results["pips"][str(org_id)] = org_results["pips"]
            except Exception as e:
                logger.exception(
                    "PMS dispute SLA enforcement failed for org %s: %s", org_id, e
                )
                db.rollback()
                results["errors"].append(
                    {"organization_id": str(org_id), "error": str(e)}
                )
    return results


@shared_task
def process_pms_dispute_deadline_reminders(days_ahead: int = 7) -> dict:
    """
    Build a watchlist of upcoming PMS dispute deadlines for reminder delivery.
    """
    from app.services.people.perf.dispute_sla_service import PMSDisputeSLAService

    logger.info("Processing PMS dispute deadline reminders (%d days)", days_ahead)
    results: dict[str, Any] = {"days_ahead": days_ahead, "grievances": [], "pips": []}

    for org_id in _list_organization_ids():
        with session_for_org(org_id) as db:
            try:
                org_results = PMSDisputeSLAService(
                    db
                ).collect_upcoming_deadline_reminders(days_ahead=days_ahead)
                results["grievances"].extend(org_results.get("grievances", []))
                results["pips"].extend(org_results.get("pips", []))
            except Exception as e:
                logger.exception(
                    "PMS dispute reminder job failed for org %s: %s", org_id, e
                )
                results.setdefault("errors", []).append(
                    {"organization_id": str(org_id), "error": str(e)}
                )

    return results


@shared_task
def activate_cycle(cycle_id: str, template_id: str | None = None) -> dict:
    """
    Activate a cycle and generate appraisals for eligible employees.

    This is a combined task that:
    1. Changes cycle status from DRAFT to ACTIVE
    2. Generates appraisals for all eligible employees

    Args:
        cycle_id: UUID of the appraisal cycle to activate
        template_id: Optional UUID of the appraisal template

    Returns:
        Dict with activation results
    """
    from app.services.performance_automation import PerformanceAutomationService

    logger.info("Activating cycle %s", cycle_id)

    results: dict[str, Any] = {
        "cycle_id": cycle_id,
        "activated": False,
        "appraisals_created": 0,
        "errors": [],
    }

    # Mode 3 — resolve cycle's org under cross-org bypass, then activate
    # the cycle under a session primed for that org.
    cycle_uuid = UUID(cycle_id)
    with cross_org_session() as cross_db:
        cycle = cross_db.get(AppraisalCycle, cycle_uuid)
        if not cycle:
            results["errors"].append({"error": f"Cycle {cycle_id} not found"})
            return results
        owning_org_id = cycle.organization_id

    try:
        with session_for_org(owning_org_id) as db:
            cycle = db.get(AppraisalCycle, cycle_uuid)
            if not cycle:
                results["errors"].append({"error": f"Cycle {cycle_id} not found"})
                return results

            if cycle.status != AppraisalCycleStatus.DRAFT:
                results["errors"].append(
                    {
                        "error": f"Cycle cannot be activated (status: {cycle.status.value})",
                    }
                )
                return results

            # Activate the cycle
            cycle.status = AppraisalCycleStatus.ACTIVE
            db.flush()
            results["activated"] = True

            # Generate appraisals
            service = PerformanceAutomationService(db)
            parsed_template_id = UUID(template_id) if template_id else None
            appraisals = service.generate_appraisals_for_cycle(
                cycle,
                template_id=parsed_template_id,
            )

            results["appraisals_created"] = len(appraisals)

            db.commit()

            logger.info(
                "Cycle %s activated with %d appraisals",
                cycle_id,
                len(appraisals),
            )

    except Exception as e:
        logger.exception("Cycle activation failed for %s: %s", cycle_id, e)
        results["activated"] = False
        results["errors"].append({"error": str(e)})

    return results


@shared_task
def complete_cycle(cycle_id: str) -> dict:
    """
    Mark a cycle as completed if all appraisals are done.

    This task checks if all appraisals are either COMPLETED or CANCELLED,
    and if so, marks the cycle as COMPLETED.

    Args:
        cycle_id: UUID of the appraisal cycle

    Returns:
        Dict with completion results
    """
    from app.services.performance_automation import PerformanceAutomationService

    logger.info("Attempting to complete cycle %s", cycle_id)

    results: dict[str, Any] = {
        "cycle_id": cycle_id,
        "completed": False,
        "reason": None,
    }

    # Mode 3 — resolve cycle's org under cross-org bypass, then complete
    # the cycle under a session primed for that org.
    cycle_uuid = UUID(cycle_id)
    with cross_org_session() as cross_db:
        cycle = cross_db.get(AppraisalCycle, cycle_uuid)
        if not cycle:
            results["reason"] = "Cycle not found"
            return results
        owning_org_id = cycle.organization_id

    try:
        with session_for_org(owning_org_id) as db:
            cycle = db.get(AppraisalCycle, cycle_uuid)
            if not cycle:
                results["reason"] = "Cycle not found"
                return results

            if cycle.status == AppraisalCycleStatus.COMPLETED:
                results["completed"] = True
                results["reason"] = "Already completed"
                return results

            service = PerformanceAutomationService(db)

            if service.check_cycle_completion_eligibility(cycle):
                cycle.status = AppraisalCycleStatus.COMPLETED
                db.commit()
                results["completed"] = True
                results["reason"] = "All appraisals completed"
                logger.info("Cycle %s marked as COMPLETED", cycle_id)
            else:
                results["reason"] = "Not all appraisals are completed"
                logger.info("Cycle %s cannot be completed yet", cycle_id)

    except Exception as e:
        logger.exception("Cycle completion failed for %s: %s", cycle_id, e)
        results["reason"] = str(e)

    return results
