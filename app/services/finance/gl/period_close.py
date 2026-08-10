"""Deciding whether a fiscal period may be closed, and closing it.

The owner of an operation that lived in `scripts/close_fiscal_periods.py`.
Closing a period is a one-way accounting act — everything downstream treats a
closed period as settled — so the rules that gate it are among the most
consequential in the ledger, and all of them were inside a 334-line script.

## The gate

A period is READY when both hold:

* **No unposted journals.** DRAFT, SUBMITTED and APPROVED all count as
  unposted; only POSTED is settled. Closing over an APPROVED journal strands
  it permanently — it can never be posted into a closed period — which is
  what makes APPROVED belong on this list rather than looking finished.
* **The period balances.** Debits and credits over POSTED journals differ by
  less than `IMBALANCE_TOLERANCE`.

## `force` bypasses the gate, and says so

The script had a `--force` flag that skipped both checks. It is preserved,
because there are real situations where an operator must close over a known
defect — but it is now an explicit argument on a named function, it is
recorded on the run, and `assess_periods` still reports what was overridden.
A bypass that leaves no trace of what it bypassed is the part worth fixing,
not the bypass itself.

## Hard close implies soft close

`hard_close_period` requires the period to be soft-closed first, so a hard
close of an OPEN period is two transitions, not one. That sequencing was
implicit in an `if` inside the script's loop.

## Tolerance

`IMBALANCE_TOLERANCE` is imported from `gl.posting_backlog` rather than
re-declared. Whether a set of journals balances is the same question in both
places, and this codebase has already produced four independent declarations
of the same sub-cent threshold (AR, AP, GL posting, and here) — which is the
divergence ADR-0015 is about.

**Sharing it changes behaviour at exactly one kobo, deliberately.** The script
blocked on ``imbalance > Decimal("0.01")``, so an imbalance of exactly 0.01
passed the gate. `gl.posting_backlog` treats a journal as balanced only when
``imbalance < IMBALANCE_TOLERANCE``, so exactly 0.01 is an imbalance there.
Two GL rules disagreeing about the same kobo is precisely the drift worth
removing, and the stricter reading is the safe one for a one-way act: this
now blocks at 0.01 rather than letting it through.

Opens no session, sets no scope and never commits — the caller owns the
transaction.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.finance.gl.posting_backlog import IMBALANCE_TOLERANCE

logger = logging.getLogger(__name__)

# Journal states that are NOT settled. APPROVED is included deliberately:
# closing over an approved-but-unposted journal strands it forever.
UNPOSTED_STATUSES = ("DRAFT", "SUBMITTED", "APPROVED")

# Period states that can still be closed.
CLOSEABLE_STATUSES = ("OPEN", "REOPENED")

_PERIODS_SQL = """
    SELECT fp.fiscal_period_id, fp.period_name, fp.period_number,
           fp.start_date, fp.end_date, fp.status, fy.year_name
    FROM gl.fiscal_period fp
    JOIN gl.fiscal_year fy ON fy.fiscal_year_id = fp.fiscal_year_id
    WHERE fp.organization_id = :org_id
      AND fp.status = ANY(:closeable)
      AND (:through_date::date IS NULL OR fp.end_date <= :through_date::date)
      AND (:year::int IS NULL OR EXTRACT(YEAR FROM fp.start_date) = :year::int)
    ORDER BY fp.start_date
"""

_JOURNAL_COUNTS_SQL = text("""
    SELECT COUNT(*) FILTER (WHERE status = ANY(:unposted)) AS unposted,
           COUNT(*) FILTER (WHERE status = 'POSTED')       AS posted
    FROM gl.journal_entry
    WHERE organization_id = :org_id
      AND fiscal_period_id = :period_id
""")

_BALANCE_SQL = text("""
    SELECT COALESCE(SUM(jel.debit_amount), 0),
           COALESCE(SUM(jel.credit_amount), 0)
    FROM gl.journal_entry je
    JOIN gl.journal_entry_line jel
      ON jel.journal_entry_id = je.journal_entry_id
    WHERE je.organization_id = :org_id
      AND je.fiscal_period_id = :period_id
      AND je.status = 'POSTED'
