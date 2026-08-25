"""AR reconciliation task — the scheduled adapter over the amount_paid repair.

Deliberately a separate module from the allocation tasks even though both are
AR: allocation CREATES the allocations, reconciliation READS them as the
authority for `amount_paid`. Keeping them apart makes the ordering explicit —
allocate first, reconcile second, so the reconciler sees a settled picture
rather than racing the thing that produces its input.

Defaults to `dry_run=True`. This writes to invoice rows; a scheduled job
should not begin doing that unattended merely because it was deployed.
"""

from __future__ import annotations

import logging
import uuid

from celery import shared_task

from app.db.session_context import session_for_org
from app.models.batch_operation import BatchOperationType
from app.services.batch_operation import batch_operation
from app.services.finance.ar.amount_paid_reconciler import reconcile_amount_paid
from app.tenant_catalog import organization_ids

logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@shared_task
def reconcile_invoice_amount_paid(
    organization_id: str | None = None,
    month: str | None = None,
    dry_run: bool = True,
) -> dict:
    """Repair `invoice.amount_paid` from the allocations that are its authority."""
    if organization_id is not None:
        org_ids = [uuid.UUID(str(organization_id))]
    else:
        org_ids = organization_ids(include_inactive=True)

    summary: dict[str, dict] = {}
    for org_id in org_ids:
        with (
            session_for_org(org_id) as db,
            batch_operation(
                db,
                organization_id=org_id,
                operation_type=BatchOperationType.BULK_UPDATE,
                operation_name="reconcile_invoice_amount_paid",
                started_by_id=SYSTEM_ACTOR_ID,
                description=(
                    "Reconcile invoice.amount_paid against payment allocations"
                    + (" (dry run)" if dry_run else "")
                ),
            ) as tally,
        ):
            result = reconcile_amount_paid(
                db, organization_id=org_id, month=month, dry_run=dry_run
            )
            tally.updated = result.updated
            tally.skipped = result.examined - result.updated

        summary[str(org_id)] = {
            "examined": result.examined,
            "updated": result.updated,
            "total_correction": str(result.total_correction),
            "status_changes": result.status_changes,
        }
    return summary
