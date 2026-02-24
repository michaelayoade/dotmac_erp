#!/usr/bin/env python3
"""
Match the 152 unmatched NGN bank statement lines for FY 2025.

Four matching phases:
1. **ACC-PAY Reference** — expense claim payment debits matched to GL journal
   credit lines using the ACC-PAY-2025-XXXXX reference as join key.
2. **Reversal Reference** — expense claim reversal credits matched to GL reversal
   journal debit lines using the same ACC-PAY reference + is_reversal flag.
   Falls back to matching the original (non-reversal) journal's bank-side debit
   line if no reversal journal exists.
3. **Transfer Charges** — Paystack transfer fee lines (NGN 10 / 25) get a new
   GL journal created (DR Finance Cost 6080, CR Paystack OPEX bank GL) then matched.
4. **Date + Amount Fallback** — remaining lines matched to unmatched GL lines on
   the same bank account by exact amount + date within 7-day window.

Lines that cannot be matched (no GL entry exists) are reported.
"""

from __future__ import annotations

import argparse
import logging
import re
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
    BankStatementLineMatch,
    StatementLineType,
)
from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.journal_entry import (
    JournalEntry,
    JournalStatus,
    JournalType,
)
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.finance.banking.bank_reconciliation import (
    BankReconciliationService,
)
from app.services.finance.gl.journal import JournalInput, JournalLineInput
from app.services.finance.posting.base import BasePostingAdapter

logger = logging.getLogger("match_2025_unmatched_ngn")

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
FINANCE_COST_ACCOUNT_CODE = "6080"
ACC_PAY_RE = re.compile(r"ACC-PAY-2025-\d+")
CHARGE_RE = re.compile(r"Charge for transfer:", re.IGNORECASE)
AMOUNT_TOL = Decimal("0.01")
DATE_WINDOW_DAYS = 7


@dataclass
class Stats:
    """Running statistics for the matching run."""

    phase1_matched: int = 0
    phase2_matched: int = 0
    phase3_journals_created: int = 0
    phase3_matched: int = 0
    phase4_matched: int = 0
    no_gl_entry: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    periods_reopened: int = 0


def _load_unmatched_lines(
    db: Session,
) -> list[tuple[BankStatementLine, uuid.UUID, uuid.UUID]]:
    """Load all 152 unmatched NGN bank statement lines for 2025."""
    rows = db.execute(
        select(
            BankStatementLine,
            BankStatement.bank_account_id,
            BankAccount.gl_account_id,
        )
        .join(
            BankStatement, BankStatement.statement_id == BankStatementLine.statement_id
        )
        .join(BankAccount, BankAccount.bank_account_id == BankStatement.bank_account_id)
        .where(
            BankStatement.organization_id == ORG_ID,
            BankStatementLine.is_matched.is_(False),
            BankStatementLine.transaction_date >= date(2025, 1, 1),
            BankStatementLine.transaction_date < date(2026, 1, 1),
            # Exclude USD accounts
            BankAccount.account_name.notin_(["Zenith USD", "UBA USD"]),
        )
    ).all()
    return [(line, ba_id, gl_id) for line, ba_id, gl_id in rows]


def _load_existing_match_pairs(db: Session) -> set[tuple[uuid.UUID, uuid.UUID]]:
    """Load all existing (statement_line_id, journal_line_id) pairs."""
    return {
        (sid, jid)
        for sid, jid in db.execute(
            select(
                BankStatementLineMatch.statement_line_id,
                BankStatementLineMatch.journal_line_id,
            )
        ).all()
    }


def _reopen_periods(
    db: Session,
    dates: set[date],
) -> dict[uuid.UUID, PeriodStatus]:
    """Temporarily reopen soft/hard-closed fiscal periods for posting."""
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
    """Restore fiscal periods to their original status."""
    for pid, status in original.items():
        period = db.get(FiscalPeriod, pid)
        if period:
            period.status = status
    if original:
        db.flush()


