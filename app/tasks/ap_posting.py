"""AP posting-backlog task — the scheduled adapter over the owning service.

The reference for what a recurring script becomes. `post_unposted_ap_invoices`
was a hand-run script that opened its own session, hardcoded one organization,
primed the RLS GUC with an f-string, and left no record of having run.

As a task it gets what a script never had: a schedule, retry, a scoped session
priming both isolation layers, and a `BatchOperation` row the admin UI can
show. The decision itself lives in
`app.services.finance.ap.posting_backlog` — this module only chooses the
organizations, opens the scope, and records the run.
"""

from __future__ import annotations

import logging
import uuid

from celery import shared_task
from sqlalchemy import select

from app.db.session_context import cross_org_session, session_for_org
from app.models.batch_operation import BatchOperationType
from app.models.finance.core_org.organization import Organization
from app.services.batch_operation import batch_operation
from app.services.finance.ap.posting_backlog import post_unposted_invoices

logger = logging.getLogger(__name__)

# Used as `started_by_id` when nothing human triggered the run. A scheduled
# task has no actor, and recording a real user's id would be a lie.
SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@shared_task
def post_unposted_ap_invoices(
    organization_id: str | None = None, dry_run: bool = True
) -> dict:
    """Post supplier invoices missing their GL journal entry.

    Defaults to `dry_run=True`: a scheduled job that silently posts to the
    general ledger the first time it runs is not a safe default. Enable
    posting explicitly, per organization.
    """
    if organization_id is not None:
        org_ids = [uuid.UUID(str(organization_id))]
    else:
        with cross_org_session() as db:
            org_ids = list(db.scalars(select(Organization.organization_id)).all())

    summary: dict[str, dict] = {}
    for org_id in org_ids:
        with session_for_org(org_id) as db:
            with batch_operation(
                db,
                organization_id=org_id,
                operation_type=BatchOperationType.MIGRATION,
                operation_name="post_unposted_ap_invoices",
                started_by_id=SYSTEM_ACTOR_ID,
                description=(
                    "Post AP supplier invoices whose journal entry is missing"
                    + (" (dry run)" if dry_run else "")
                ),
            ) as tally:
                result = post_unposted_invoices(
                    db,
                    organization_id=org_id,
                    fallback_user_id=SYSTEM_ACTOR_ID,
                    dry_run=dry_run,
                )
                tally.created = result.posted
                tally.skipped = result.skipped + (result.found if dry_run else 0)
                tally.failed = result.errors

            summary[str(org_id)] = {
                "found": result.found,
                "posted": result.posted,
                "skipped": result.skipped,
                "errors": result.errors,
                "total_amount": str(result.total_amount),
            }

    return summary
