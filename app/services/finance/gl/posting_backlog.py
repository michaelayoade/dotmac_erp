"""Post APPROVED journal entries that never reached POSTED.

The owner of an operation that lived only in
`scripts/post_approved_journals.py`. Same three defects as the AP backlog
before it: a hardcoded `ORG_ID` at module level, a `SessionLocal()` that
primed neither tenant layer, and no record that it ran.

It also carried two business rules that belong here rather than in a script:

* **The imbalance tolerance.** A journal whose debits and credits differ by
  less than `IMBALANCE_TOLERANCE` is treated as balanced and postable;
  anything above is skipped as a data defect. This is the same sub-cent dust
  concept AR and AP settled on, and it was the third place in the codebase to
  declare it independently.
* **Which periods accept a posting.** `OPEN`, `REOPENED`, or no period at
  all. A journal in a closed period is skipped rather than force-posted —
  closing a period is a decision the period service owns, and a backlog job
  must not quietly reverse it.

Neither rule was discoverable before: they sat inside a 195-line script that
only ran when someone typed its name.

This function opens no session, sets no scope and never commits — the caller
owns the transaction, which is what lets the task, the CLI and any future
admin action share one implementation.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Debits and credits differing by less than this are rounding dust, not an
# unbalanced entry. Mirrors the AR/AP payment tolerance; declared here because
# GL imbalance and payment coverage are different questions that happen to
# share a magnitude.
IMBALANCE_TOLERANCE = Decimal("0.01")

# Period states that accept a posting. `None` covers journals with no fiscal
# period attached at all.
POSTABLE_PERIOD_STATUSES: tuple[str | None, ...] = ("OPEN", "REOPENED", None)


@dataclass(frozen=True)
class ApprovedJournal:
    journal_entry_id: uuid.UUID
    journal_number: str
    imbalance: Decimal
    period_status: str | None
    source_module: str | None

    @property
    def is_balanced(self) -> bool:
        return self.imbalance < IMBALANCE_TOLERANCE

    @property
    def period_accepts_posting(self) -> bool:
        return self.period_status in POSTABLE_PERIOD_STATUSES

    @property
    def is_postable(self) -> bool:
        return self.is_balanced and self.period_accepts_posting


@dataclass
class JournalPostingResult:
    found: int = 0
    posted: int = 0
    unbalanced: int = 0
    closed_period: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def postable(self) -> int:
        return self.found - self.unbalanced - self.closed_period


def find_approved_journals(
    db: Session, *, organization_id: uuid.UUID
) -> list[ApprovedJournal]:
    """Every APPROVED journal for this organization, with balance and period.

    Raw SQL because this is an aggregate over lines with a period join — the
    ORM would issue it far worse. Parameterised and org-filtered explicitly:
    the RLS GUC is a second line of defence, never the only one.
    """
    rows = db.execute(
        text("""
            SELECT je.journal_entry_id,
                   je.journal_number,
                   ABS(SUM(jel.debit_amount) - SUM(jel.credit_amount)) AS imbalance,
                   fp.status AS period_status,
                   je.source_module
            FROM gl.journal_entry je
            JOIN gl.journal_entry_line jel
              ON jel.journal_entry_id = je.journal_entry_id
            LEFT JOIN gl.fiscal_period fp
              ON fp.fiscal_period_id = je.fiscal_period_id
            WHERE je.organization_id = :org_id
              AND je.status = 'APPROVED'
            GROUP BY je.journal_entry_id, je.journal_number, fp.status,
                     je.source_module
            ORDER BY je.entry_date
        """),
        {"org_id": str(organization_id)},
    ).all()
    return [
        ApprovedJournal(
            journal_entry_id=uuid.UUID(str(row[0])),
            journal_number=row[1],
            imbalance=Decimal(str(row[2] or 0)),
            period_status=row[3],
            source_module=row[4],
        )
        for row in rows
    ]


def post_approved_journals(
    db: Session,
    *,
    organization_id: uuid.UUID,
    posted_by_user_id: uuid.UUID,
    dry_run: bool = True,
) -> JournalPostingResult:
    """Post every APPROVED journal that is balanced and in an open period.

    Skips rather than force-posts: an unbalanced journal is a data defect to
    investigate, and a closed period is a decision to respect.

    Does not commit — the caller owns the transaction.
    """
    from app.services.finance.gl.journal import JournalService

    journals = find_approved_journals(db, organization_id=organization_id)
    result = JournalPostingResult(found=len(journals))
    result.unbalanced = sum(1 for j in journals if not j.is_balanced)
    result.closed_period = sum(
        1 for j in journals if j.is_balanced and not j.period_accepts_posting
    )

    if dry_run:
        return result

    for journal in (j for j in journals if j.is_postable):
        try:
            JournalService.post_journal(
                db=db,
                organization_id=organization_id,
                journal_entry_id=journal.journal_entry_id,
                posted_by_user_id=posted_by_user_id,
            )
            result.posted += 1
        except Exception as exc:
            result.errors.append(f"{journal.journal_number}: {exc}")
            logger.warning("Failed to post journal %s", journal.journal_number)

    return result
