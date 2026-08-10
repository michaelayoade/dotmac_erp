"""
Finish inter-bank transfer matching: post APPROVED journals + match all bank lines.

Handles the remaining work after the initial match_interbank_transfers.py run:
1. Posts the 20 APPROVED journals (created but not posted due to soft-closed periods)
2. Matches bank lines for all 78 POSTED journals (44 still unmatched)

Usage:
    python scripts/finish_interbank_matching.py --dry-run
    python scripts/finish_interbank_matching.py --execute
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta  # noqa: F811
from uuid import UUID

sys.path.insert(0, "/app")
sys.path.insert(0, "/root/dotmac")

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import SessionLocal
from app.models.finance.banking.bank_statement import (
    BankStatementLine,
    BankStatementLineMatch,
)
from app.models.finance.gl.account import Account
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.finance.posting.base import BasePostingAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("finish_interbank")

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
SYSTEM_USER_ID = UUID("00000000-0000-0000-0000-000000000000")
CORRELATION_PREFIX = "interbank-uba-zen523-"


def get_interbank_journals(db: Session) -> list[JournalEntry]:
    """Get all interbank transfer journals."""
    stmt = (
        select(JournalEntry)
        .options(joinedload(JournalEntry.lines))
        .where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.correlation_id.like(f"{CORRELATION_PREFIX}%"),
            JournalEntry.status.in_([JournalStatus.APPROVED, JournalStatus.POSTED]),
        )
        .order_by(JournalEntry.posting_date)
    )
    return list(db.execute(stmt).unique().scalars().all())


def get_gl_accounts(db: Session) -> dict[str, Account]:
    """Get the GL accounts needed for matching."""
    accts: dict[str, Account] = {}
    for code in ("1200", "1202", "6080"):
        acct = db.scalar(
            select(Account).where(
                Account.organization_id == ORG_ID,
                Account.account_code == code,
            )
        )
        if acct:
            accts[code] = acct
    return accts


def get_bank_line_for_journal(
    db: Session, journal: JournalEntry, gl_account_id: UUID
) -> JournalEntryLine | None:
    """Find the journal line for a specific GL account."""
    for jl in journal.lines:
        if jl.account_id == gl_account_id:
            return jl
    return None


def is_line_already_matched(db: Session, journal_line_id: UUID) -> bool:
    """Check if a journal line already has a bank match."""
    return (
        db.scalar(
            select(BankStatementLineMatch.match_id).where(
                BankStatementLineMatch.journal_line_id == journal_line_id,
            )
        )
        is not None
    )


def find_bank_line_by_correlation(
    db: Session, correlation_id: str, bank_account_id: UUID
) -> BankStatementLine | None:
    """Find the bank statement line for a journal by correlation.

    The correlation_id contains the UBA line_id. For Zenith, we need
    to find the corresponding credit line by looking up via the journal amounts.
    """
    # Extract the UBA line_id from correlation_id
    uba_line_id_str = correlation_id.replace(CORRELATION_PREFIX, "")
    try:
        uba_line_id = UUID(uba_line_id_str)
    except ValueError:
        return None

    return db.get(BankStatementLine, uba_line_id)


def match_bank_line(
    db: Session,
    statement_line: BankStatementLine,
    journal_line: JournalEntryLine,
) -> bool:
    """Match a bank statement line to a GL journal line."""
    from app.services.finance.banking.bank_reconciliation import (
        BankReconciliationService,
    )

    try:
        recon_svc = BankReconciliationService()
        recon_svc.match_statement_line(
            db=db,
            organization_id=ORG_ID,
            statement_line_id=statement_line.line_id,
            journal_line_id=journal_line.line_id,
            matched_by=None,
            force_match=True,
        )
        return True
    except Exception as e:
        logger.error("Failed to match line %s: %s", statement_line.line_id, e)
        return False


def run(*, execute: bool = False) -> None:
    """Main execution flow."""
    with SessionLocal() as db:
        logger.info("=" * 70)
        logger.info("FINISH INTER-BANK MATCHING")
        logger.info("Mode: %s", "EXECUTE" if execute else "DRY RUN")
        logger.info("=" * 70)

        # Get all interbank journals
        journals = get_interbank_journals(db)
        approved = [j for j in journals if j.status == JournalStatus.APPROVED]
        posted = [j for j in journals if j.status == JournalStatus.POSTED]
        logger.info("Total interbank journals: %d", len(journals))
        logger.info("  APPROVED (need posting): %d", len(approved))
        logger.info("  POSTED (check matching): %d", len(posted))

        # Get GL accounts
        gl_accounts = get_gl_accounts(db)
        zenith_gl = gl_accounts.get("1200")
        uba_gl = gl_accounts.get("1202")
        if not zenith_gl or not uba_gl:
            logger.error("Missing GL accounts 1200 or 1202")
            return

        # Phase 1: Post APPROVED journals
        post_success = 0
        post_error = 0

        logger.info("")
        logger.info("-" * 70)
        logger.info("PHASE 1: Posting %d APPROVED journals...", len(approved))
        logger.info("-" * 70)

        for je in approved:
            logger.info(
                "  %s | %s | NGN %s",
                je.journal_number,
                je.posting_date,
                f"{sum(l.debit_amount for l in je.lines):,.2f}",
            )
            if execute:
                try:
                    idempotency_key = f"interbank-finish-{je.journal_entry_id}"
                    result = BasePostingAdapter.post_to_ledger(
                        db,
                        organization_id=ORG_ID,
                        journal_entry_id=je.journal_entry_id,
                        posting_date=je.posting_date,
                        idempotency_key=idempotency_key,
                        source_module="BANKING",
                        correlation_id=je.correlation_id,
                        posted_by_user_id=SYSTEM_USER_ID,
                        success_message="Interbank transfer posted (finish)",
                        error_prefix="Interbank posting failed",
                    )
                    if result.success:
                        post_success += 1
                        logger.info("    -> POSTED: %s", je.journal_number)
                    else:
                        post_error += 1
                        logger.error("    -> FAILED: %s", result.message)
                except Exception as e:
                    post_error += 1
                    logger.exception("    -> ERROR: %s", e)

        if execute:
            db.commit()
            logger.info(
                "Phase 1 complete: %d posted, %d errors", post_success, post_error
            )

        # Refresh journals after posting
        if execute:
            journals = get_interbank_journals(db)
            posted = [j for j in journals if j.status == JournalStatus.POSTED]

        # Phase 2: Match bank lines for all POSTED journals
        logger.info("")
        logger.info("-" * 70)
        logger.info(
            "PHASE 2: Matching bank lines for %d POSTED journals...", len(posted)
        )
        logger.info("-" * 70)

        match_success = 0
        match_skipped = 0
        match_error = 0

        for je in posted:
            corr_id = je.correlation_id or ""
            uba_line_id_str = corr_id.replace(CORRELATION_PREFIX, "")

            # Find Zenith GL line (DR 1200) and UBA GL line (CR 1202)
            zenith_jl = get_bank_line_for_journal(db, je, zenith_gl.account_id)
            uba_jl = get_bank_line_for_journal(db, je, uba_gl.account_id)

            if not zenith_jl or not uba_jl:
                logger.warning(
                    "  %s: Missing GL lines (zenith=%s, uba=%s)",
                    je.journal_number,
                    bool(zenith_jl),
                    bool(uba_jl),
                )
                match_error += 1
                continue

            # Check existing matches
            zenith_matched = is_line_already_matched(db, zenith_jl.line_id)
            uba_matched = is_line_already_matched(db, uba_jl.line_id)

            if zenith_matched and uba_matched:
                match_skipped += 1
                continue

            # Find the bank statement lines
            # UBA line_id is embedded in the correlation_id
            try:
                uba_bank_line_id = UUID(uba_line_id_str)
            except ValueError:
                logger.warning("  %s: Invalid correlation_id", je.journal_number)
                match_error += 1
                continue

            uba_bank_line = db.get(BankStatementLine, uba_bank_line_id)
            if not uba_bank_line:
                logger.warning(
                    "  %s: UBA bank line not found: %s",
                    je.journal_number,
                    uba_bank_line_id,
                )
                match_error += 1
                continue

            # For Zenith line: find by amount and date proximity
            # The Zenith credit = UBA debit minus fee
            zenith_credit_amount = sum(
                l.debit_amount for l in je.lines if l.account_id == zenith_gl.account_id
            )

            if not zenith_matched:
                # Find the Zenith bank statement line
                zenith_stmt = (
                    select(BankStatementLine)
                    .join(
                        BankStatementLine.statement,
                    )
                    .where(
                        BankStatementLine.is_matched.is_(False),
                        BankStatementLine.amount == zenith_credit_amount,
                        BankStatementLine.transaction_date.between(
                            je.posting_date - timedelta(days=3),
                            je.posting_date + timedelta(days=3),
                        ),
                    )
                )
                zenith_bank_line = db.scalar(zenith_stmt)

                if not zenith_bank_line:
                    # Try matched Zenith lines too (may have been matched by first run)
                    zenith_stmt_any = select(BankStatementLine).where(
                        BankStatementLine.amount == zenith_credit_amount,
                        BankStatementLine.transaction_date.between(
                            je.posting_date - timedelta(days=3),
                            je.posting_date + timedelta(days=3),
                        ),
                    )
                    zenith_bank_line = db.scalar(zenith_stmt_any)

                if zenith_bank_line and not zenith_matched:
                    logger.info(
                        "  %s: Match Zenith line %s (NGN %s, %s)",
                        je.journal_number,
                        zenith_bank_line.line_id,
                        f"{zenith_bank_line.amount:,.2f}",
                        zenith_bank_line.transaction_date,
                    )
                    if execute:
                        match_bank_line(db, zenith_bank_line, zenith_jl)
                else:
                    logger.warning(
                        "  %s: Zenith bank line not found for NGN %s near %s",
                        je.journal_number,
                        f"{zenith_credit_amount:,.2f}",
                        je.posting_date,
                    )

            if not uba_matched:
                logger.info(
                    "  %s: Match UBA line %s (NGN %s, %s)",
                    je.journal_number,
                    uba_bank_line.line_id,
                    f"{uba_bank_line.amount:,.2f}",
                    uba_bank_line.transaction_date,
                )
                if execute:
                    match_bank_line(db, uba_bank_line, uba_jl)

            match_success += 1

            if execute and match_success % 10 == 0:
                db.commit()

        if execute:
            db.commit()

        logger.info("")
        logger.info("=" * 70)
        logger.info(
            "COMPLETE: Phase 1: %d posted, %d errors | "
            "Phase 2: %d matched, %d skipped, %d errors",
            post_success if execute else len(approved),
            post_error,
            match_success,
            match_skipped,
            match_error,
        )
        logger.info("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="Finish inter-bank transfer matching")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Preview without changes")
    group.add_argument("--execute", action="store_true", help="Execute changes")
    args = parser.parse_args()
    run(execute=args.execute)


if __name__ == "__main__":
    main()
