"""AR allocation-backlog task — the scheduled adapter over the FIFO service.

`FIFOAllocationService.allocate_for_org` already owned the allocation
decision; what lived in the script was the choice of organization, and it
chose badly (`LIMIT 1` over a data scan). This iterates every organization
that actually has Splynx payments, one scoped session each, recording a
`BatchOperation` per organization so a partial run is visible rather than
inferred.

Defaults to `dry_run=True`. Allocation moves money against invoices; a
scheduled job should not start doing that unattended because it was
deployed.
"""

from __future__ import annotations

import logging
import uuid

from celery import shared_task

from app.db.session_context import cross_org_session, session_for_org
from app.models.batch_operation import BatchOperationType
from app.services.batch_operation import batch_operation
from app.services.finance.ar.allocation_backlog import (
    organizations_with_splynx_payments,
)
from app.services.finance.ar.exact_match_allocation import allocate_exact_matches

logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@shared_task
def allocate_splynx_payments_fifo(
    organization_id: str | None = None, dry_run: bool = True
) -> dict:
    """FIFO-allocate unallocated Splynx payments against open invoices."""
    from app.services.finance.ar.fifo_allocation_service import FIFOAllocationService

    if organization_id is not None:
        org_ids = [uuid.UUID(str(organization_id))]
    else:
        # Asking a question ABOUT tenants, so it cannot be asked from inside
        # one. The old script asked it with LIMIT 1 and took whatever came
        # back first.
        with cross_org_session() as db:
            org_ids = organizations_with_splynx_payments(db)
        logger.info("%d organization(s) have Splynx payments", len(org_ids))

    summary: dict[str, dict] = {}
    for org_id in org_ids:
        with (
            session_for_org(org_id) as db,
            batch_operation(
                db,
                organization_id=org_id,
                operation_type=BatchOperationType.BULK_UPDATE,
                operation_name="allocate_splynx_fifo",
                started_by_id=SYSTEM_ACTOR_ID,
                description=(
                    "FIFO-allocate unallocated Splynx payments"
                    + (" (dry run)" if dry_run else "")
                ),
            ) as tally,
        ):
            result = FIFOAllocationService(db).allocate_for_org(org_id, dry_run=dry_run)
            tally.created = result.allocations_created
            tally.failed = len(result.errors)

        summary[str(org_id)] = {
            "customers_processed": result.customers_processed,
            "allocations_created": result.allocations_created,
            "total_allocated": str(result.total_allocated),
            "prepayment_customers": len(result.prepayment_customers),
            "errors": result.errors,
        }
    return summary


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
        with cross_org_session() as db:
            org_ids = organizations_with_splynx_payments(db)

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
