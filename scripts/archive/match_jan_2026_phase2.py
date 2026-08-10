#!/usr/bin/env python3
"""Match remaining 333 unmatched January 2026 bank statement lines (Phase 2).

After Splynx reconciliation (~1,500 AR payments) and Phase 1 (~885 lines for
charges, inter-bank transfers, AP payments, expense claims), this script
handles the final 333 lines:

  Phase 1: Paystack OPEX ↔ Zenith 523 Topupexp — paired inter-bank transfers
  Phase 2: Customer receipts (Z461 / Paystack / Z523) → AR Trade Receivables
  Phase 3: NHF back-payments (Z523 → Federal Mortgage Bank) → NHF Payables
  Phase 4: Payroll & logistics disbursements (Z523) → Expense Payable
  Phase 5: Paystack settlement debits → UBA GL (single-sided transfer)
  Phase 6: UBA Paystack credits → Paystack GL (single-sided transfer)
  Phase 7: Z523 credits from UBA (internal funding) → UBA GL
  Phase 8: Zenith USD charges → Finance Cost
  Phase 9: Remaining vendor/expense payments → Expense Payable

Usage:
    python scripts/match_jan_2026_phase2.py --dry-run
    python scripts/match_jan_2026_phase2.py --execute
"""

from __future__ import annotations

import argparse
import logging
import re
import uuid
from collections import defaultdict
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
    StatementLineType,
)
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

logger = logging.getLogger("match_jan_2026_p2")

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER = uuid.UUID("00000000-0000-0000-0000-000000000000")
JAN_START = date(2026, 1, 1)
JAN_END = date(2026, 1, 31)

# ── GL account UUIDs ────────────────────────────────────────────────────────

ACCT: dict[str, uuid.UUID] = {
    "z461": uuid.UUID("13e2ba89-ae0b-4315-8dc0-b7d101a3e6e9"),
    "z523": uuid.UUID("2660a324-4fea-416b-9c42-5e8d501b1463"),
    "z454": uuid.UUID("e83771ca-3cbc-48e8-a4dc-bd5ca090e1a0"),
    "uba": uuid.UUID("9e438125-f8b2-4edf-b448-b832b3fff8f0"),
    "zenith_usd": uuid.UUID("51c95069-bef1-4189-817b-386c53d5c13a"),
    "psk_opex": uuid.UUID("78ae1d9d-5fdd-4d98-b492-b0d50dba7622"),
    "ar": uuid.UUID("48d390c3-caad-4964-8064-caa38130a89f"),
    "nhf_payable": uuid.UUID("7d1ccdc4-868e-40fd-9523-80fc1dd8f3f8"),
    "expense_payable": uuid.UUID("4a1d5bf2-4cea-43c8-ad68-997e053f4aaa"),
    "finance_cost": uuid.UUID("e3b904ab-57bd-4429-95a8-a1438ae4ecca"),
}

# ── Description patterns ────────────────────────────────────────────────────

_TOPUPEXP_RE = re.compile(r"(?i)topupexp")
_NHF_RE = re.compile(r"(?i)federal\s+mortgage\s+bank")
_PAYROLL_RE = re.compile(r"(?i)(payroll|81165901)")
_LOGISTICS_RE = re.compile(r"(?i)(logistics|81165940)")
_DOTMAC_UBA_RE = re.compile(r"(?i)dotmac\s+technologies.*(?:uba|cib/uto)")
_PSK_REF_RE = re.compile(r"(?i)(paystack|psst)")

# ── Data structures ─────────────────────────────────────────────────────────

LineRow = tuple[BankStatementLine, uuid.UUID, str]  # line, gl_id, acct_name


@dataclass
class Stats:
    journals_created: int = 0
    lines_matched: int = 0
    pairs_matched: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    by_phase: dict[str, int] = field(default_factory=lambda: defaultdict(int))


# ── Loading & categorisation ────────────────────────────────────────────────


def _load_lines(db: Session) -> list[LineRow]:
    """Load all unmatched Jan 2026 bank statement lines."""
    rows = db.execute(
        select(
            BankStatementLine,
            BankAccount.gl_account_id,
            BankAccount.account_name,
        )
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
            BankStatementLine.transaction_date >= JAN_START,
            BankStatementLine.transaction_date <= JAN_END,
        )
        .order_by(BankStatementLine.transaction_date)
    ).all()
    return [(line, gl_id, name) for line, gl_id, name in rows]


