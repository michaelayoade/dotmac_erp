"""Match remaining 79 unmatched NGN bank statement lines for 2025.

Contra accounts are pre-determined from ERPNext cross-reference analysis:
- Zenith 461 debits → Accounts Receivable (customer refunds)
- UBA credits → Flutterwave (settlement deposits)
- UBA debits → TAJ Bank or Zenith USD (internal transfers)
- Zenith 523 debits → Expense Payable
- Zenith 523 credits → PAYE Payables or Expense Payable
- Paystack OPEX → Expense Payable

Usage:
    python scripts/match_2025_remaining_ngn.py --dry-run
    python scripts/match_2025_remaining_ngn.py --execute
"""

from __future__ import annotations

import argparse
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
)
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.finance.banking.bank_reconciliation import (
    BankReconciliationService,
)
from app.services.finance.gl.journal import JournalInput, JournalLineInput
from app.services.finance.posting.base import BasePostingAdapter

logger = logging.getLogger("match_2025_remaining_ngn")

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# ── GL account UUIDs ─────────────────────────────────────────────────────────

ACCT = {
    "flutterwave": uuid.UUID("f361a518-7b03-4a46-b9c9-23b1cdd9c2ae"),
    "uba": uuid.UUID("9e438125-f8b2-4edf-b448-b832b3fff8f0"),
    "ar": uuid.UUID("4620a846-cc0f-4b4a-806c-006059005e89"),
    "psk_opex": uuid.UUID("78ae1d9d-5fdd-4d98-b492-b0d50dba7622"),
    "z461": uuid.UUID("13e2ba89-ae0b-4315-8dc0-b7d101a3e6e9"),
    "z523": uuid.UUID("2660a324-4fea-416b-9c42-5e8d501b1463"),
    "expense_payable": uuid.UUID("4a1d5bf2-4cea-43c8-ad68-997e053f4aaa"),
    "paye": uuid.UUID("8b58a4d1-20cd-4144-89d1-cf31d0604464"),
    "taj": uuid.UUID("8db05682-1e3d-4dab-be14-15172f50e7cf"),
    "zenith_usd": uuid.UUID("51c95069-bef1-4189-817b-386c53d5c13a"),
}


# ── Contra account rules ────────────────────────────────────────────────────


def _get_contra(
    bank_gl_id: uuid.UUID,
    txn_type: str,
    amount: Decimal,
    txn_date: date,
) -> uuid.UUID | None:
    """Determine contra account from bank account + direction + amount."""
    # Zenith 461 — all debits are customer refunds
    if bank_gl_id == ACCT["z461"]:
        return ACCT["ar"]

    # UBA Bank
    if bank_gl_id == ACCT["uba"]:
        if txn_type == "credit":
            return ACCT["flutterwave"]  # Flutterwave settlements
        # Debits: 1.625M to Zenith USD, rest to TAJ Bank
        if amount == Decimal("1625000"):
            return ACCT["zenith_usd"]
        return ACCT["taj"]

    # Zenith 523
    if bank_gl_id == ACCT["z523"]:
        if txn_type == "debit":
            return ACCT["expense_payable"]
        # Credits: PAYE refunds (37,228.99) vs expense payable
        if abs(amount - Decimal("37228.99")) < Decimal("0.01"):
            return ACCT["paye"]
        return ACCT["expense_payable"]

    # Paystack OPEX — always Expense Payable
    if bank_gl_id == ACCT["psk_opex"]:
        return ACCT["expense_payable"]

    return None


# ── Stats ────────────────────────────────────────────────────────────────────


@dataclass
class Stats:
    journals_created: int = 0
    lines_matched: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    periods_reopened: int = 0


# ── Helpers ──────────────────────────────────────────────────────────────────