def _do_match(
    db: Session,
    recon: BankReconciliationService,
    line: BankStatementLine,
    gl_line_id: uuid.UUID,
    user_id: uuid.UUID,
    match_type: str,
    *,
    force: bool = True,
) -> None:
    """Match a statement line to a GL journal line via the service."""
    recon.match_statement_line(
        db=db,
        organization_id=ORG_ID,
        statement_line_id=line.line_id,
        journal_line_id=gl_line_id,
        matched_by=user_id,
        force_match=force,
    )


# ────────────────────────────────────────────────────────────────
# Phase 1: ACC-PAY reference match for expense claim payments
# ────────────────────────────────────────────────────────────────


def phase1_acc_pay_debits(
    db: Session,
    recon: BankReconciliationService,
    lines: list[tuple[BankStatementLine, uuid.UUID, uuid.UUID]],
    matched_ids: set[uuid.UUID],
    stats: Stats,
    user_id: uuid.UUID,
    dry_run: bool,
) -> None:
    """Match expense claim payment bank debits to GL credit lines by ACC-PAY ref."""
    logger.info("Phase 1: ACC-PAY reference matching for expense claim payments")

    # Build lookup: ACC-PAY reference → GL credit line on Paystack OPEX accounts
    psk_opex_gl_ids = {
        uuid.UUID("0ebe38df-36cc-4834-b3be-948410bd9565"),
        uuid.UUID("78ae1d9d-5fdd-4d98-b492-b0d50dba7622"),
    }
    gl_by_ref: dict[str, list[tuple[JournalEntryLine, JournalEntry]]] = {}
    rows = db.execute(
        select(JournalEntryLine, JournalEntry)
        .join(
            JournalEntry,
            JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
        )
        .where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date >= date(2025, 1, 1),
            JournalEntry.entry_date < date(2026, 1, 1),
            JournalEntry.reference.like("ACC-PAY-2025-%"),
            JournalEntryLine.account_id.in_(psk_opex_gl_ids),
            JournalEntryLine.credit_amount > 0,
            JournalEntry.is_reversal.is_(False),
        )
    ).all()
    for jel, je in rows:
        gl_by_ref.setdefault(je.reference, []).append((jel, je))

    for line, ba_id, gl_id in lines:
        if line.line_id in matched_ids:
            continue
        if line.transaction_type != StatementLineType.debit:
            continue
        desc = line.description or ""
        m = ACC_PAY_RE.search(desc)
        if not m:
            continue
        ref = m.group(0)
        candidates = gl_by_ref.get(ref, [])
        if not candidates:
            stats.no_gl_entry.append(
                f"Phase1 no GL: {ref} {line.transaction_date} {line.amount}"
            )
            continue

        # Pick best: exact amount match
        best: tuple[JournalEntryLine, JournalEntry] | None = None
        for jel, je in candidates:
            if abs(jel.credit_amount - line.amount) <= AMOUNT_TOL:
                best = (jel, je)
                break
        if not best:
            stats.no_gl_entry.append(
                f"Phase1 amount mismatch: {ref} bank={line.amount} "
                f"gl={candidates[0][0].credit_amount}"
            )
            continue

        jel, je = best
        logger.info(
            "  P1 match: %s bank=%s gl=%s date=%s",
            ref,
            line.amount,
            jel.credit_amount,
            line.transaction_date,
        )
        if not dry_run:
            _do_match(db, recon, line, jel.line_id, user_id, "ACC_PAY_REFERENCE")
        matched_ids.add(line.line_id)
        stats.phase1_matched += 1


# ────────────────────────────────────────────────────────────────
# Phase 2: Reversal reference match
# ────────────────────────────────────────────────────────────────