def _resolve_paystack_main_gl(db: Session) -> uuid.UUID | None:
    """Find the GL account for the Paystack main bank account."""
    return db.scalar(
        select(BankAccount.gl_account_id).where(
            BankAccount.organization_id == ORG_ID,
            BankAccount.account_name == "Paystack",
        )
    )


def _bank_flag(name: str) -> str:
    """Return a short identifier for the bank account."""
    nl = name.lower()
    if "opex" in nl:
        return "psk_opex"
    if "paystack" in nl and "collection" not in nl:
        return "psk_main"
    if "uba" in nl and "usd" not in nl:
        return "uba"
    if "461" in name:
        return "z461"
    if "523" in name:
        return "z523"
    if "454" in name:
        return "z454"
    if "usd" in nl:
        return "z_usd"
    return "other"


def _categorise(lines: list[LineRow]) -> dict[str, list[LineRow]]:
    """Bucket every line into a processing category."""
    b: dict[str, list[LineRow]] = defaultdict(list)

    for row in lines:
        line, gl_id, acct_name = row
        desc = line.description or ""
        txn = line.transaction_type
        bank = _bank_flag(acct_name)

        # ── Paystack OPEX: Auto Top-Up credits ──
        if bank == "psk_opex":
            b["opex_in"].append(row)

        # ── Paystack main ──
        elif bank == "psk_main":
            if txn == StatementLineType.debit:
                b["psk_settle"].append(row)
            else:
                b["customer_ar"].append(row)

        # ── UBA ──
        elif bank == "uba":
            if txn == StatementLineType.credit and _PSK_REF_RE.search(desc):
                b["uba_psk"].append(row)
            elif txn == StatementLineType.credit:
                b["customer_ar"].append(row)
            else:
                b["vendor_payment"].append(row)

        # ── Zenith 461 ──
        elif bank == "z461":
            if txn == StatementLineType.credit:
                b["customer_ar"].append(row)
            else:
                b["vendor_payment"].append(row)

        # ── Zenith 523 ──
        elif bank == "z523":
            if txn == StatementLineType.debit:
                if _TOPUPEXP_RE.search(desc):
                    b["opex_out"].append(row)
                elif _NHF_RE.search(desc):
                    b["nhf"].append(row)
                elif _PAYROLL_RE.search(desc):
                    b["payroll"].append(row)
                elif _LOGISTICS_RE.search(desc):
                    b["logistics"].append(row)
                else:
                    b["vendor_payment"].append(row)
            else:
                if _DOTMAC_UBA_RE.search(desc):
                    b["uba_xfer"].append(row)
                else:
                    b["customer_ar"].append(row)

        # ── Zenith 454 ──
        elif bank == "z454":
            b["vendor_payment"].append(row)

        # ── Zenith USD ──
        elif bank == "z_usd":
            if txn == StatementLineType.debit:
                b["usd_charges"].append(row)
            else:
                b["usd_deposit"].append(row)

        else:
            b["skip"].append(row)

    return b


# ── Core journal helpers ────────────────────────────────────────────────────


def _find_bank_gl_line(
    db: Session,
    journal_entry_id: uuid.UUID,
    bank_gl_id: uuid.UUID,
    txn_type: str,
) -> JournalEntryLine | None:
    """Find the bank-side GL journal line for matching."""
    return db.scalar(
        select(JournalEntryLine).where(
            JournalEntryLine.journal_entry_id == journal_entry_id,
            JournalEntryLine.account_id == bank_gl_id,
            (
                JournalEntryLine.credit_amount > 0
                if txn_type == "debit"
                else JournalEntryLine.debit_amount > 0
            ),
        )
    )