def _load_unmatched_lines(
    db: Session,
) -> list[tuple[BankStatementLine, uuid.UUID]]:
    """Load unmatched NGN bank statement lines for 2025."""
    rows = db.execute(
        select(BankStatementLine, BankAccount.gl_account_id)
        .join(
            BankStatement,
            BankStatement.statement_id == BankStatementLine.statement_id,
        )
        .join(
            BankAccount,
            BankAccount.bank_account_id == BankStatement.bank_account_id,
        )
        .where(
            BankStatement.organization_id == ORG_ID,
            BankStatementLine.is_matched.is_(False),
            BankStatementLine.transaction_date >= date(2025, 1, 1),
            BankStatementLine.transaction_date < date(2026, 1, 1),
            BankAccount.account_name.notin_(["Zenith USD", "UBA USD"]),
        )
    ).all()
    return [(line, gl_id) for line, gl_id in rows]


def _reopen_periods(
    db: Session,
    dates: set[date],
) -> dict[uuid.UUID, PeriodStatus]:
    """Temporarily reopen closed fiscal periods for posting."""
    original: dict[uuid.UUID, PeriodStatus] = {}
    for d in sorted(dates):
        period = db.scalar(
            select(FiscalPeriod).where(
                FiscalPeriod.organization_id == ORG_ID,
                FiscalPeriod.start_date <= d,
                FiscalPeriod.end_date >= d,
            )
        )
        if not period:
            continue
        if period.status in {PeriodStatus.HARD_CLOSED, PeriodStatus.SOFT_CLOSED}:
            if period.fiscal_period_id not in original:
                original[period.fiscal_period_id] = period.status
                period.status = PeriodStatus.OPEN
    if original:
        db.flush()
    return original


def _restore_periods(
    db: Session,
    original: dict[uuid.UUID, PeriodStatus],
) -> None:
    for pid, status in original.items():
        period = db.get(FiscalPeriod, pid)
        if period:
            period.status = status
    if original:
        db.flush()


def _process_line(
    db: Session,
    recon: BankReconciliationService,
    line: BankStatementLine,
    bank_gl_id: uuid.UUID,
    stats: Stats,
    user_id: uuid.UUID,
    dry_run: bool,
) -> bool:
    """Create journal, post, and match one bank statement line."""
    txn_type = line.transaction_type.value
    amount = line.amount
    correlation_id = f"erpnext-import-{line.line_id}"

    # ── Idempotency ──
    existing = db.scalar(
        select(JournalEntry).where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.correlation_id == correlation_id,
            JournalEntry.status == JournalStatus.POSTED,
        )
    )
    if existing:
        gl_line = db.scalar(
            select(JournalEntryLine).where(
                JournalEntryLine.journal_entry_id == existing.journal_entry_id,
                JournalEntryLine.account_id == bank_gl_id,
                (
                    JournalEntryLine.credit_amount > 0
                    if txn_type == "debit"
                    else JournalEntryLine.debit_amount > 0
                ),
            )
        )
        if gl_line and not dry_run:
            recon.match_statement_line(
                db=db,
                organization_id=ORG_ID,
                statement_line_id=line.line_id,
                journal_line_id=gl_line.line_id,
                matched_by=user_id,
                force_match=True,
            )
        if gl_line:
            stats.lines_matched += 1
            return True
        return False

    # ── Determine contra ──
    contra_id = _get_contra(bank_gl_id, txn_type, amount, line.transaction_date)
    if contra_id is None:
        stats.skipped.append(
            f"{line.transaction_date} {txn_type} {amount} — no contra rule"
        )
        return False

    if dry_run:
        stats.journals_created += 1
        stats.lines_matched += 1
        return True

    # ── Build journal ──
    desc = (line.description or "")[:60]
    if txn_type == "debit":
        journal_lines = [
            JournalLineInput(
                account_id=contra_id, debit_amount=amount, description=desc
            ),
            JournalLineInput(
                account_id=bank_gl_id, credit_amount=amount, description=desc
            ),
        ]
    else:
        journal_lines = [
            JournalLineInput(
                account_id=bank_gl_id, debit_amount=amount, description=desc
            ),
            JournalLineInput(
                account_id=contra_id, credit_amount=amount, description=desc
            ),
        ]

    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=line.transaction_date,
        posting_date=line.transaction_date,
        description=f"ERPNext import — {desc}",
        reference=line.reference or "",
        source_module="BANKING",
        source_document_type="ERPNEXT_IMPORT",
        correlation_id=correlation_id,
        lines=journal_lines,
    )

    journal, err = BasePostingAdapter.create_and_approve_journal(
        db=db,
        organization_id=ORG_ID,
        journal_input=journal_input,
        posted_by_user_id=user_id,
        error_prefix=f"Journal create failed {line.line_id}",
    )
    if err:
        stats.errors.append(f"Create: {line.line_id}: {err.message}")
        return False

    # ── Post ──
    idem_key = BasePostingAdapter.make_idempotency_key(
        ORG_ID,
        "BANKING",
        line.line_id,
        action="erpnext-import",
    )
    result = BasePostingAdapter.post_to_ledger(
        db=db,
        organization_id=ORG_ID,
        journal_entry_id=journal.journal_entry_id,
        posting_date=line.transaction_date,
        idempotency_key=idem_key,
        source_module="BANKING",
        correlation_id=correlation_id,
        posted_by_user_id=user_id,
        success_message="Posted",
        error_prefix="Post failed",
    )
    if not result.success:
        stats.errors.append(f"Post: {line.line_id}: {result.message}")
        return False

    stats.journals_created += 1

    # ── Match ──
    gl_line = db.scalar(
        select(JournalEntryLine).where(
            JournalEntryLine.journal_entry_id == journal.journal_entry_id,
            JournalEntryLine.account_id == bank_gl_id,
            (
                JournalEntryLine.credit_amount > 0
                if txn_type == "debit"
                else JournalEntryLine.debit_amount > 0
            ),
        )
    )
    if gl_line:
        recon.match_statement_line(
            db=db,
            organization_id=ORG_ID,
            statement_line_id=line.line_id,
            journal_line_id=gl_line.line_id,
            matched_by=user_id,
            force_match=True,
        )
        stats.lines_matched += 1
        return True

    stats.errors.append(f"No bank GL line after posting: {line.line_id}")
    return False


