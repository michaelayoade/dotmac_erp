"""GL posting-backlog tasks — scheduled adapters over the owning services.

Extends the pattern established by `app.tasks.ap_posting`: the decision lives
in a service, this module only chooses organizations, opens a scoped session,
and records a `BatchOperation` the admin UI can show.

Both tasks default to `dry_run=True`. A scheduled job that silently posts to
the general ledger the first time it runs is not a safe default, and these
were hand-run scripts until now — nobody has yet decided they should post
unattended.
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
from app.services.expense.posting_backlog import post_unposted_claims
from app.services.finance.gl.posting_backlog import post_approved_journals

logger = logging.getLogger(__name__)

# Recorded as `started_by_id` when nothing human triggered the run. A schedule
# has no actor, and naming a real user would be a lie.
SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _organization_ids(organization_id: str | None) -> list[uuid.UUID]:
    if organization_id is not None:
        return [uuid.UUID(str(organization_id))]
    with cross_org_session() as db:
        return list(db.scalars(select(Organization.organization_id)).all())


@shared_task
def post_approved_journal_backlog(
    organization_id: str | None = None, dry_run: bool = True
) -> dict:
    """Post APPROVED journals that are balanced and in an open period."""
    summary: dict[str, dict] = {}
    for org_id in _organization_ids(organization_id):
        with (
            session_for_org(org_id) as db,
            batch_operation(
                db,
                organization_id=org_id,
                operation_type=BatchOperationType.MIGRATION,
                operation_name="post_approved_journals",
                started_by_id=SYSTEM_ACTOR_ID,
                description=(
                    "Post APPROVED journal entries that never reached POSTED"
                    + (" (dry run)" if dry_run else "")
                ),
            ) as tally,
        ):
            result = post_approved_journals(
                db,
                organization_id=org_id,
                posted_by_user_id=SYSTEM_ACTOR_ID,
                dry_run=dry_run,
            )
            tally.created = result.posted
            # Unbalanced entries and closed periods are deliberate skips, not
            # failures — they need a human, not a retry.
            tally.skipped = result.unbalanced + result.closed_period
            tally.failed = len(result.errors)

        summary[str(org_id)] = {
            "found": result.found,
            "postable": result.postable,
            "posted": result.posted,
            "unbalanced": result.unbalanced,
            "closed_period": result.closed_period,
            "errors": result.errors,
        }
    return summary


@shared_task
def post_expense_claim_backlog(
    organization_id: str | None = None, dry_run: bool = True
) -> dict:
    """Post expense claims that never reached the general ledger."""
    summary: dict[str, dict] = {}
    for org_id in _organization_ids(organization_id):
        with (
            session_for_org(org_id) as db,
            batch_operation(
                db,
                organization_id=org_id,
                operation_type=BatchOperationType.MIGRATION,
                operation_name="post_unposted_expense_claims",
                started_by_id=SYSTEM_ACTOR_ID,
                description=(
                    "Post expense claims whose journal entry is missing"
                    + (" (dry run)" if dry_run else "")
                ),
            ) as tally,
        ):
            result = post_unposted_claims(
                db,
                organization_id=org_id,
                fallback_user_id=SYSTEM_ACTOR_ID,
                dry_run=dry_run,
            )
            tally.created = result.posted
            tally.skipped = result.skipped + (result.found if dry_run else 0)
            tally.failed = len(result.errors)

        summary[str(org_id)] = {
            "found": result.found,
            "posted": result.posted,
            "skipped": result.skipped,
            "errors": result.errors,
            "total_amount": str(result.total_amount),
        }
    return summary