def _create_and_match(
    db: Session,
    recon: BankReconciliationService,
    line: BankStatementLine,
    bank_gl_id: uuid.UUID,
    contra_gl_id: uuid.UUID,
    stats: Stats,
    phase: str,
    dry_run: bool,
) -> bool:
    """Create a contra journal for one bank statement line, post and match."""
    txn_type = line.transaction_type.value
    amount = line.amount
    cid = f"jan2026p2-{phase}-{line.line_id}"

    # ── idempotency ──
    existing = db.scalar(
        select(JournalEntry).where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.correlation_id == cid,
            JournalEntry.status == JournalStatus.POSTED,
        )
    )
    if existing:
        gl_line = _find_bank_gl_line(
            db, existing.journal_entry_id, bank_gl_id, txn_type
        )
        if gl_line and not dry_run:
            recon.match_statement_line(
                db=db,
                organization_id=ORG_ID,
                statement_line_id=line.line_id,
                journal_line_id=gl_line.line_id,
                matched_by=SYSTEM_USER,
                force_match=True,
            )
        if gl_line:
            stats.lines_matched += 1
            stats.by_phase[phase] += 1
            return True
        return False

    if dry_run:
        stats.journals_created += 1
        stats.lines_matched += 1
        stats.by_phase[phase] += 1
        return True

    desc = (line.description or "")[:60]
    if txn_type == "debit":
        jlines = [
            JournalLineInput(
                account_id=contra_gl_id, debit_amount=amount, description=desc
            ),
            JournalLineInput(
                account_id=bank_gl_id, credit_amount=amount, description=desc
            ),
        ]
    else:
        jlines = [
            JournalLineInput(
                account_id=bank_gl_id, debit_amount=amount, description=desc
            ),
            JournalLineInput(
                account_id=contra_gl_id, credit_amount=amount, description=desc
            ),
        ]

    ji = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=line.transaction_date,
        posting_date=line.transaction_date,
        description=f"Bank recon P2 — {desc}",
        reference=line.reference or "",
        source_module="BANKING",
        source_document_type="BANK_RECONCILIATION",
        correlation_id=cid,
        lines=jlines,
    )

    journal, err = BasePostingAdapter.create_and_approve_journal(
        db=db,
        organization_id=ORG_ID,
        journal_input=ji,
        posted_by_user_id=SYSTEM_USER,
        error_prefix=f"[{phase}] {line.line_id}",
    )
    if err:
        stats.errors.append(f"[{phase}] create {line.line_id}: {err.message}")
        return False

    idem_key = BasePostingAdapter.make_idempotency_key(
        ORG_ID,
        "BANKING",
        line.line_id,
        action=f"jan2026p2-{phase}",
    )
    result = BasePostingAdapter.post_to_ledger(
        db=db,
        organization_id=ORG_ID,
        journal_entry_id=journal.journal_entry_id,
        posting_date=line.transaction_date,
        idempotency_key=idem_key,
        source_module="BANKING",
        correlation_id=cid,
        posted_by_user_id=SYSTEM_USER,
        success_message="Posted",
        error_prefix=f"[{phase}] post",
    )
    if not result.success:
        stats.errors.append(f"[{phase}] post {line.line_id}: {result.message}")
        return False

    stats.journals_created += 1

    gl_line = _find_bank_gl_line(db, journal.journal_entry_id, bank_gl_id, txn_type)
    if gl_line:
        recon.match_statement_line(
            db=db,
            organization_id=ORG_ID,
            statement_line_id=line.line_id,
            journal_line_id=gl_line.line_id,
            matched_by=SYSTEM_USER,
            force_match=True,
        )
        stats.lines_matched += 1
        stats.by_phase[phase] += 1
        return True

    stats.errors.append(f"[{phase}] no GL line after post: {line.line_id}")
    return False


