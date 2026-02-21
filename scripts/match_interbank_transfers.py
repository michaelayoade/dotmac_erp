"""
Match inter-bank transfers between UBA 96 and Zenith 523.

Finds UBA debits (transfers OUT) and corresponding Zenith 523 credits (transfers IN),
creates balanced GL journals (DR Zenith Bank + DR Finance Cost / CR UBA), and marks
both bank statement lines as matched.

Usage:
    python scripts/match_interbank_transfers.py --dry-run     # Preview matches
    python scripts/match_interbank_transfers.py --execute      # Execute matching
    python scripts/match_interbank_transfers.py --execute --max 10  # Limit to first 10
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

# ---------------------------------------------------------------------------
# Bootstrap Django-style: add project root to path, import app DB
# ---------------------------------------------------------------------------
sys.path.insert(0, "/root/dotmac")
sys.path.insert(0, "/app")

from app.db import SessionLocal  # noqa: E402
from app.models.finance.banking.bank_account import BankAccount  # noqa: E402
from app.models.finance.banking.bank_statement import (  # noqa: E402
    BankStatement,
    BankStatementLine,
    StatementLineType,
)
from app.models.finance.gl.account import Account  # noqa: E402
from app.models.finance.gl.journal_entry import (  # noqa: E402
    JournalEntry,
    JournalStatus,
    JournalType,
)
from app.models.finance.gl.journal_entry_line import JournalEntryLine  # noqa: E402
from app.services.finance.gl.journal import (  # noqa: E402
    JournalInput,
    JournalLineInput,
)
from app.services.finance.posting.base import BasePostingAdapter  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("match_interbank")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")

# GL account codes
ZENITH_BANK_GL_CODE = "1200"  # Zenith Bank (all Zenith accounts share this)
UBA_GL_CODE = "1202"  # UBA
FINANCE_COST_GL_CODE = "6080"  # Finance Cost (bank fees)

# Matching parameters
DATE_WINDOW_DAYS = 3  # Max days between UBA debit and Zenith credit
MAX_FEE = Decimal("200")  # Max acceptable fee (UBA amount - Zenith amount)
MIN_FEE = Decimal("0")  # Min acceptable fee (could be zero for exact matches)


@dataclass
class TransferPair:
    """A matched inter-bank transfer pair."""

    uba_line: BankStatementLine
    zenith_line: BankStatementLine
    transfer_amount: Decimal  # Amount received at Zenith
    fee_amount: Decimal  # UBA amount - Zenith amount
    date_diff_days: int  # Absolute days between transactions


def find_bank_accounts(db: Session) -> tuple[BankAccount, BankAccount]:
    """Find the UBA 96 and Zenith 523 bank accounts."""
    uba = db.scalar(
        select(BankAccount).where(
            BankAccount.organization_id == ORG_ID,
            BankAccount.account_name.ilike("%UBA%"),
            BankAccount.currency_code == "NGN",
            BankAccount.account_number == "1018904696",
        )
    )
    zenith = db.scalar(
        select(BankAccount).where(
            BankAccount.organization_id == ORG_ID,
            BankAccount.account_name.ilike("%523%"),
            BankAccount.account_number == "1011649523",
        )
    )
    if not uba:
        raise RuntimeError("UBA 96 bank account not found")
    if not zenith:
        raise RuntimeError("Zenith 523 bank account not found")
    return uba, zenith


def find_gl_accounts(db: Session) -> tuple[Account, Account, Account]:
    """Find the GL accounts for Zenith, UBA, and Finance Cost."""
    zenith_gl = db.scalar(
        select(Account).where(
            Account.organization_id == ORG_ID,
            Account.account_code == ZENITH_BANK_GL_CODE,
        )
    )
    uba_gl = db.scalar(
        select(Account).where(
            Account.organization_id == ORG_ID,
            Account.account_code == UBA_GL_CODE,
        )
    )
    finance_cost_gl = db.scalar(
        select(Account).where(
            Account.organization_id == ORG_ID,
            Account.account_code == FINANCE_COST_GL_CODE,
        )
    )
    if not zenith_gl or not uba_gl or not finance_cost_gl:
        missing = []
        if not zenith_gl:
            missing.append(ZENITH_BANK_GL_CODE)
        if not uba_gl:
            missing.append(UBA_GL_CODE)
        if not finance_cost_gl:
            missing.append(FINANCE_COST_GL_CODE)
        raise RuntimeError(f"GL accounts not found: {', '.join(missing)}")
    return zenith_gl, uba_gl, finance_cost_gl


def get_uba_transfer_debits(
    db: Session, uba_account: BankAccount
) -> list[BankStatementLine]:
    """Get unmatched UBA debits that are transfers to Zenith 523 / other DotMac accounts.

    Patterns:
    - CIB/UTO//DOTMAC TECHNOLOGIES L...  (Corporate Internet Banking transfers)
    - CIB/UTU/... (alternate prefix)
    - IBG/UTO/... (inter-bank guarantee transfers)
    - TUD  CIB/UTO/... (with prefix)
    """
    stmt = (
        select(BankStatementLine)
        .join(
            BankStatement, BankStatementLine.statement_id == BankStatement.statement_id
        )
        .where(
            BankStatement.bank_account_id == uba_account.bank_account_id,
            BankStatementLine.is_matched.is_(False),
            BankStatementLine.transaction_type == StatementLineType.debit,
        )
    )
    all_debits = list(db.scalars(stmt).all())

    # Filter to transfers destined for Zenith 523 / DotMac accounts
    transfer_debits = []
    for line in all_debits:
        desc = (line.description or "").upper()
        # CIB/UTO patterns transferring to DOTMAC or to 523
        if ("CIB/UTO" in desc or "CIB/UTU" in desc or "IBG/UTO" in desc) and (
            "DOTMAC" in desc or "523" in desc
        ):
            transfer_debits.append(line)

    return transfer_debits


def get_zenith_transfer_credits(
    db: Session, zenith_account: BankAccount
) -> list[BankStatementLine]:
    """Get unmatched Zenith 523 credits that came from UBA (NIP transfers).

    Patterns:
    - NIP/UBA/DOTMAC TECHNOLOGIES LIMITED/CIB/UTO/...
    """
    stmt = (
        select(BankStatementLine)
        .join(
            BankStatement, BankStatementLine.statement_id == BankStatement.statement_id
        )
        .where(
            BankStatement.bank_account_id == zenith_account.bank_account_id,
            BankStatementLine.is_matched.is_(False),
            BankStatementLine.transaction_type == StatementLineType.credit,
        )
    )
    all_credits = list(db.scalars(stmt).all())

    # Filter to NIP transfers from UBA
    transfer_credits = []
    for line in all_credits:
        desc = (line.description or "").upper()
        if "NIP/UBA/DOTMAC" in desc or "NIP/UBA/ DOTMAC" in desc:
            transfer_credits.append(line)

    return transfer_credits


def find_matching_pairs(
    uba_debits: list[BankStatementLine],
    zenith_credits: list[BankStatementLine],
) -> list[TransferPair]:
    """Match UBA debits to Zenith credits by amount (within fee tolerance) and date.

    Algorithm:
    - For each UBA debit, find Zenith credits where:
      - Zenith amount ≤ UBA amount (UBA pays the fee)
      - Fee = UBA amount - Zenith amount is between MIN_FEE and MAX_FEE
      - Transaction dates are within DATE_WINDOW_DAYS of each other
    - Greedy matching: best match = closest amount, then closest date
    - Each line can only be matched once
    """
    pairs: list[TransferPair] = []
    used_zenith_ids: set[UUID] = set()
    used_uba_ids: set[UUID] = set()

    # Sort UBA debits by amount desc (match largest first for stability)
    uba_sorted = sorted(uba_debits, key=lambda x: x.amount, reverse=True)

    for uba_line in uba_sorted:
        if uba_line.line_id in used_uba_ids:
            continue

        best_match: BankStatementLine | None = None
        best_fee = Decimal("999999999")
        best_date_diff = 999

        for zenith_line in zenith_credits:
            if zenith_line.line_id in used_zenith_ids:
                continue

            # Fee calculation: UBA sent more, Zenith received less
            fee = uba_line.amount - zenith_line.amount
            if fee < MIN_FEE or fee > MAX_FEE:
                continue

            # Date proximity
            date_diff = abs(
                (uba_line.transaction_date - zenith_line.transaction_date).days
            )
            if date_diff > DATE_WINDOW_DAYS:
                continue

            # Best match: smallest fee first, then closest date
            if fee < best_fee or (fee == best_fee and date_diff < best_date_diff):
                best_match = zenith_line
                best_fee = fee
                best_date_diff = date_diff

        if best_match is not None:
            pairs.append(
                TransferPair(
                    uba_line=uba_line,
                    zenith_line=best_match,
                    transfer_amount=best_match.amount,
                    fee_amount=best_fee,
                    date_diff_days=best_date_diff,
                )
            )
            used_zenith_ids.add(best_match.line_id)
            used_uba_ids.add(uba_line.line_id)

    return pairs


def check_existing_journal(db: Session, correlation_id: str) -> JournalEntry | None:
    """Check if a journal already exists for this transfer (idempotency)."""
    return db.scalar(
        select(JournalEntry).where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.correlation_id == correlation_id,
            JournalEntry.status == JournalStatus.POSTED,
        )
    )


def create_and_post_journal(
    db: Session,
    pair: TransferPair,
    zenith_gl: Account,
    uba_gl: Account,
    finance_cost_gl: Account,
) -> JournalEntry | None:
    """Create, approve, and post a balanced inter-bank transfer journal.

    Journal:
        DR 1200 Zenith Bank         <transfer_amount>
        DR 6080 Finance Cost         <fee_amount>       (if fee > 0)
        CR 1202 UBA                  <uba_total_amount>
    """
    correlation_id = f"interbank-uba-zen523-{pair.uba_line.line_id}"

    # Idempotency: skip if already posted
    existing = check_existing_journal(db, correlation_id)
    if existing:
        logger.info(
            "Journal already exists for transfer %s: %s",
            correlation_id,
            existing.journal_number,
        )
        return existing

    uba_desc = (pair.uba_line.description or "")[:200]
    entry_date = pair.zenith_line.transaction_date  # Use the receiving date

    lines = [
        JournalLineInput(
            account_id=zenith_gl.account_id,
            debit_amount=pair.transfer_amount,
            description=f"Inter-bank transfer from UBA: {uba_desc}",
        ),
    ]

    if pair.fee_amount > 0:
        lines.append(
            JournalLineInput(
                account_id=finance_cost_gl.account_id,
                debit_amount=pair.fee_amount,
                description=f"UBA transfer fee: {uba_desc}",
            ),
        )

    lines.append(
        JournalLineInput(
            account_id=uba_gl.account_id,
            credit_amount=pair.uba_line.amount,
            description=f"Inter-bank transfer to Zenith 523: {uba_desc}",
        ),
    )

    journal_input = JournalInput(
        journal_type=JournalType.STANDARD,
        entry_date=entry_date,
        posting_date=entry_date,
        description=f"Inter-bank transfer UBA→Zenith 523: NGN {pair.transfer_amount:,.2f}",
        lines=lines,
        reference=f"IBT-UBA-ZEN523-{entry_date.isoformat()}",
        source_module="BANKING",
        source_document_type="INTERBANK_TRANSFER",
        correlation_id=correlation_id,
    )

    # Step 1: Create, submit, approve
    journal, create_error = BasePostingAdapter.create_and_approve_journal(
        db,
        ORG_ID,
        journal_input,
        SYSTEM_USER_ID,
        error_prefix="Interbank journal creation failed",
    )

    if create_error:
        logger.error(
            "Failed to create journal for pair UBA %s → Zenith %s: %s",
            pair.uba_line.line_id,
            pair.zenith_line.line_id,
            create_error.message,
        )
        return None

    # Step 2: Post to ledger
    idempotency_key = BasePostingAdapter.make_idempotency_key(
        ORG_ID, "BANKING", pair.uba_line.line_id, action="interbank-transfer"
    )
    posting_result = BasePostingAdapter.post_to_ledger(
        db,
        organization_id=ORG_ID,
        journal_entry_id=journal.journal_entry_id,
        posting_date=entry_date,
        idempotency_key=idempotency_key,
        source_module="BANKING",
        correlation_id=correlation_id,
        posted_by_user_id=SYSTEM_USER_ID,
        success_message="Interbank transfer posted",
        error_prefix="Interbank journal posting failed",
    )

    if not posting_result.success:
        logger.error(
            "Failed to post journal for pair UBA %s → Zenith %s: %s",
            pair.uba_line.line_id,
            pair.zenith_line.line_id,
            posting_result.message,
        )
        return None

    logger.info(
        "Created and posted journal %s for NGN %s transfer on %s",
        journal.journal_number,
        f"{pair.transfer_amount:,.2f}",
        entry_date,
    )
    return journal


def find_journal_gl_line(
    db: Session, correlation_id: str, gl_account_id: UUID
) -> JournalEntryLine | None:
    """Find the GL journal line for a specific account in a posted journal."""
    stmt = (
        select(JournalEntry)
        .options(joinedload(JournalEntry.lines))
        .where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.correlation_id == correlation_id,
            JournalEntry.status == JournalStatus.POSTED,
        )
    )
    journal = db.execute(stmt).unique().scalar_one_or_none()
    if not journal:
        return None

    for jl in journal.lines:
        if jl.account_id == gl_account_id:
            return jl

    return None


def match_bank_line(
    db: Session,
    line: BankStatementLine,
    journal_line: JournalEntryLine,
) -> bool:
    """Mark a bank statement line as matched to a GL journal line."""
    from app.services.finance.banking.bank_reconciliation import (
        BankReconciliationService,
    )

    try:
        recon_svc = BankReconciliationService()
        recon_svc.match_statement_line(
            db=db,
            organization_id=ORG_ID,
            statement_line_id=line.line_id,
            journal_line_id=journal_line.line_id,
            matched_by=None,
            force_match=True,
        )
        return True
    except Exception as e:
        logger.error("Failed to match line %s: %s", line.line_id, e)
        return False


def run(*, execute: bool = False, max_pairs: int | None = None) -> None:
    """Main execution flow."""
    with SessionLocal() as db:
        # Step 1: Find bank accounts
        logger.info("=" * 70)
        logger.info("INTER-BANK TRANSFER MATCHING: UBA 96 → Zenith 523")
        logger.info("Mode: %s", "EXECUTE" if execute else "DRY RUN")
        logger.info("=" * 70)

        uba_account, zenith_account = find_bank_accounts(db)
        logger.info(
            "UBA account: %s (%s)",
            uba_account.account_name,
            uba_account.bank_account_id,
        )
        logger.info(
            "Zenith account: %s (%s)",
            zenith_account.account_name,
            zenith_account.bank_account_id,
        )

        zenith_gl, uba_gl, finance_cost_gl = find_gl_accounts(db)
        logger.info("Zenith GL: %s %s", zenith_gl.account_code, zenith_gl.account_name)
        logger.info("UBA GL: %s %s", uba_gl.account_code, uba_gl.account_name)
        logger.info(
            "Finance Cost GL: %s %s",
            finance_cost_gl.account_code,
            finance_cost_gl.account_name,
        )

        # Step 2: Get candidate lines
        logger.info("-" * 70)
        uba_debits = get_uba_transfer_debits(db, uba_account)
        zenith_credits = get_zenith_transfer_credits(db, zenith_account)
        logger.info("UBA transfer debits (unmatched): %d", len(uba_debits))
        logger.info("Zenith 523 NIP/UBA credits (unmatched): %d", len(zenith_credits))

        # Step 3: Find matching pairs
        pairs = find_matching_pairs(uba_debits, zenith_credits)
        if max_pairs:
            pairs = pairs[:max_pairs]
        logger.info("Matched pairs found: %d", len(pairs))

        if not pairs:
            logger.info("No matching pairs found. Nothing to do.")
            return

        # Step 4: Display matches
        logger.info("-" * 70)
        logger.info(
            "%-4s  %-12s  %-16s  %-12s  %-16s  %-10s  %-6s  %-4s",
            "#",
            "UBA Date",
            "UBA Amount",
            "ZEN Date",
            "ZEN Amount",
            "Fee",
            "Fee%",
            "Days",
        )
        logger.info("-" * 70)

        total_transfer = Decimal("0")
        total_fees = Decimal("0")

        for i, pair in enumerate(pairs, 1):
            fee_pct = (
                (pair.fee_amount / pair.uba_line.amount * 100)
                if pair.uba_line.amount
                else Decimal("0")
            )
            logger.info(
                "%-4d  %-12s  %16s  %-12s  %16s  %10s  %5.2f%%  %4d",
                i,
                pair.uba_line.transaction_date,
                f"NGN {pair.uba_line.amount:,.2f}",
                pair.zenith_line.transaction_date,
                f"NGN {pair.zenith_line.amount:,.2f}",
                f"NGN {pair.fee_amount:,.2f}",
                fee_pct,
                pair.date_diff_days,
            )
            total_transfer += pair.transfer_amount
            total_fees += pair.fee_amount

        logger.info("-" * 70)
        logger.info(
            "TOTAL: %d pairs | Transfer: NGN %s | Fees: NGN %s",
            len(pairs),
            f"{total_transfer:,.2f}",
            f"{total_fees:,.2f}",
        )

        if not execute:
            logger.info("")
            logger.info(
                "DRY RUN complete. Re-run with --execute to create journals and match lines."
            )
            return

        # Step 5: Execute - create journals and match lines
        logger.info("")
        logger.info("=" * 70)
        logger.info("EXECUTING: Creating journals and matching bank lines...")
        logger.info("=" * 70)

        matched_count = 0
        error_count = 0

        for i, pair in enumerate(pairs, 1):
            try:
                correlation_id = f"interbank-uba-zen523-{pair.uba_line.line_id}"

                # Create and post journal
                journal = create_and_post_journal(
                    db, pair, zenith_gl, uba_gl, finance_cost_gl
                )
                if not journal:
                    error_count += 1
                    continue

                # Match Zenith 523 credit → debit GL line (DR 1200 Zenith Bank)
                zenith_gl_line = find_journal_gl_line(
                    db, correlation_id, zenith_gl.account_id
                )
                if zenith_gl_line:
                    match_bank_line(db, pair.zenith_line, zenith_gl_line)
                else:
                    logger.warning(
                        "Could not find Zenith GL line for %s", correlation_id
                    )

                # Match UBA debit → credit GL line (CR 1202 UBA)
                uba_gl_line = find_journal_gl_line(
                    db, correlation_id, uba_gl.account_id
                )
                if uba_gl_line:
                    match_bank_line(db, pair.uba_line, uba_gl_line)
                else:
                    logger.warning("Could not find UBA GL line for %s", correlation_id)

                matched_count += 1

                if matched_count % 10 == 0:
                    db.commit()
                    logger.info(
                        "Progress: %d/%d matched, %d errors",
                        matched_count,
                        len(pairs),
                        error_count,
                    )

            except Exception as e:
                logger.exception("Error processing pair %d: %s", i, e)
                error_count += 1
                db.rollback()

        # Final commit
        db.commit()

        logger.info("=" * 70)
        logger.info(
            "COMPLETE: %d matched, %d errors out of %d pairs",
            matched_count,
            error_count,
            len(pairs),
        )
        logger.info("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Match inter-bank transfers between UBA 96 and Zenith 523"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run", action="store_true", help="Preview matches without making changes"
    )
    group.add_argument(
        "--execute", action="store_true", help="Create journals and match lines"
    )
    parser.add_argument(
        "--max", type=int, default=None, help="Maximum number of pairs to process"
    )
    args = parser.parse_args()

    run(execute=args.execute, max_pairs=args.max)


if __name__ == "__main__":
    main()
