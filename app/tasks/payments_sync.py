"""Payment-provider sync tasks — scheduled adapters over the owning services.

Defaults to `dry_run=True`. This pushes customer records to an external
payment provider; a scheduled job should not begin doing that unattended
merely because it was deployed.
"""

from __future__ import annotations

import logging
import uuid

from celery import shared_task

from app.db.session_context import session_for_org
from app.models.batch_operation import BatchOperationType
from app.services.batch_operation import batch_operation
from app.services.finance.payments.paystack_customer_sync import sync_customers
from app.tenant_catalog import organization_ids

logger = logging.getLogger(__name__)

SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@shared_task
def sync_customers_to_paystack(
    organization_id: str | None = None, dry_run: bool = True
) -> dict:
    """Create or update every active customer in Paystack, per organization."""
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
                operation_type=BatchOperationType.SYNC,
                operation_name="sync_customers_to_paystack",
                started_by_id=SYSTEM_ACTOR_ID,
                description=(
                    "Sync active customers to Paystack"
                    + (" (dry run)" if dry_run else "")
                ),
            ) as tally,
        ):
            result = sync_customers(db, organization_id=org_id, dry_run=dry_run)
            tally.created = result.created
            tally.updated = result.updated
            tally.skipped = result.skipped_no_email
            tally.failed = len(result.errors)

        summary[str(org_id)] = {
            "total": result.total,
            "created": result.created,
            "updated": result.updated,
            "skipped_no_email": result.skipped_no_email,
            "errors": result.errors,
        }
    return summary