def phase2_reversals(
    db: Session,
    recon: BankReconciliationService,
    lines: list[tuple[BankStatementLine, uuid.UUID, uuid.UUID]],
    matched_ids: set[uuid.UUID],
    stats: Stats,
    user_id: uuid.UUID,
    dry_run: bool,
) -> None:
    """Match expense claim reversal credits to GL reversal debit lines."""
    logger.info("Phase 2: ACC-PAY reversal matching")

    psk_opex_gl_ids = {
        uuid.UUID("0ebe38df-36cc-4834-b3be-948410bd9565"),
        uuid.UUID("78ae1d9d-5fdd-4d98-b492-b0d50dba7622"),
    }

    # Reversal journals: is_reversal=true, reference=ACC-PAY-2025-XXXXX
    gl_reversal_by_ref: dict[str, list[tuple[JournalEntryLine, JournalEntry]]] = {}
    rows = db.execute(
        select(JournalEntryLine, JournalEntry)
        .join(
            JournalEntry,
            JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
        )
        .where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date >= date(2025, 1, 1),
            JournalEntry.entry_date < date(2026, 1, 1),
            JournalEntry.reference.like("ACC-PAY-2025-%"),
            JournalEntryLine.account_id.in_(psk_opex_gl_ids),
            JournalEntryLine.debit_amount > 0,
            JournalEntry.is_reversal.is_(True),
        )
    ).all()
    for jel, je in rows:
        gl_reversal_by_ref.setdefault(je.reference, []).append((jel, je))

    # Also look for non-reversal debit lines (some reversals are posted as
    # standard journals with different descriptions)
    gl_debit_by_ref: dict[str, list[tuple[JournalEntryLine, JournalEntry]]] = {}
    rows2 = db.execute(
        select(JournalEntryLine, JournalEntry)
        .join(
            JournalEntry,
            JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
        )
        .where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.status == JournalStatus.POSTED,
            JournalEntry.entry_date >= date(2025, 1, 1),
            JournalEntry.entry_date < date(2026, 1, 1),
            JournalEntry.reference.like("ACC-PAY-2025-%"),
            JournalEntryLine.account_id.in_(psk_opex_gl_ids),
            JournalEntryLine.debit_amount > 0,
        )
    ).all()
    for jel, je in rows2:
        gl_debit_by_ref.setdefault(je.reference, []).append((jel, je))

    for line, ba_id, gl_id in lines:
        if line.line_id in matched_ids:
            continue
        if line.transaction_type != StatementLineType.credit:
            continue
        desc = line.description or ""
        m = ACC_PAY_RE.search(desc)
        if not m:
            continue
        ref = m.group(0)

        # Try reversal journal first, then any debit journal with same ref
        candidates = gl_reversal_by_ref.get(ref, []) or gl_debit_by_ref.get(ref, [])
        if not candidates:
            stats.no_gl_entry.append(
                f"Phase2 no reversal GL: {ref} {line.transaction_date} {line.amount}"
            )
            continue

        best: tuple[JournalEntryLine, JournalEntry] | None = None
        for jel, je in candidates:
            if abs(jel.debit_amount - line.amount) <= AMOUNT_TOL:
                best = (jel, je)
                break
        if not best:
            stats.no_gl_entry.append(
                f"Phase2 reversal amount mismatch: {ref} bank={line.amount}"
            )
            continue

        jel, je = best
        logger.info(
            "  P2 match: %s bank=%s gl=%s date=%s reversal=%s",
            ref,
            line.amount,
            jel.debit_amount,
            line.transaction_date,
            je.is_reversal,
        )
        if not dry_run:
            _do_match(db, recon, line, jel.line_id, user_id, "ACC_PAY_REVERSAL")
        matched_ids.add(line.line_id)
        stats.phase2_matched += 1


# ────────────────────────────────────────────────────────────────
# Phase 3: Transfer charges — create GL journals + match
# ────────────────────────────────────────────────────────────────