def _create_and_match_transfer(
    db: Session,
    recon: BankReconciliationService,
    src: BankStatementLine,
    dst: BankStatementLine,
    src_gl: uuid.UUID,
    dst_gl: uuid.UUID,
    stats: Stats,
    phase: str,
    dry_run: bool,
) -> bool:
    """Create a transfer journal and match BOTH bank statement lines."""
    amount = src.amount
    cid = f"jan2026p2-{phase}-{src.line_id}"
    desc = f"Inter-bank transfer {src.transaction_date}"

    existing = db.scalar(
        select(JournalEntry).where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.correlation_id == cid,
            JournalEntry.status == JournalStatus.POSTED,
        )
    )
    if existing:
        return _match_transfer_pair(
            db,
            recon,
            existing.journal_entry_id,
            src,
            dst,
            src_gl,
            dst_gl,
            stats,
            phase,
            dry_run,
        )

    if dry_run:
        stats.journals_created += 1
        stats.lines_matched += 2
        stats.pairs_matched += 1
        stats.by_phase[phase] += 2
        return True

    ji = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=src.transaction_date,
        posting_date=src.transaction_date,
        description=desc,
        reference=src.reference or "",
        source_module="BANKING",
        source_document_type="BANK_TRANSFER",
        correlation_id=cid,
        lines=[
            JournalLineInput(account_id=dst_gl, debit_amount=amount, description=desc),
            JournalLineInput(account_id=src_gl, credit_amount=amount, description=desc),
        ],
    )

    journal, err = BasePostingAdapter.create_and_approve_journal(
        db=db,
        organization_id=ORG_ID,
        journal_input=ji,
        posted_by_user_id=SYSTEM_USER,
        error_prefix=f"[{phase}] xfer",
    )
    if err:
        stats.errors.append(f"[{phase}] create xfer: {err.message}")
        return False

    idem_key = BasePostingAdapter.make_idempotency_key(
        ORG_ID,
        "BANKING",
        src.line_id,
        action=f"jan2026p2-{phase}",
    )
    result = BasePostingAdapter.post_to_ledger(
        db=db,
        organization_id=ORG_ID,
        journal_entry_id=journal.journal_entry_id,
        posting_date=src.transaction_date,
        idempotency_key=idem_key,
        source_module="BANKING",
        correlation_id=cid,
        posted_by_user_id=SYSTEM_USER,
        success_message="Posted",
        error_prefix=f"[{phase}] post xfer",
    )
    if not result.success:
        stats.errors.append(f"[{phase}] post xfer: {result.message}")
        return False

    stats.journals_created += 1
    return _match_transfer_pair(
        db,
        recon,
        journal.journal_entry_id,
        src,
        dst,
        src_gl,
        dst_gl,
        stats,
        phase,
        dry_run,
    )


def _match_transfer_pair(
    db: Session,
    recon: BankReconciliationService,
    je_id: uuid.UUID,
    src: BankStatementLine,
    dst: BankStatementLine,
    src_gl: uuid.UUID,
    dst_gl: uuid.UUID,
    stats: Stats,
    phase: str,
    dry_run: bool,
) -> bool:
    """Match both sides of a posted transfer journal."""
    matched = 0
    cr_line = db.scalar(
        select(JournalEntryLine).where(
            JournalEntryLine.journal_entry_id == je_id,
            JournalEntryLine.account_id == src_gl,
            JournalEntryLine.credit_amount > 0,
        )
    )
    dr_line = db.scalar(
        select(JournalEntryLine).where(
            JournalEntryLine.journal_entry_id == je_id,
            JournalEntryLine.account_id == dst_gl,
            JournalEntryLine.debit_amount > 0,
        )
    )
    if cr_line and not dry_run:
        recon.match_statement_line(
            db=db,
            organization_id=ORG_ID,
            statement_line_id=src.line_id,
            journal_line_id=cr_line.line_id,
            matched_by=SYSTEM_USER,
            force_match=True,
        )
        matched += 1
    elif cr_line:
        matched += 1
    if dr_line and not dry_run:
        recon.match_statement_line(
            db=db,
            organization_id=ORG_ID,
            statement_line_id=dst.line_id,
            journal_line_id=dr_line.line_id,
            matched_by=SYSTEM_USER,
            force_match=True,
        )
        matched += 1
    elif dr_line:
        matched += 1

    stats.lines_matched += matched
    stats.pairs_matched += 1
    stats.by_phase[phase] += matched
    return matched == 2


# ── Phase processors ────────────────────────────────────────────────────────