# ── Main ─────────────────────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    user_id = uuid.UUID(args.user_id)
    dry_run = args.dry_run
    stats = Stats()
    recon = BankReconciliationService()

    with SessionLocal() as db:
        assert isinstance(db, Session)

        lines = _load_unmatched_lines(db)
        logger.info("Loaded %d unmatched NGN lines", len(lines))
        if not lines:
            logger.info("Nothing to do")
            return 0

        # Reopen periods
        all_dates = {line.transaction_date for line, _ in lines}
        original_periods: dict[uuid.UUID, PeriodStatus] = {}
        if not dry_run:
            original_periods = _reopen_periods(db, all_dates)
            stats.periods_reopened = len(original_periods)
            if original_periods:
                logger.info("Reopened %d fiscal periods", len(original_periods))

        # Process
        for i, (line, gl_id) in enumerate(lines, 1):
            desc = (line.description or "")[:40]
            logger.info(
                "[%d/%d] %s %s %s %s",
                i,
                len(lines),
                line.transaction_date,
                line.transaction_type.value,
                line.amount,
                desc,
            )
            ok = _process_line(db, recon, line, gl_id, stats, user_id, dry_run)
            logger.info("  %s", "OK" if ok else "SKIP/ERR")

        # Finalize
        if dry_run:
            db.rollback()
        else:
            if original_periods:
                _restore_periods(db, original_periods)
            db.commit()

    # Report
    print(f"\n{'=' * 60}")
    print(f"{'DRY RUN — ' if dry_run else ''}RESULTS")
    print(f"{'=' * 60}")
    print(f"Total lines:       {len(lines)}")
    print(f"Journals created:  {stats.journals_created}")
    print(f"Lines matched:     {stats.lines_matched}")
    print(f"Skipped:           {len(stats.skipped)}")
    print(f"Errors:            {len(stats.errors)}")
    for s in stats.skipped:
        print(f"  SKIP: {s}")
    for e in stats.errors:
        print(f"  ERR:  {e}")
    remaining = len(lines) - stats.lines_matched
    print(f"Remaining:         {remaining}")
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    p.add_argument("--user-id", default="00000000-0000-0000-0000-000000000001")
    raise SystemExit(run(p.parse_args()))
