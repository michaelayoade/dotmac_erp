#!/usr/bin/env python3
"""
Match ERPNext Internal Transfer payment links to Dotmac bank statement lines.

Safe mode only:
- only considers unmatched ERPNEXT_SQL statement lines
- only considers Payment Entry links with no synced target mapping
- only processes ERP Payment Entry records with payment_type=Internal Transfer
- requires exactly one debit line and one credit line for the same Payment Entry
- requires equal amounts (no fee/delta handling)
"""

from __future__ import annotations

import argparse
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pymysql
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    StatementLineType,
)
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.sync import SyncEntity
from app.services.finance.banking.bank_reconciliation import BankReconciliationService
from app.services.finance.gl.journal import JournalInput, JournalLineInput
from app.services.finance.posting.base import BasePostingAdapter

logger = logging.getLogger("match_erpnext_internal_transfer_links")


@dataclass
class Pair:
    payment_entry: str
    debit_line: BankStatementLine
    credit_line: BankStatementLine
    amount: Decimal
    debit_gl_account_id: uuid.UUID
    credit_gl_account_id: uuid.UUID
    posting_date: date


@dataclass
class Stats:
    candidate_lines: int = 0
    candidate_payment_entries: int = 0
    exact_pairs: int = 0
    journals_created: int = 0
    journals_reused: int = 0
    periods_reopened: int = 0
    lines_matched: int = 0
    skipped: int = 0
    errors: int = 0