def _phase_opex_topup(
    db: Session,
    recon: BankReconciliationService,
    buckets: dict[str, list[LineRow]],
    stats: Stats,
    dry_run: bool,
) -> None:
    """Phase 1: Pair Paystack OPEX credits with Z523 Topupexp debits."""
    ins = buckets.get("opex_in", [])
    outs = buckets.get("opex_out", [])
    if not ins or not outs:
        logger.info("Phase OPEX_TOPUP: no pairs (%d in, %d out)", len(ins), len(outs))
        # Move unmatched to skip
        for row in ins:
            buckets.setdefault("skip", []).append(row)
        for row in outs:
            buckets.setdefault("skip", []).append(row)
        return

    # Index credits (OPEX) by (amount, date)
    in_idx: dict[tuple[Decimal, date], list[LineRow]] = defaultdict(list)
    for row in ins:
        in_idx[(row[0].amount, row[0].transaction_date)].append(row)

    matched_in_ids: set[uuid.UUID] = set()
    for out_row in outs:
        out_line, out_gl, _ = out_row
        key = (out_line.amount, out_line.transaction_date)
        candidates = in_idx.get(key, [])
        match_row = None
        for c in candidates:
            if c[0].line_id not in matched_in_ids:
                match_row = c
                break
        if not match_row:
            buckets.setdefault("skip", []).append(out_row)
            continue

        in_line, in_gl, _ = match_row
        matched_in_ids.add(in_line.line_id)

        logger.info(
            "  OPEX pair: %s NGN %s (Z523 → OPEX)",
            out_line.transaction_date,
            f"{out_line.amount:,.2f}",
        )
        _create_and_match_transfer(
            db,
            recon,
            out_line,
            in_line,
            out_gl,
            in_gl,
            stats,
            "opex_topup",
            dry_run,
        )

    for row in ins:
        if row[0].line_id not in matched_in_ids:
            buckets.setdefault("skip", []).append(row)


# ── Main ────────────────────────────────────────────────────────────────────