def phase3_transfer_charges(
    db: Session,
    recon: BankReconciliationService,
    lines: list[tuple[BankStatementLine, uuid.UUID, uuid.UUID]],
    matched_ids: set[uuid.UUID],
    stats: Stats,
    user_id: uuid.UUID,
    dry_run: bool,
) -> None:
    """Create GL journals for Paystack transfer charges and match them."""
    logger.info("Phase 3: Transfer charge matching (creates GL journals)")

    finance_cost = db.scalar(
        select(Account).where(
            Account.organization_id == ORG_ID,
            Account.account_code == FINANCE_COST_ACCOUNT_CODE,
        )
    )
    if not finance_cost:
        logger.error("Finance Cost account (6080) not found — skipping phase 3")
        return

    charge_lines = [
        (line, ba_id, gl_id)
        for line, ba_id, gl_id in lines
        if line.line_id not in matched_ids
        and line.transaction_type == StatementLineType.debit
        and line.description
        and CHARGE_RE.search(line.description)
    ]

    if not charge_lines:
        logger.info("  No transfer charge lines to process")
        return

    logger.info("  Processing %d transfer charge lines", len(charge_lines))

    for line, ba_id, gl_id in charge_lines:
        correlation_id = f"psk-transfer-charge-{line.line_id}"

        # Check for existing journal (idempotency)
        existing = db.scalar(
            select(JournalEntry).where(
                JournalEntry.organization_id == ORG_ID,
                JournalEntry.correlation_id == correlation_id,
                JournalEntry.status.in_(
                    [
                        JournalStatus.DRAFT,
                        JournalStatus.SUBMITTED,
                        JournalStatus.APPROVED,
                        JournalStatus.POSTED,
                    ]
                ),
            )
        )

        if existing and existing.status == JournalStatus.POSTED:
            # Journal exists and is posted — find the credit GL line and match
            credit_line = db.scalar(
                select(JournalEntryLine).where(
                    JournalEntryLine.journal_entry_id == existing.journal_entry_id,
                    JournalEntryLine.account_id == gl_id,
                    JournalEntryLine.credit_amount > 0,
                )
            )
            if credit_line and not dry_run:
                _do_match(
                    db, recon, line, credit_line.line_id, user_id, "TRANSFER_CHARGE"
                )
                matched_ids.add(line.line_id)
                stats.phase3_matched += 1
            continue

        if dry_run:
            logger.info(
                "  P3 would create journal: %s %s %s",
                line.transaction_date,
                line.amount,
                line.description,
            )
            matched_ids.add(line.line_id)
            stats.phase3_matched += 1
            continue

        amount = abs(line.amount)
        journal_input = JournalInput(
            journal_type=JournalType.STANDARD,
            entry_date=line.transaction_date,
            posting_date=line.transaction_date,
            description=f"Paystack transfer charge - {line.description}",
            reference=line.reference,
            source_module="BANKING",
            source_document_type="BANK_FEE",
            correlation_id=correlation_id,
            lines=[
                JournalLineInput(
                    account_id=finance_cost.account_id,
                    debit_amount=amount,
                    description=line.description,
                ),
                JournalLineInput(
                    account_id=gl_id,
                    credit_amount=amount,
                    description=line.description,
                ),
            ],
        )

        journal, create_error = BasePostingAdapter.create_and_approve_journal(
            db=db,
            organization_id=ORG_ID,
            journal_input=journal_input,
            posted_by_user_id=user_id,
            error_prefix=f"Transfer charge journal failed for {line.line_id}",
        )
        if create_error:
            stats.errors.append(
                f"P3 create failed: {line.line_id}: {create_error.message}"
            )
            continue

        idempotency_key = BasePostingAdapter.make_idempotency_key(
            ORG_ID, "BANKING", line.line_id, action="transfer-charge"
        )
        posting_result = BasePostingAdapter.post_to_ledger(
            db=db,
            organization_id=ORG_ID,
            journal_entry_id=journal.journal_entry_id,
            posting_date=line.transaction_date,
            idempotency_key=idempotency_key,
            source_module="BANKING",
            correlation_id=correlation_id,
            posted_by_user_id=user_id,
            success_message="Transfer charge posted",
            error_prefix="Transfer charge posting failed",
        )
        if not posting_result.success:
            stats.errors.append(
                f"P3 post failed: {line.line_id}: {posting_result.message}"
            )
            continue

        stats.phase3_journals_created += 1

        # Find credit GL line and match
        credit_line = db.scalar(
            select(JournalEntryLine).where(
                JournalEntryLine.journal_entry_id == journal.journal_entry_id,
                JournalEntryLine.account_id == gl_id,
                JournalEntryLine.credit_amount > 0,
            )
        )
        if credit_line:
            _do_match(db, recon, line, credit_line.line_id, user_id, "TRANSFER_CHARGE")
            matched_ids.add(line.line_id)
            stats.phase3_matched += 1
        else:
            stats.errors.append(f"P3 no credit line after posting: {line.line_id}")


