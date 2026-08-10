"""Post APPROVED journals that a source module left stranded before the ledger.

The owner of an operation that lived in
`scripts/post_stranded_bank_fees.py`. A journal is *stranded* when its source
module created and approved it but posting never completed, so it sits
APPROVED with no ledger batch behind it.

## What extracting it found

**A hardcoded fiscal year.** `TARGET_YEAR_CODE = "FY2025"` sat at module
level, which made a general repair look like a general tool while only ever
addressing one year. It is a parameter now, along with the source module and
document type — the mechanism is "post stranded journals from a source", and
bank fees were one instance of it.

**No organization filter.** The query joined journal -> period -> year and
filtered on year code, status, module and document type — never on tenant.
The script opened a raw `SessionLocal()` per journal, so nothing at any layer
bounded it.

**Idempotent replays were detected by substring.** The old loop did
``if "Already posted" in msg``, re-classifying a human-readable message to
decide whether anything had been written. `PostingResult.idempotent_replay`
now carries that as a flag set where the condition is actually known. The
message stays prose; the decision reads the flag.

## One session per journal, deliberately

Posting is per-journal atomic: one bad entry must not roll back the ones
before it. The caller therefore opens a session per journal rather than
wrapping the batch, which is why this module exposes `post_one` separately —
the loop belongs to the caller that owns the session lifecycle.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.fiscal_year import FiscalYear
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus

logger = logging.getLogger(__name__)

DEFAULT_IDEMPOTENCY_PREFIX = "backfill-stranded"


@dataclass
class StrandedPostingResult:
    found: int = 0
    posted: int = 0
    already_posted: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def find_stranded_journals(
    db: Session,
    *,
    organization_id: uuid.UUID,
    year_code: str,
    source_module: str,
    source_document_type: str,
    limit: int | None = None,
) -> list[JournalEntry]:
    """APPROVED journals from a source that never reached the ledger."""
    stmt = (
        select(JournalEntry)
        .join(
            FiscalPeriod,
            FiscalPeriod.fiscal_period_id == JournalEntry.fiscal_period_id,
        )
        .join(FiscalYear, FiscalYear.fiscal_year_id == FiscalPeriod.fiscal_year_id)
        .where(
            JournalEntry.organization_id == organization_id,
            FiscalYear.year_code == year_code,
            JournalEntry.status == JournalStatus.APPROVED,
            JournalEntry.source_module == source_module,
            JournalEntry.source_document_type == source_document_type,
        )
        .order_by(JournalEntry.posting_date, JournalEntry.journal_number)
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def post_one(
    db: Session,
    journal: JournalEntry,
    *,
    source_module: str,
    idempotency_prefix: str = DEFAULT_IDEMPOTENCY_PREFIX,
) -> tuple[bool, bool, str]:
    """Post one journal. Returns (succeeded, was_replay, message).

    `was_replay` comes from `PostingResult.idempotent_replay`, not from
    reading the message — the caller needs to distinguish "wrote something"
    from "confirmed it was already written", and prose is the wrong carrier
    for that.
    """
    from app.services.finance.gl.ledger_posting import (
        LedgerPostingService,
        PostingRequest,
    )

    key = f"{idempotency_prefix}-{journal.journal_number}"
    request = PostingRequest(
        organization_id=journal.organization_id,
        journal_entry_id=journal.journal_entry_id,
        posting_date=journal.posting_date,
        idempotency_key=key,
        source_module=source_module,
        # An empty `entries` tells the service to load lines from
        # journal_entry_line rather than trusting anything passed in here.
        posted_by_user_id=journal.approved_by_user_id or journal.created_by_user_id,
        correlation_id=key,
    )
    try:
        result = LedgerPostingService.post_journal_entry(db, request)
        return (
            bool(result.success),
            bool(result.idempotent_replay),
            (result.message or "ok"),
        )
    except Exception as exc:  # noqa: BLE001 — per-journal failure, reported below
        return False, False, f"{type(exc).__name__}: {exc}"