""")


@dataclass(frozen=True)
class PeriodReadiness:
    fiscal_period_id: uuid.UUID
    period_name: str
    year_name: str
    status: str
    end_date: dt.date
    unposted_journals: int
    posted_journals: int
    imbalance: Decimal

    @property
    def blockers(self) -> list[str]:
        """Why this period cannot close. Empty means ready."""
        reasons = []
        if self.unposted_journals:
            reasons.append(f"{self.unposted_journals} unposted journal(s)")
        if self.imbalance >= IMBALANCE_TOLERANCE:
            reasons.append(f"trial balance out by {self.imbalance}")
        return reasons

    @property
    def is_ready(self) -> bool:
        return not self.blockers


@dataclass
class PeriodCloseResult:
    assessed: int = 0
    ready: int = 0
    blocked: int = 0
    closed: int = 0
    forced: int = 0
    errors: list[str] = field(default_factory=list)
    blocked_detail: list[tuple[str, list[str]]] = field(default_factory=list)


def assess_periods(
    db: Session,
    *,
    organization_id: uuid.UUID,
    through_date: dt.date | None = None,
    year: int | None = None,
) -> list[PeriodReadiness]:
    """Every closeable period with the facts that decide whether it may close."""
    rows = db.execute(
        text(_PERIODS_SQL),
        {
            "org_id": str(organization_id),
            "closeable": list(CLOSEABLE_STATUSES),
            "through_date": through_date.isoformat() if through_date else None,
            "year": year,
        },
    ).all()

    readiness: list[PeriodReadiness] = []
    for row in rows:
        period_id = str(row[0])
        counts = db.execute(
            _JOURNAL_COUNTS_SQL,
            {
                "org_id": str(organization_id),
                "period_id": period_id,
                "unposted": list(UNPOSTED_STATUSES),
            },
        ).one()
        debit, credit = db.execute(
            _BALANCE_SQL,
            {"org_id": str(organization_id), "period_id": period_id},
        ).one()
        readiness.append(
            PeriodReadiness(
                fiscal_period_id=uuid.UUID(period_id),
                period_name=row[1],
                year_name=row[6],
                status=row[5],
                end_date=row[4],
                unposted_journals=int(counts[0]),
                posted_journals=int(counts[1]),
                imbalance=abs(Decimal(str(debit)) - Decimal(str(credit))),
            )
        )
    return readiness


def close_periods(
    db: Session,
    *,
    organization_id: uuid.UUID,
    closed_by_user_id: uuid.UUID,
    through_date: dt.date | None = None,
    year: int | None = None,
    hard: bool = False,
    force: bool = False,
    dry_run: bool = True,
) -> PeriodCloseResult:
    """Close every period that passes the gate (or all of them, if forced).

    Does not commit — the caller owns the transaction.
    """
    from app.services.finance.gl.fiscal_period import FiscalPeriodService

    periods = assess_periods(
        db, organization_id=organization_id, through_date=through_date, year=year
    )
    result = PeriodCloseResult(assessed=len(periods))

    for period in periods:
        if period.is_ready:
            result.ready += 1
        else:
            result.blocked += 1
            result.blocked_detail.append((period.period_name, period.blockers))

    to_close = periods if force else [p for p in periods if p.is_ready]
    result.forced = sum(1 for p in to_close if not p.is_ready)
    if result.forced:
        logger.warning("force: closing %d period(s) over their blockers", result.forced)

    if dry_run:
        return result

    for period in to_close:
        try:
            # A hard close requires the period to be soft-closed first, so an
            # OPEN period takes two transitions rather than one.
            if hard and period.status == "OPEN":
                FiscalPeriodService.soft_close_period(
                    db=db,
                    organization_id=organization_id,
                    fiscal_period_id=period.fiscal_period_id,
                    closed_by_user_id=closed_by_user_id,
                )
            closer = (
                FiscalPeriodService.hard_close_period
                if hard
                else FiscalPeriodService.soft_close_period
            )
            closer(
                db=db,
                organization_id=organization_id,
                fiscal_period_id=period.fiscal_period_id,
                closed_by_user_id=closed_by_user_id,
            )
            result.closed += 1
        except Exception as exc:
            result.errors.append(f"{period.period_name}: {exc}")
            logger.warning("Failed to close %s", period.period_name)

    return result