def _mysql_connect() -> pymysql.Connection:
    return pymysql.connect(
        host=os.getenv("ERPNEXT_SQL_HOST", "127.0.0.1"),
        port=int(os.getenv("ERPNEXT_SQL_PORT", "3307")),
        user=os.getenv("ERPNEXT_SQL_USER", "root"),
        password=os.getenv("ERPNEXT_SQL_PASSWORD", "root"),
        database=os.getenv("ERPNEXT_SQL_DATABASE", "erpnext_temp"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _load_internal_transfer_names(
    mysql_conn: pymysql.Connection,
    names: list[str],
) -> set[str]:
    if not names:
        return set()
    valid: set[str] = set()
    with mysql_conn.cursor() as cur:
        for i in range(0, len(names), 400):
            chunk = names[i : i + 400]
            placeholders = ",".join(["%s"] * len(chunk))
            cur.execute(
                f"""
                SELECT name
                FROM `tabPayment Entry`
                WHERE name IN ({placeholders})
                  AND docstatus = 1
                  AND payment_type = 'Internal Transfer'
                """,
                chunk,
            )
            valid.update(str(r["name"]) for r in (cur.fetchall() or []))
    return valid


def _find_exact_pairs(
    db: Session,
    organization_id: uuid.UUID,
    mysql_conn: pymysql.Connection,
    stats: Stats,
) -> list[Pair]:
    synced_payment_entries = {
        source_name
        for (source_name,) in db.execute(
            select(SyncEntity.source_name).where(
                SyncEntity.organization_id == organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Payment Entry",
                SyncEntity.target_id.is_not(None),
            )
        ).all()
    }

    rows = db.execute(
        select(BankStatementLine, BankStatement.bank_account_id)
        .join(
            BankStatement, BankStatement.statement_id == BankStatementLine.statement_id
        )
        .where(
            BankStatement.organization_id == organization_id,
            BankStatement.import_source == "ERPNEXT_SQL",
            BankStatementLine.is_matched.is_(False),
        )
    ).all()

    bank_gl = {
        bank_id: gl_id
        for bank_id, gl_id in db.execute(
            select(BankAccount.bank_account_id, BankAccount.gl_account_id)
        ).all()
    }

    by_payment_entry: dict[
        str, dict[uuid.UUID, tuple[BankStatementLine, uuid.UUID]]
    ] = {}
    for line, bank_account_id in rows:
        raw = line.raw_data or {}
        links = list(raw.get("erpnext_payment_links") or [])
        if not links:
            continue
        stats.candidate_lines += 1
        for link in links:
            if str(link.get("payment_document") or "") != "Payment Entry":
                continue
            pe = str(link.get("payment_entry") or "").strip()
            if not pe or pe in synced_payment_entries:
                continue
            by_line = by_payment_entry.setdefault(pe, {})
            by_line[line.line_id] = (line, bank_account_id)

    stats.candidate_payment_entries = len(by_payment_entry)
    valid_internal = _load_internal_transfer_names(
        mysql_conn, list(by_payment_entry.keys())
    )

    pairs: list[Pair] = []
    for pe, line_map in by_payment_entry.items():
        if pe not in valid_internal:
            continue
        items = list(line_map.values())
        debits = [x for x in items if x[0].transaction_type == StatementLineType.debit]
        credits = [
            x for x in items if x[0].transaction_type == StatementLineType.credit
        ]
        if len(debits) != 1 or len(credits) != 1:
            stats.skipped += 1
            continue
        debit_line, debit_bank = debits[0]
        credit_line, credit_bank = credits[0]
        if debit_line.amount != credit_line.amount:
            stats.skipped += 1
            continue
        debit_gl = bank_gl.get(debit_bank)
        credit_gl = bank_gl.get(credit_bank)
        if not debit_gl or not credit_gl:
            stats.skipped += 1
            continue
        if debit_gl == credit_gl:
            stats.skipped += 1
            continue
        pairs.append(
            Pair(
                payment_entry=pe,
                debit_line=debit_line,
                credit_line=credit_line,
                amount=debit_line.amount,
                debit_gl_account_id=debit_gl,
                credit_gl_account_id=credit_gl,
                posting_date=credit_line.transaction_date,
            )
        )
    stats.exact_pairs = len(pairs)
    return pairs


def _get_or_create_journal(
    db: Session,
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    pair: Pair,
    dry_run: bool,
    stats: Stats,
) -> JournalEntry | None:
    correlation_id = f"erpnext-int-transfer:{pair.payment_entry}"
    existing = (
        db.execute(
            select(JournalEntry).where(
                JournalEntry.organization_id == organization_id,
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
        .scalars()
        .first()
    )
    if existing:
        stats.journals_reused += 1
        if dry_run:
            return existing
        if existing.status == JournalStatus.POSTED:
            return existing
        # Reuse previously created journal and retry posting.
        posting_result = BasePostingAdapter.post_to_ledger(
            db=db,
            organization_id=organization_id,
            journal_entry_id=existing.journal_entry_id,
            posting_date=pair.posting_date,
            idempotency_key=f"erpnext-int-transfer:{pair.payment_entry}",
            source_module="BANKING",
            correlation_id=correlation_id,
            posted_by_user_id=user_id,
            success_message=f"Posted internal transfer {pair.payment_entry}",
            error_prefix=f"Internal transfer {pair.payment_entry} posting failed",
        )
        if not posting_result.success:
            stats.errors += 1
            logger.error("%s", posting_result.message)
            return None
        return existing
    if dry_run:
        return None

    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=pair.posting_date,
        posting_date=pair.posting_date,
        description=f"ERPNext Internal Transfer {pair.payment_entry}",
        lines=[
            JournalLineInput(
                account_id=pair.credit_gl_account_id,
                debit_amount=pair.amount,
                description=f"Internal transfer in: {pair.payment_entry}",
            ),
            JournalLineInput(
                account_id=pair.debit_gl_account_id,
                credit_amount=pair.amount,
                description=f"Internal transfer out: {pair.payment_entry}",
            ),
        ],
        reference=pair.payment_entry[:100],
        source_module="BANKING",
        source_document_type="INTERBANK_TRANSFER",
        correlation_id=correlation_id,
    )

    journal, create_error = BasePostingAdapter.create_and_approve_journal(
        db=db,
        organization_id=organization_id,
        journal_input=journal_input,
        posted_by_user_id=user_id,
        error_prefix=f"Internal transfer {pair.payment_entry} create failed",
    )
    if create_error:
        stats.errors += 1
        logger.error("%s", create_error.message)
        return None

    posting_result = BasePostingAdapter.post_to_ledger(
        db=db,
        organization_id=organization_id,
        journal_entry_id=journal.journal_entry_id,
        posting_date=pair.posting_date,
        idempotency_key=f"erpnext-int-transfer:{pair.payment_entry}",
        source_module="BANKING",
        correlation_id=correlation_id,
        posted_by_user_id=user_id,
        success_message=f"Posted internal transfer {pair.payment_entry}",
        error_prefix=f"Internal transfer {pair.payment_entry} posting failed",
    )
    if not posting_result.success:
        stats.errors += 1
        logger.error("%s", posting_result.message)
        return None

    stats.journals_created += 1
    return journal


def _reopen_periods_for_dates(
    db: Session,
    organization_id: uuid.UUID,
    dates: set[date],
    stats: Stats,
) -> dict[uuid.UUID, PeriodStatus]:
    original_status: dict[uuid.UUID, PeriodStatus] = {}
    for d in sorted(dates):
        period = db.execute(
            select(FiscalPeriod).where(
                FiscalPeriod.organization_id == organization_id,
                FiscalPeriod.start_date <= d,
                FiscalPeriod.end_date >= d,
            )
        ).scalar_one_or_none()
        if not period:
            continue
        if period.status in {PeriodStatus.HARD_CLOSED, PeriodStatus.SOFT_CLOSED}:
            original_status[period.fiscal_period_id] = period.status
            period.status = PeriodStatus.OPEN
    if original_status:
        stats.periods_reopened = len(original_status)
        db.flush()
    return original_status


def _restore_periods(
    db: Session, original_status: dict[uuid.UUID, PeriodStatus]
) -> None:
    for period_id, status in original_status.items():
        period = db.get(FiscalPeriod, period_id)
        if period:
            period.status = status
    if original_status:
        db.flush()


def _find_journal_line(
    db: Session,
    journal_entry_id: uuid.UUID,
    account_id: uuid.UUID,
    debit_amount: Decimal | None = None,
    credit_amount: Decimal | None = None,
) -> JournalEntryLine | None:
    q = select(JournalEntryLine).where(
        JournalEntryLine.journal_entry_id == journal_entry_id,
        JournalEntryLine.account_id == account_id,
    )
    if debit_amount is not None:
        q = q.where(
            JournalEntryLine.debit_amount >= debit_amount - Decimal("0.01"),
            JournalEntryLine.debit_amount <= debit_amount + Decimal("0.01"),
        )
    if credit_amount is not None:
        q = q.where(
            JournalEntryLine.credit_amount >= credit_amount - Decimal("0.01"),
            JournalEntryLine.credit_amount <= credit_amount + Decimal("0.01"),
        )
    lines = list(db.execute(q).scalars().all())
    if len(lines) == 1:
        return lines[0]
    return None


def run(args: argparse.Namespace) -> int:
    organization_id = uuid.UUID(args.org_id)
    user_id = uuid.UUID(args.user_id)
    dry_run = args.dry_run
    max_pairs = args.max
    stats = Stats()

    mysql_conn = _mysql_connect()
    recon = BankReconciliationService()
    try:
        with SessionLocal() as db:
            assert isinstance(db, Session)
            pairs = _find_exact_pairs(db, organization_id, mysql_conn, stats)
            if max_pairs > 0:
                pairs = pairs[:max_pairs]
            original_period_status: dict[uuid.UUID, PeriodStatus] = {}
            if not dry_run and pairs:
                original_period_status = _reopen_periods_for_dates(
                    db, organization_id, {p.posting_date for p in pairs}, stats
                )

            logger.info(
                "Found %d exact internal-transfer pairs%s",
                len(pairs),
                " (dry-run)" if dry_run else "",
            )

            for i, pair in enumerate(pairs, start=1):
                logger.info(
                    "[%d/%d] %s amount=%s debit_line=%s credit_line=%s",
                    i,
                    len(pairs),
                    pair.payment_entry,
                    pair.amount,
                    pair.debit_line.line_id,
                    pair.credit_line.line_id,
                )
                journal = _get_or_create_journal(
                    db=db,
                    organization_id=organization_id,
                    user_id=user_id,
                    pair=pair,
                    dry_run=dry_run,
                    stats=stats,
                )
                if dry_run:
                    continue
                if not journal:
                    continue

                debit_stmt_to_gl_credit = _find_journal_line(
                    db,
                    journal.journal_entry_id,
                    pair.debit_gl_account_id,
                    credit_amount=pair.amount,
                )
                credit_stmt_to_gl_debit = _find_journal_line(
                    db,
                    journal.journal_entry_id,
                    pair.credit_gl_account_id,
                    debit_amount=pair.amount,
                )
                if not debit_stmt_to_gl_credit or not credit_stmt_to_gl_debit:
                    stats.errors += 1
                    logger.warning(
                        "Could not uniquely resolve journal lines for %s",
                        pair.payment_entry,
                    )
                    continue

                if not pair.debit_line.is_matched:
                    recon.match_statement_line(
                        db=db,
                        organization_id=organization_id,
                        statement_line_id=pair.debit_line.line_id,
                        journal_line_id=debit_stmt_to_gl_credit.line_id,
                        matched_by=user_id,
                        force_match=True,
                    )
                    stats.lines_matched += 1
                if not pair.credit_line.is_matched:
                    recon.match_statement_line(
                        db=db,
                        organization_id=organization_id,
                        statement_line_id=pair.credit_line.line_id,
                        journal_line_id=credit_stmt_to_gl_debit.line_id,
                        matched_by=user_id,
                        force_match=True,
                    )
                    stats.lines_matched += 1

            if dry_run:
                db.rollback()
            else:
                _restore_periods(db, original_period_status)
                db.commit()
    finally:
        mysql_conn.close()

    logger.info(
        "Done%s | candidate_lines=%d candidate_payment_entries=%d exact_pairs=%d "
        "journals_created=%d journals_reused=%d periods_reopened=%d "
        "lines_matched=%d skipped=%d errors=%d",
        " (dry-run)" if dry_run else "",
        stats.candidate_lines,
        stats.candidate_payment_entries,
        stats.exact_pairs,
        stats.journals_created,
        stats.journals_reused,
        stats.periods_reopened,
        stats.lines_matched,
        stats.skipped,
        stats.errors,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Match ERPNext internal transfer payment links in banking."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Preview only")
    mode.add_argument("--execute", action="store_true", help="Apply changes")
    parser.add_argument(
        "--org-id",
        default="00000000-0000-0000-0000-000000000001",
        help="Organization UUID",
    )
    parser.add_argument(
        "--user-id",
        default="00000000-0000-0000-0000-000000000001",
        help="User UUID for created/matched actions",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=0,
        help="Optional max exact pairs to process (0 = all)",
    )
    return parser


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = build_parser().parse_args()
    if not args.dry_run and not args.execute:
        raise SystemExit("Specify one of --dry-run or --execute")
    raise SystemExit(run(args))