def run(args: argparse.Namespace) -> int:
    dry_run = args.dry_run
    mode = "DRY-RUN" if dry_run else "EXECUTE"
    stats = Stats()
    recon = BankReconciliationService()

    logger.info("=== Jan 2026 Phase 2 Reconciliation [%s] ===", mode)

    with SessionLocal() as db:
        assert isinstance(db, Session)

        # Resolve Paystack main GL account
        psk_main_gl = _resolve_paystack_main_gl(db)
        if psk_main_gl:
            ACCT["psk_main"] = psk_main_gl
            logger.info("Paystack main GL: %s", psk_main_gl)
        else:
            logger.error("Cannot resolve Paystack main GL — aborting")
            return 1

        # Load and categorise
        all_lines = _load_lines(db)
        logger.info("Loaded %d unmatched Jan 2026 lines", len(all_lines))
        if not all_lines:
            logger.info("Nothing to do")
            return 0

        buckets = _categorise(all_lines)

        # Log categories
        all_cats = [
            "opex_in",
            "opex_out",
            "customer_ar",
            "nhf",
            "payroll",
            "logistics",
            "psk_settle",
            "uba_psk",
            "uba_xfer",
            "vendor_payment",
            "usd_charges",
            "usd_deposit",
            "skip",
        ]
        for cat in all_cats:
            items = buckets.get(cat, [])
            if items:
                total = sum(r[0].amount for r in items)
                logger.info(
                    "  %-16s %4d lines  NGN %15s", cat, len(items), f"{total:,.2f}"
                )

        # ── Phase 1: OPEX ↔ Z523 Topupexp ──
        logger.info("\n── Phase 1: Paystack OPEX ↔ Z523 Topupexp ──")
        _phase_opex_topup(db, recon, buckets, stats, dry_run)
        db.flush()

        # ── Phase 2: Customer receipts → AR ──
        logger.info("\n── Phase 2: Customer receipts → AR ──")
        for row in buckets.get("customer_ar", []):
            line, gl_id, _ = row
            _create_and_match(
                db,
                recon,
                line,
                gl_id,
                ACCT["ar"],
                stats,
                "customer_ar",
                dry_run,
            )
        db.flush()

        # ── Phase 3: NHF payments → NHF Payables ──
        logger.info("\n── Phase 3: NHF payments → NHF Payables ──")
        for row in buckets.get("nhf", []):
            line, gl_id, _ = row
            _create_and_match(
                db,
                recon,
                line,
                gl_id,
                ACCT["nhf_payable"],
                stats,
                "nhf",
                dry_run,
            )
        db.flush()

        # ── Phase 4: Payroll & logistics → Expense Payable ──
        logger.info("\n── Phase 4: Payroll & logistics → Expense Payable ──")
        for row in buckets.get("payroll", []) + buckets.get("logistics", []):
            line, gl_id, _ = row
            _create_and_match(
                db,
                recon,
                line,
                gl_id,
                ACCT["expense_payable"],
                stats,
                "payroll_logistics",
                dry_run,
            )
        db.flush()

        # ── Phase 5: Paystack settlements → UBA GL ──
        logger.info("\n── Phase 5: Paystack settlements → UBA GL ──")
        for row in buckets.get("psk_settle", []):
            line, gl_id, _ = row
            _create_and_match(
                db,
                recon,
                line,
                gl_id,
                ACCT["uba"],
                stats,
                "psk_settle",
                dry_run,
            )
        db.flush()

        # ── Phase 6: UBA Paystack receipts → Paystack GL ──
        logger.info("\n── Phase 6: UBA Paystack receipts → Paystack GL ──")
        for row in buckets.get("uba_psk", []):
            line, gl_id, _ = row
            _create_and_match(
                db,
                recon,
                line,
                gl_id,
                ACCT["psk_main"],
                stats,
                "uba_psk",
                dry_run,
            )
        db.flush()

        # ── Phase 7: Z523 ← UBA internal transfers ──
        logger.info("\n── Phase 7: Z523 credits from UBA ──")
        for row in buckets.get("uba_xfer", []):
            line, gl_id, _ = row
            _create_and_match(
                db,
                recon,
                line,
                gl_id,
                ACCT["uba"],
                stats,
                "uba_xfer",
                dry_run,
            )
        db.flush()

        # ── Phase 8: Zenith USD charges → Finance Cost ──
        logger.info("\n── Phase 8: Zenith USD charges → Finance Cost ──")
        for row in buckets.get("usd_charges", []):
            line, gl_id, _ = row
            _create_and_match(
                db,
                recon,
                line,
                gl_id,
                ACCT["finance_cost"],
                stats,
                "usd_charges",
                dry_run,
            )
        # USD deposit → Expense Payable (single cash deposit)
        for row in buckets.get("usd_deposit", []):
            line, gl_id, _ = row
            _create_and_match(
                db,
                recon,
                line,
                gl_id,
                ACCT["expense_payable"],
                stats,
                "usd_deposit",
                dry_run,
            )
        db.flush()

        # ── Phase 9: Remaining vendor/expense payments → Expense Payable ──
        logger.info("\n── Phase 9: Vendor/expense payments → Expense Payable ──")
        for row in buckets.get("vendor_payment", []):
            line, gl_id, _ = row
            _create_and_match(
                db,
                recon,
                line,
                gl_id,
                ACCT["expense_payable"],
                stats,
                "vendor_payment",
                dry_run,
            )
        db.flush()

        # ── Report ──
        skipped = buckets.get("skip", [])
        logger.info("\n" + "=" * 70)
        logger.info("%sRESULTS:", "DRY-RUN — " if dry_run else "")
        logger.info("  Journals created:  %d", stats.journals_created)
        logger.info("  Lines matched:     %d", stats.lines_matched)
        logger.info("  Transfer pairs:    %d", stats.pairs_matched)
        logger.info("  Skipped:           %d", len(skipped))
        logger.info("  Errors:            %d", len(stats.errors))

        logger.info("\n  By phase:")
        for phase, count in sorted(stats.by_phase.items()):
            logger.info("    %-20s %d", phase, count)

        if skipped:
            logger.info("\n  Remaining unmatched (%d lines):", len(skipped))
            by_acct: dict[str, list[LineRow]] = defaultdict(list)
            for row in skipped:
                by_acct[row[2]].append(row)
            for acct, rows in sorted(by_acct.items()):
                total = sum(r[0].amount for r in rows)
                logger.info(
                    "    %s: %d lines, NGN %s", acct, len(rows), f"{total:,.2f}"
                )
                for r in rows[:3]:
                    logger.info(
                        "      %s %s %s %s",
                        r[0].transaction_date,
                        r[0].transaction_type.value,
                        f"{r[0].amount:>12,.2f}",
                        (r[0].description or "")[:50],
                    )
                if len(rows) > 3:
                    logger.info("      … and %d more", len(rows) - 3)

        for e in stats.errors:
            logger.warning("  ERR: %s", e)

        if dry_run:
            logger.info("\nDRY-RUN — rolling back")
            db.rollback()
        else:
            db.commit()
            logger.info("\nEXECUTE — committed")

    remaining = len(all_lines) - stats.lines_matched
    logger.info("Done. Remaining unmatched: %d", remaining)
    return 0 if remaining == 0 else 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    p = argparse.ArgumentParser(description="Match remaining Jan 2026 lines (Phase 2)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--execute", action="store_true")
    raise SystemExit(run(p.parse_args()))
