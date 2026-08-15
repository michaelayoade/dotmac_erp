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

from app.db.session_context import session_for_org
from app.models.batch_operation import BatchOperationType
from app.services.batch_operation import batch_operation
from app.services.expense.posting_backlog import post_unposted_claims
from app.services.finance.gl.posting_backlog import post_approved_journals
from app.services.finance.gl.stranded_fee_posting import (
    StrandedPostingResult,
    find_stranded_journals,
    post_one,
)
from app.tenant_catalog import organization_ids

logger = logging.getLogger(__name__)

# Recorded as `started_by_id` when nothing human triggered the run. A schedule
# has no actor, and naming a real user would be a lie.
SYSTEM_ACTOR_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


def _organization_ids(organization_id: str | None) -> list[uuid.UUID]:
    if organization_id is not None:
        return [uuid.UUID(str(organization_id))]
    return organization_ids(include_inactive=True)


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


@shared_task
def post_stranded_source_journals(
    organization_id: str | None = None,
    year_code: str = "",
    source_module: str = "BANKING",
    source_document_type: str = "BANK_FEE",
    limit: int | None = None,
    dry_run: bool = True,
) -> dict:
    """Post APPROVED journals a source module left stranded before the ledger.

    `year_code` is required in practice — it was a module constant
    (`TARGET_YEAR_CODE = "FY2025"`) in the script this replaces, which made a
    one-year repair look like a general tool. An empty value is rejected
    rather than defaulted, because guessing a fiscal year is worse than
    asking for one.
    """
    if not year_code:
        raise ValueError("year_code is required (e.g. 'FY2025')")

    summary: dict[str, dict] = {}
    for org_id in _organization_ids(organization_id):
        result = StrandedPostingResult()
        with (
            session_for_org(org_id) as db,
            batch_operation(
                db,
                organization_id=org_id,
                operation_type=BatchOperationType.MIGRATION,
                operation_name="post_stranded_source_journals",
                started_by_id=SYSTEM_ACTOR_ID,
                description=(
                    f"{source_module}/{source_document_type} {year_code}"
                    + (" (dry run)" if dry_run else "")
                ),
            ) as tally,
        ):
            journals = find_stranded_journals(
                db,
                organization_id=org_id,
                year_code=year_code,
                source_module=source_module,
                source_document_type=source_document_type,
                limit=limit,
            )
            result.found = len(journals)
            if not dry_run:
                for journal in journals:
                    ok, replay, msg = post_one(db, journal, source_module=source_module)
                    if ok and replay:
                        result.already_posted += 1
                    elif ok:
                        result.posted += 1
                    else:
                        result.failures.append((journal.journal_number, msg))
            tally.created = result.posted
            tally.skipped = result.already_posted + (result.found if dry_run else 0)
            tally.failed = len(result.failures)

        summary[str(org_id)] = {
            "found": result.found,
            "posted": result.posted,
            "already_posted": result.already_posted,
            "failures": result.failures,
        }
    return summary