# ────────────────────────────────────────────────────────────────
# Phase 4: Date + amount fallback
# ────────────────────────────────────────────────────────────────


def phase4_date_amount(
    db: Session,
    recon: BankReconciliationService,
    lines: list[tuple[BankStatementLine, uuid.UUID, uuid.UUID]],
    matched_ids: set[uuid.UUID],
    existing_pairs: set[tuple[uuid.UUID, uuid.UUID]],
    stats: Stats,
    user_id: uuid.UUID,
    dry_run: bool,
) -> None:
    """Match remaining lines by exact amount + date window on same GL account."""
    logger.info("Phase 4: Date + amount fallback matching")

    remaining = [
        (line, ba_id, gl_id)
        for line, ba_id, gl_id in lines
        if line.line_id not in matched_ids
    ]
    if not remaining:
        logger.info("  No remaining lines for phase 4")
        return

    # Load all unmatched GL lines on the relevant bank accounts
    gl_account_ids = {gl_id for _, _, gl_id in remaining}
    already_matched_gl = {
        jid
        for (jid,) in db.execute(
            select(BankStatementLineMatch.journal_line_id).distinct()
        ).all()
    }

    gl_lines: list[tuple[JournalEntryLine, JournalEntry]] = []
    for gl_account_id in gl_account_ids:
        rows = db.execute(
            select(JournalEntryLine, JournalEntry)
            .join(
                JournalEntry,
                JournalEntry.journal_entry_id == JournalEntryLine.journal_entry_id,
            )
            .where(
                JournalEntry.organization_id == ORG_ID,
                JournalEntry.status == JournalStatus.POSTED,
                JournalEntry.entry_date >= date(2024, 12, 1),
                JournalEntry.entry_date < date(2026, 2, 1),
                JournalEntryLine.account_id == gl_account_id,
            )
        ).all()
        gl_lines.extend([(jel, je) for jel, je in rows])

    # Index by (account_id, amount) for fast lookup
    gl_by_key: dict[
        tuple[uuid.UUID, str], list[tuple[JournalEntryLine, JournalEntry]]
    ] = {}
    for jel, je in gl_lines:
        if jel.line_id in already_matched_gl:
            continue
        if jel.debit_amount and jel.debit_amount > 0:
            key = (jel.account_id, f"debit:{jel.debit_amount}")
            gl_by_key.setdefault(key, []).append((jel, je))
        if jel.credit_amount and jel.credit_amount > 0:
            key = (jel.account_id, f"credit:{jel.credit_amount}")
            gl_by_key.setdefault(key, []).append((jel, je))

    used_gl_ids: set[uuid.UUID] = set()

    for line, ba_id, gl_id in remaining:
        # Bank debit → GL credit, Bank credit → GL debit
        if line.transaction_type == StatementLineType.debit:
            lookup_key = (gl_id, f"credit:{line.amount}")
        else:
            lookup_key = (gl_id, f"debit:{line.amount}")

        candidates = gl_by_key.get(lookup_key, [])
        # Filter to date window and not already used
        best: tuple[JournalEntryLine, JournalEntry, int] | None = None
        for jel, je in candidates:
            if jel.line_id in used_gl_ids:
                continue
            if (line.line_id, jel.line_id) in existing_pairs:
                continue
            days_diff = abs((je.entry_date - line.transaction_date).days)
            if days_diff > DATE_WINDOW_DAYS:
                continue
            if best is None or days_diff < best[2]:
                best = (jel, je, days_diff)

        if not best:
            stats.no_gl_entry.append(
                f"Phase4 no match: {line.transaction_date} "
                f"{line.transaction_type.value} {line.amount} "
                f"{(line.description or '')[:60]}"
            )
            continue

        jel, je, days_diff = best
        logger.info(
            "  P4 match: bank %s %s %s → GL %s (date diff %dd)",
            line.transaction_date,
            line.amount,
            (line.description or "")[:40],
            je.entry_date,
            days_diff,
        )
        if not dry_run:
            _do_match(db, recon, line, jel.line_id, user_id, "DATE_AMOUNT_FALLBACK")
        matched_ids.add(line.line_id)
        used_gl_ids.add(jel.line_id)
        stats.phase4_matched += 1


