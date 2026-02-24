#!/usr/bin/env python3
"""
Heuristic matcher for ERPNext-linked bank statement lines.

Reads `raw_data.erpnext_payment_links` on `banking.bank_statement_lines`,
resolves ERPNext Payment Entry / Journal Entry mappings, then creates
`banking.bank_statement_line_matches` using relaxed amount/account heuristics.
"""

from __future__ import annotations

import argparse
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.expense.expense_claim import ExpenseClaim
from app.models.finance.ap.supplier_payment import SupplierPayment
from app.models.finance.ar.customer_payment import CustomerPayment
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementLine,
    BankStatementLineMatch,
    StatementLineType,
)
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.sync import SyncEntity

logger = logging.getLogger("match_erpnext_banking_links")


@dataclass
class MatchStats:
    candidate_lines: int = 0
    links_seen: int = 0
    links_resolved_journal: int = 0
    matches_created: int = 0
    lines_marked_matched: int = 0
    skipped_existing_match: int = 0


def _to_decimal(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def _resolve_linked_journal_ids(
    db: Session,
    organization_id: uuid.UUID,
) -> tuple[
    dict[str, tuple[str, uuid.UUID | None]],
    dict[str, uuid.UUID | None],
    dict[uuid.UUID, uuid.UUID | None],
    dict[uuid.UUID, uuid.UUID | None],
    dict[uuid.UUID, uuid.UUID | None],
]:
    payment_sync = {
        source_name: (target_table or "", target_id)
        for source_name, target_table, target_id in db.execute(
            select(
                SyncEntity.source_name,
                SyncEntity.target_table,
                SyncEntity.target_id,
            ).where(
                SyncEntity.organization_id == organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Payment Entry",
                SyncEntity.target_id.is_not(None),
            )
        ).all()
    }
    journal_sync = {
        source_name: target_id
        for source_name, target_id in db.execute(
            select(SyncEntity.source_name, SyncEntity.target_id).where(
                SyncEntity.organization_id == organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == "Journal Entry",
                SyncEntity.target_id.is_not(None),
            )
        ).all()
    }

    ar_ids = [
        tid
        for table, tid in payment_sync.values()
        if table == "ar.customer_payment" and tid
    ]
    ap_ids = [
        tid
        for table, tid in payment_sync.values()
        if table == "ap.supplier_payment" and tid
    ]
    expense_claim_ids = [
        tid
        for table, tid in payment_sync.values()
        if table == "expense.expense_claim" and tid
    ]

    ar_journal_by_payment: dict[uuid.UUID, uuid.UUID | None] = {}
    ap_journal_by_payment: dict[uuid.UUID, uuid.UUID | None] = {}
    expense_journal_by_claim: dict[uuid.UUID, uuid.UUID | None] = {}
    if ar_ids:
        for pid, jeid in db.execute(
            select(CustomerPayment.payment_id, CustomerPayment.journal_entry_id).where(
                CustomerPayment.payment_id.in_(ar_ids)
            )
        ).all():
            ar_journal_by_payment[pid] = jeid
    if ap_ids:
        for pid, jeid in db.execute(
            select(SupplierPayment.payment_id, SupplierPayment.journal_entry_id).where(
                SupplierPayment.payment_id.in_(ap_ids)
            )
        ).all():
            ap_journal_by_payment[pid] = jeid
    if expense_claim_ids:
        for cid, reimb_jeid, claim_jeid in db.execute(
            select(
                ExpenseClaim.claim_id,
                ExpenseClaim.reimbursement_journal_id,
                ExpenseClaim.journal_entry_id,
            ).where(ExpenseClaim.claim_id.in_(expense_claim_ids))
        ).all():
            # Payment Entry for employee reimbursement should hit reimbursement journal
            # first; fallback to claim journal when reimbursement journal is absent.
            expense_journal_by_claim[cid] = reimb_jeid or claim_jeid

    return (
        payment_sync,
        journal_sync,
        ar_journal_by_payment,
        ap_journal_by_payment,
        expense_journal_by_claim,
    )


def _score_candidate(
    line: BankStatementLine,
    target_amount: Decimal,
    bank_gl_account_id: uuid.UUID | None,
    candidate: JournalEntryLine,
) -> int:
    debit = candidate.debit_amount or Decimal("0")
    credit = candidate.credit_amount or Decimal("0")
    preferred_side_amt = (
        debit if line.transaction_type == StatementLineType.credit else credit
    )
    counter_side_amt = (
        credit if line.transaction_type == StatementLineType.credit else debit
    )
    net_amt = abs(debit - credit)

    same_account = (
        bank_gl_account_id is not None and candidate.account_id == bank_gl_account_id
    )
    exact_preferred = abs(preferred_side_amt - target_amount) <= Decimal("0.01")
    exact_net = abs(net_amt - target_amount) <= Decimal("0.01")
    exact_counter = abs(counter_side_amt - target_amount) <= Decimal("0.01")

    if same_account and exact_preferred:
        return 100
    if same_account and exact_net:
        return 90
    if exact_preferred:
        return 80
    if exact_net:
        return 70
    if same_account and exact_counter:
        return 60
    if exact_counter:
        return 50
    return 0


def run(args: argparse.Namespace) -> int:
    organization_id = uuid.UUID(args.org_id)
    user_id = uuid.UUID(args.user_id)
    dry_run = args.dry_run
    min_score = args.min_score
    limit = args.limit

    stats = MatchStats()

    with SessionLocal() as db:
        assert isinstance(db, Session)
        (
            payment_sync,
            journal_sync,
            ar_journal_by_payment,
            ap_journal_by_payment,
            expense_journal_by_claim,
        ) = _resolve_linked_journal_ids(db, organization_id)

        rows = db.execute(
            select(BankStatementLine, BankStatement.bank_account_id)
            .join(
                BankStatement,
                BankStatement.statement_id == BankStatementLine.statement_id,
            )
            .where(
                BankStatement.organization_id == organization_id,
                BankStatement.import_source == "ERPNEXT_SQL",
                BankStatementLine.raw_data.is_not(None),
            )
        ).all()

        bank_gl_map = {
            ba_id: gl_id
            for ba_id, gl_id in db.execute(
                select(BankAccount.bank_account_id, BankAccount.gl_account_id)
            ).all()
        }

        existing_pairs = {
            (sid, jid)
            for sid, jid in db.execute(
                select(
                    BankStatementLineMatch.statement_line_id,
                    BankStatementLineMatch.journal_line_id,
                )
            ).all()
        }
        existing_line_ids = {
            sid
            for (sid,) in db.execute(
                select(BankStatementLineMatch.statement_line_id).distinct()
            ).all()
        }

        # Collect journal IDs needed for all links first.
        needed_journal_ids: set[uuid.UUID] = set()
        per_line_links: list[
            tuple[BankStatementLine, uuid.UUID, list[dict[str, Any]]]
        ] = []
        for line, bank_account_id in rows:
            links = list((line.raw_data or {}).get("erpnext_payment_links") or [])
            if not links:
                continue
            stats.candidate_lines += 1
            stats.links_seen += len(links)
            per_line_links.append((line, bank_account_id, links))
            for link in links:
                doc = str(link.get("payment_document") or "")
                name = str(link.get("payment_entry") or "")
                if not name:
                    continue
                journal_id: uuid.UUID | None = None
                if doc == "Payment Entry":
                    target = payment_sync.get(name)
                    if target:
                        table, target_id = target
                        if table == "ar.customer_payment" and target_id:
                            journal_id = ar_journal_by_payment.get(target_id)
                        elif table == "ap.supplier_payment" and target_id:
                            journal_id = ap_journal_by_payment.get(target_id)
                        elif table == "expense.expense_claim" and target_id:
                            journal_id = expense_journal_by_claim.get(target_id)
                elif doc == "Journal Entry":
                    journal_id = journal_sync.get(name)
                if journal_id:
                    needed_journal_ids.add(journal_id)
                    stats.links_resolved_journal += 1

        journal_lines_by_entry: dict[uuid.UUID, list[JournalEntryLine]] = {}
        if needed_journal_ids:
            for jel in db.execute(
                select(JournalEntryLine).where(
                    JournalEntryLine.journal_entry_id.in_(needed_journal_ids)
                )
            ).scalars():
                journal_lines_by_entry.setdefault(jel.journal_entry_id, []).append(jel)

        processed = 0
        for line, bank_account_id, links in per_line_links:
            if limit and processed >= limit:
                break
            processed += 1

            best_pair: tuple[JournalEntryLine, int, str, str] | None = None
            bank_gl_account_id = bank_gl_map.get(bank_account_id)
            for link in links:
                doc = str(link.get("payment_document") or "")
                name = str(link.get("payment_entry") or "")
                if not name:
                    continue

                journal_id: uuid.UUID | None = None
                if doc == "Payment Entry":
                    target = payment_sync.get(name)
                    if target:
                        table, target_id = target
                        if table == "ar.customer_payment" and target_id:
                            journal_id = ar_journal_by_payment.get(target_id)
                        elif table == "ap.supplier_payment" and target_id:
                            journal_id = ap_journal_by_payment.get(target_id)
                        elif table == "expense.expense_claim" and target_id:
                            journal_id = expense_journal_by_claim.get(target_id)
                elif doc == "Journal Entry":
                    journal_id = journal_sync.get(name)
                if not journal_id:
                    continue

                target_amount = _to_decimal(link.get("allocated_amount"))
                if target_amount <= 0:
                    target_amount = line.amount

                for cand in journal_lines_by_entry.get(journal_id, []):
                    score = _score_candidate(
                        line, target_amount, bank_gl_account_id, cand
                    )
                    if score < min_score:
                        continue
                    if not best_pair or score > best_pair[1]:
                        best_pair = (cand, score, doc, name)

            if not best_pair:
                continue

            cand, score, doc, name = best_pair
            pair = (line.line_id, cand.line_id)
            if pair in existing_pairs:
                stats.skipped_existing_match += 1
                continue

            is_primary = line.line_id not in existing_line_ids
            existing_pairs.add(pair)
            existing_line_ids.add(line.line_id)

            db.add(
                BankStatementLineMatch(
                    statement_line_id=line.line_id,
                    journal_line_id=cand.line_id,
                    matched_by=user_id,
                    is_primary=is_primary,
                    match_type="ERPNEXT_HEURISTIC",
                    match_reason={
                        "source": "erpnext_payment_links",
                        "score": score,
                        "payment_document": doc,
                        "payment_entry": name,
                    },
                    idempotency_key=f"erpnext-heur:{line.line_id}:{cand.line_id}",
                )
            )
            stats.matches_created += 1

            if not line.is_matched:
                line.is_matched = True
                line.matched_at = datetime.now(UTC)
                line.matched_by = user_id
                line.matched_journal_line_id = cand.line_id
                stats.lines_marked_matched += 1

            if stats.matches_created % 2000 == 0:
                db.flush()
                logger.info("Created %d matches...", stats.matches_created)

        if dry_run:
            db.rollback()
        else:
            db.commit()

    logger.info(
        "Done%s | candidate_lines=%d links_seen=%d links_with_journal=%d "
        "matches_created=%d lines_marked_matched=%d skipped_existing=%d",
        " (dry-run)" if dry_run else "",
        stats.candidate_lines,
        stats.links_seen,
        stats.links_resolved_journal,
        stats.matches_created,
        stats.lines_marked_matched,
        stats.skipped_existing_match,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Match ERPNext bank links to GL lines."
    )
    parser.add_argument(
        "--org-id",
        default="00000000-0000-0000-0000-000000000001",
        help="Organization UUID",
    )
    parser.add_argument(
        "--user-id",
        default="00000000-0000-0000-0000-000000000001",
        help="Matcher user UUID",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=70,
        help="Minimum heuristic score threshold (default: 70)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max lines to process (0 = all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without committing matches",
    )
    return parser


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    raise SystemExit(run(build_parser().parse_args()))
