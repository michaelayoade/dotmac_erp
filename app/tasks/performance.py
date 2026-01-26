"""
Performance Review Background Tasks - Celery tasks for performance cycle automation.

Handles:
- Automatic cycle phase transitions based on deadlines
- Appraisal generation for eligible employees
- Progress tracking and reporting
- Deadline notification scheduling
"""

import logging
from datetime import date
from typing import Any, Optional
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from app.db import SessionLocal
from app.models.people.perf.appraisal_cycle import AppraisalCycle, AppraisalCycleStatus

logger = logging.getLogger(__name__)


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

    with SessionLocal() as db:
        service = PerformanceAutomationService(db)

        try:
            transitions = service.get_cycles_ready_for_transition()

            for cycle, target_status in transitions:
                try:
                    old_status = cycle.status.value
                    success = service.advance_cycle_phase(cycle, target_status)

                    if success:
                        results["transitions"].append({
                            "cycle_id": str(cycle.cycle_id),
                            "cycle_name": cycle.cycle_name,
                            "from_status": old_status,
                            "to_status": target_status.value,
                        })

                except Exception as e:
                    logger.error(
                        "Failed to transition cycle %s: %s",
                        cycle.cycle_id,
                        e,
                    )
                    results["errors"].append({
                        "cycle_id": str(cycle.cycle_id),
                        "error": str(e),
                    })

            db.commit()

        except Exception as e:
            logger.exception("Cycle phase transition processing failed: %s", e)
            db.rollback()
            results["errors"].append({"error": str(e)})

    logger.info(
        "Cycle phase transitions complete: %d transitions, %d errors",
        len(results["transitions"]),
        len(results["errors"]),
    )

    return results


@shared_task
def generate_cycle_appraisals(cycle_id: str, template_id: Optional[str] = None) -> dict:
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

    with SessionLocal() as db:
        try:
            cycle = db.scalar(
                select(AppraisalCycle).where(
                    AppraisalCycle.cycle_id == UUID(cycle_id),
                )
            )

            if not cycle:
                results["errors"].append({"error": f"Cycle {cycle_id} not found"})
                return results

            if cycle.status != AppraisalCycleStatus.ACTIVE:
                results["errors"].append({
                    "error": f"Cycle is not ACTIVE (status: {cycle.status.value})",
                })
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
            db.rollback()
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

    with SessionLocal() as db:
        try:
            cycle = db.scalar(
                select(AppraisalCycle).where(
                    AppraisalCycle.cycle_id == UUID(cycle_id),
                )
            )

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

    with SessionLocal() as db:
        try:
            service = PerformanceAutomationService(db)
            deadlines = service.get_upcoming_deadlines(days_ahead=days_ahead)

            results: dict[str, Any] = {
                "deadlines_found": len(deadlines),
                "deadlines": deadlines,
            }

            logger.info("Found %d upcoming deadlines", len(deadlines))

            return results

        except Exception as e:
            logger.exception("Deadline check failed: %s", e)
            return {"error": str(e)}


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

    with SessionLocal() as db:
        try:
            # Get all non-completed cycles
            cycles = db.scalars(
                select(AppraisalCycle).where(
                    AppraisalCycle.status.in_([
                        AppraisalCycleStatus.ACTIVE,
                        AppraisalCycleStatus.REVIEW,
                        AppraisalCycleStatus.CALIBRATION,
                    ]),
                )
            ).all()

            service = PerformanceAutomationService(db)

            for cycle in cycles:
                try:
                    progress = service.get_cycle_progress(cycle)
                    results["cycle_progress"].append({
                        "cycle_id": str(cycle.cycle_id),
                        "cycle_name": cycle.cycle_name,
                        "status": cycle.status.value,
                        "total_appraisals": progress["total_appraisals"],
                        "completed_pct": progress["progress"].get("completed_pct", 0),
                    })
                    results["cycles_processed"] += 1

                except Exception as e:
                    logger.error("Progress calc failed for cycle %s: %s", cycle.cycle_id, e)
                    results["errors"].append({
                        "cycle_id": str(cycle.cycle_id),
                        "error": str(e),
                    })

        except Exception as e:
            logger.exception("Cycle progress sync failed: %s", e)
            results["errors"].append({"error": str(e)})

    logger.info(
        "Cycle progress sync complete: %d cycles processed",
        results["cycles_processed"],
    )

    return results


@shared_task
def activate_cycle(cycle_id: str, template_id: Optional[str] = None) -> dict:
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

    with SessionLocal() as db:
        try:
            cycle = db.scalar(
                select(AppraisalCycle).where(
                    AppraisalCycle.cycle_id == UUID(cycle_id),
                )
            )

            if not cycle:
                results["errors"].append({"error": f"Cycle {cycle_id} not found"})
                return results

            if cycle.status != AppraisalCycleStatus.DRAFT:
                results["errors"].append({
                    "error": f"Cycle cannot be activated (status: {cycle.status.value})",
                })
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
            db.rollback()
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

    with SessionLocal() as db:
        try:
            cycle = db.scalar(
                select(AppraisalCycle).where(
                    AppraisalCycle.cycle_id == UUID(cycle_id),
                )
            )

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
            db.rollback()
            results["reason"] = str(e)

    return results
