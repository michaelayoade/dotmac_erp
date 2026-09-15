"""
Celery tasks for ERP -> dotmac_sub staff sync.

``sync_employee_staff_account`` is the event-driven push enqueued by the
employee lifecycle service (activate/terminate/resign/suspend);
``run_staff_sync_reconcile`` is the nightly drift sweep.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery import shared_task

from app.db.session_context import session_for_org
from app.models.people.hr.employee import Employee
from app.services.dotmac_sub import DotmacSubPermanentSyncError
from app.services.dotmac_sub import staff_sync
from app.services.people.hr.staff_access_projection import StaffAccessProjectionService
from app.tasks.dotmac_sub import _resolve_org_id

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def sync_employee_staff_account(
    self,
    employee_id: str,
    organization_id: str,
    *,
    allow_active_access_revocation: bool = False,
) -> dict[str, Any]:
    """Push one employee's staff-account state to dotmac_sub (with retry)."""
    org_id = _resolve_org_id(organization_id)
    if org_id is None:
        return {"success": False, "error": "No valid organization ID"}

    from app.services.people.hr.provisioning_monitor import record_stage

    try:
        with session_for_org(org_id) as db:
            employee = db.get(Employee, UUID(employee_id))
            if not employee:
                return {"success": False, "error": "Employee not found"}
            record_stage(employee, "selfcare", "running")
            record_stage(employee, "talk", "running")
            db.commit()
            result = staff_sync.sync_employee(
                db,
                employee,
                allow_active_access_revocation=allow_active_access_revocation,
            )
            record_stage(
                employee,
                "selfcare",
                "completed"
                if getattr(employee, "dotmac_sub_account_id", None)
                else "skipped",
            )
            record_stage(
                employee,
                "talk",
                "completed"
                if getattr(employee, "dotmac_sub_staff_synced_at", None)
                else "skipped",
            )
            db.commit()
            logger.info(
                "Staff sync for employee %s: %s", employee_id, result.get("action")
            )
            return {"success": True, **result}
    except DotmacSubPermanentSyncError as e:
        with session_for_org(org_id) as db:
            employee = db.get(Employee, UUID(employee_id))
            if employee:
                if getattr(employee, "dotmac_sub_account_id", None):
                    record_stage(employee, "selfcare", "completed")
                    record_stage(employee, "talk", "failed", error=e)
                else:
                    record_stage(employee, "selfcare", "failed", error=e)
                    record_stage(employee, "talk", "blocked")
                db.commit()
        logger.error(
            "Staff sync failed permanently for employee %s: %s", employee_id, e
        )
        return {"success": False, "retryable": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 — retry transport/API failures
        with session_for_org(org_id) as db:
            employee = db.get(Employee, UUID(employee_id))
            if employee:
                if getattr(employee, "dotmac_sub_account_id", None):
                    record_stage(employee, "selfcare", "completed")
                    record_stage(employee, "talk", "failed", error=e)
                else:
                    record_stage(employee, "selfcare", "failed", error=e)
                    record_stage(employee, "talk", "blocked")
                db.commit()
        logger.warning("Staff sync retry for employee %s: %s", employee_id, e)
        raise self.retry(exc=e)


@shared_task
def run_staff_sync_reconcile(organization_id: str | None = None) -> dict[str, Any]:
    """Nightly sweep: reconcile every syncable employee's dotmac_sub account."""
    org_id = _resolve_org_id(organization_id)
    if org_id is None:
        return {"success": False, "error": "No valid organization ID configured"}

    with session_for_org(org_id) as db:
        projection_outcome = StaffAccessProjectionService(
            db
        ).reconcile_organization_projections(org_id)
        db.commit()
        staff_outcome = staff_sync.reconcile_staff_accounts(db, org_id)
        return {
            **staff_outcome,
            "staff_access_projection": {
                "employees_seen": projection_outcome.employees_seen,
                "mapped_employees_seen": projection_outcome.mapped_employees_seen,
                "account_statuses_seen": projection_outcome.account_statuses_seen,
                "leave_restrictions_seen": projection_outcome.leave_restrictions_seen,
            },
        }
