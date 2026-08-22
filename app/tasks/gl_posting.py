"""GL posting-backlog tasks — scheduled adapters over the owning services.

Extends the pattern established by `app.tasks.ap_posting`: the decision lives
in a service, this module only chooses organizations, opens a scoped session,
and records a `BatchOperation` the admin UI can show.

Both tasks default to `dry_run=True`. A scheduled job that silently posts to
the general ledger the first time it runs is not a safe default, and these
were hand-run scripts until now — nobody has yet decided they should post
unattended.

## `dry_run=False` is gated, and the reason is not hypothetical

A default is not a safeguard: `dry_run=False` is one keyword argument away, and
these tasks post to the ledger without deciding whether the effect is already
there. Two measured facts make that dangerous today:

* **`post_approved_journals` does not filter by source.** Its query selects
  every APPROVED journal for the organization, so its blast radius is the whole
  backlog — 14,263 journals, ₦76,495,739.50 — of which the detector in ERP PR
  #335 finds **zero** that should be posted.
* **`post_stranded_source_journals` has already caused duplicate postings.** It
  keys idempotency on `backfill-stranded-bank-fees-<journal_number>`, a
  per-journal namespace that bypasses the ledger's per-statement-line boundary
  entirely. 429 duplicate bank-fee postings (₦7,764.68) in production came from
  exactly that path.

So a live run now requires `ALLOW_BULK_JOURNAL_BACKLOG_POSTING=true` in the
environment as well. The point is not that an environment variable is hard to
set — it is that setting it is a deliberate act by someone who went looking for
this comment, rather than a default someone inherited.

Remove the gate when every journal in the backlog has an approved disposition.
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

#: Set to `true` to allow a live (non-dry-run) bulk backlog post. See the module
#: docstring for why this exists rather than trusting the `dry_run` default.
BULK_POSTING_ENV_FLAG = "ALLOW_BULK_JOURNAL_BACKLOG_POSTING"


def _require_bulk_posting_allowed(task_name: str, dry_run: bool) -> None:
    """Refuse a live bulk post unless it was explicitly enabled.

    Raises rather than silently downgrading to a dry run: an operator who asked
    for a live run and got a quiet no-op would believe the work was done.
    """
    if dry_run:
        return
    import os

    if os.getenv(BULK_POSTING_ENV_FLAG, "").strip().lower() not in {"1", "true", "yes"}:
        raise RuntimeError(
            f"{task_name} refused: a live bulk backlog post requires "
            f"{BULK_POSTING_ENV_FLAG}=true. The backlog currently contains no "
            f"journal that should be posted (ERP PR #335), and one of these "
            f"paths has already produced duplicate postings. Run with "
            f"dry_run=True, or set the flag deliberately."
        )


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
    """Post APPROVED journals that are balanced and in an open period.

    Balanced and in an open period is the ENTIRE test this applies. It says
    nothing about whether the effect is already in the ledger — and for the
    current backlog, it always is. See `_require_bulk_posting_allowed`.
    """
    _require_bulk_posting_allowed("post_approved_journal_backlog", dry_run)

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

    _require_bulk_posting_allowed("post_stranded_source_journals", dry_run)

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
