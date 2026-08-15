"""AR allocation task — the scheduled adapter over exact-match allocation.

Tier-A only. The Tier-B FIFO allocator was Splynx-specific and was deleted
with that integration: it existed because Splynx owned `invoice.amount_paid`
and `invoice.status`, so it created allocation records without touching them.
Once Splynx is not the owner that premise inverts, which made it wrong to
keep rather than merely unused.

Defaults to `dry_run=True`. Allocation moves money against invoices; a
scheduled job should not begin doing that unattended because it was deployed.
"""

from __future__ import annotations

import logging
import uuid

from celery import shared_task

from app.db.session_context import session_for_org
from app.models.batch_operation import BatchOperationType
from app.services.batch_operation import batch_operation
from app.services.finance.ar.exact_match_allocation import allocate_exact_matches
from app.tenant_catalog import organization_ids

logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@shared_task
def allocate_exact_match_payments(
    organization_id: str | None = None,
    year: int | None = None,
    dry_run: bool = True,
) -> dict:
    """Tier-A: allocate payments that match exactly one open invoice.

    Runs before the FIFO tier by convention — an unambiguous 1:1 match is a
    better answer than any ordering heuristic, so taking those out first
    leaves FIFO a smaller and less speculative problem.
    """
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
                operation_name="allocate_exact_match_payments",
                started_by_id=SYSTEM_ACTOR_ID,
                description=(
                    "Allocate payments with exactly one matching invoice"
                    + (" (dry run)" if dry_run else "")
                ),
            ) as tally,
        ):
            result = allocate_exact_matches(
                db, organization_id=org_id, year=year, dry_run=dry_run
            )
            tally.created = result.allocated
            tally.skipped = result.skipped
            tally.failed = len(result.errors)

        summary[str(org_id)] = {
            "candidates": result.candidates,
            "allocated": result.allocated,
            "skipped": result.skipped,
            "total_allocated": str(result.total_allocated),
            "errors": result.errors,
        }
    return summary