# ────────────────────────────────────────────────────────────────
# Main orchestrator
# ────────────────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    user_id = uuid.UUID(args.user_id)
    dry_run = args.dry_run
    stats = Stats()
    recon = BankReconciliationService()

    with SessionLocal() as db:
        assert isinstance(db, Session)

        lines = _load_unmatched_lines(db)
        logger.info("Loaded %d unmatched NGN lines for 2025", len(lines))

        existing_pairs = _load_existing_match_pairs(db)
        matched_ids: set[uuid.UUID] = set()

        # Temporarily reopen closed periods for journal creation
        all_dates = {line.transaction_date for line, _, _ in lines}
        original_periods: dict[uuid.UUID, PeriodStatus] = {}
        if not dry_run:
            original_periods = _reopen_periods(db, all_dates)
            if original_periods:
                stats.periods_reopened = len(original_periods)
                logger.info(
                    "Temporarily reopened %d fiscal periods", len(original_periods)
                )

        # Run all four phases
        phase1_acc_pay_debits(db, recon, lines, matched_ids, stats, user_id, dry_run)
        phase2_reversals(db, recon, lines, matched_ids, stats, user_id, dry_run)
        phase3_transfer_charges(db, recon, lines, matched_ids, stats, user_id, dry_run)
        phase4_date_amount(
            db, recon, lines, matched_ids, existing_pairs, stats, user_id, dry_run
        )

        if dry_run:
            db.rollback()
            logger.info("DRY RUN — no changes committed")
        else:
            # Restore fiscal periods
            if original_periods:
                _restore_periods(db, original_periods)
                logger.info("Restored %d fiscal periods", len(original_periods))
            db.commit()

    total_matched = (
        stats.phase1_matched
        + stats.phase2_matched
        + stats.phase3_matched
        + stats.phase4_matched
    )
    logger.info(
        "\n"
        "═══════════════════════════════════════════════════\n"
        " Results%s\n"
        "═══════════════════════════════════════════════════\n"
        " Total lines loaded:      %d\n"
        " Phase 1 (ACC-PAY debits): %d matched\n"
        " Phase 2 (Reversals):      %d matched\n"
        " Phase 3 (Charges):        %d matched (%d journals created)\n"
        " Phase 4 (Date+amount):    %d matched\n"
        " ─────────────────────────────────────────────────\n"
        " TOTAL MATCHED:           %d\n"
        " Unmatched (no GL):       %d\n"
        " Errors:                  %d\n"
        " Periods reopened:        %d\n"
        "═══════════════════════════════════════════════════",
        " (DRY RUN)" if dry_run else "",
        len(lines),
        stats.phase1_matched,
        stats.phase2_matched,
        stats.phase3_matched,
        stats.phase3_journals_created,
        stats.phase4_matched,
        total_matched,
        len(stats.no_gl_entry),
        len(stats.errors),
        stats.periods_reopened,
    )

    if stats.no_gl_entry:
        logger.info(
            "\nUnmatched lines (no GL entry found):\n  %s",
            "\n  ".join(stats.no_gl_entry),
        )

    if stats.errors:
        logger.warning(
            "\nErrors:\n  %s",
            "\n  ".join(stats.errors),
        )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Match 2025 unmatched NGN bank statement lines."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview only")
    mode.add_argument("--execute", action="store_true", help="Apply changes")
    parser.add_argument(
        "--user-id",
        default="00000000-0000-0000-0000-000000000001",
        help="User UUID for match attribution",
    )
    return parser


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = build_parser().parse_args()
    raise SystemExit(run(args))
