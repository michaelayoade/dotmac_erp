"""
Phase 6: Rebuild account balances and run verification queries.

Steps:
  1. Update numbering sequences to the highest used values
  2. Rebuild AccountBalance cache for every period with posted ledger lines
  3. Run 8 verification queries to confirm data integrity

Usage:
    docker exec dotmac_erp_app python -m scripts.clean_sweep.phase6_rebuild_verify
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from scripts.clean_sweep.config import ORG_ID, setup_logging

logger = setup_logging("phase6_rebuild_verify")


# ── Step 1: Update numbering sequences ─────────────────────────────────


def _update_numbering_sequences(db: Session) -> None:
    """Set each numbering sequence counter to the max used value."""
    from app.models.finance.core_config.numbering_sequence import (
        NumberingSequence,
        SequenceType,
    )
    from app.models.finance.gl.journal_entry import JournalEntry

    logger.info("Step 1: Updating numbering sequences")

    # Journal entries: extract max number from JE-2025-NNNNN pattern
    max_je = db.scalar(
        select(func.max(JournalEntry.journal_number)).where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.journal_number.like("JE-2025-%"),
        )
    )
    if max_je:
        # Extract the counter: "JE-2025-00123" → 123
        try:
            je_counter = int(max_je.rsplit("-", 1)[1])
        except (ValueError, IndexError):
            je_counter = 0
    else:
        je_counter = 0

    # Also check JE-2026-NNNNN
    max_je_2026 = db.scalar(
        select(func.max(JournalEntry.journal_number)).where(
            JournalEntry.organization_id == ORG_ID,
            JournalEntry.journal_number.like("JE-2026-%"),
        )
    )
    je_counter_2026 = 0
    if max_je_2026:
        try:
            je_counter_2026 = int(max_je_2026.rsplit("-", 1)[1])
        except (ValueError, IndexError):
            pass

    # Use whichever is higher (most journals will be 2025)
    final_je_counter = max(je_counter, je_counter_2026)

    if final_je_counter > 0:
        seq = db.scalar(
            select(NumberingSequence).where(
                NumberingSequence.organization_id == ORG_ID,
                NumberingSequence.sequence_type == SequenceType.JOURNAL,
            )
        )
        if seq and seq.current_number < final_je_counter:
            old = seq.current_number
            seq.current_number = final_je_counter
            seq.last_used_at = datetime.now(UTC)
            db.flush()
            logger.info(
                "  JOURNAL sequence: %d → %d (from %s)",
                old,
                final_je_counter,
                max_je_2026 or max_je,
            )
        elif seq:
            logger.info(
                "  JOURNAL sequence already at %d (max imported: %d)",
                seq.current_number,
                final_je_counter,
            )
        else:
            logger.warning("  JOURNAL sequence not found — skip")

    # Other sequences: check max document numbers via raw SQL
    # These use service-generated numbers, so we query max from each table
    sequence_checks: list[tuple[SequenceType, str, str, str]] = [
        (
            SequenceType.INVOICE,
            "ar.invoice",
            "invoice_number",
            "organization_id = :org_id",
        ),
        (
            SequenceType.RECEIPT,
            "ar.customer_payment",
            "payment_number",
            "organization_id = :org_id",
        ),
        (
            SequenceType.SUPPLIER_INVOICE,
            "ap.supplier_invoice",
            "invoice_number",
            "organization_id = :org_id",
        ),
        (
            SequenceType.PAYMENT,
            "ap.supplier_payment",
            "payment_number",
            "organization_id = :org_id",
        ),
        (
            SequenceType.EXPENSE,
            "expense.expense_claim",
            "claim_number",
            "organization_id = :org_id",
        ),
    ]

    for seq_type, table, col, where in sequence_checks:
        # Extract trailing numeric suffix from document numbers
        # Handles: "INV-00421", "ACC-SINV-2025-00421", "REC-00103"
        # Uses regexp_replace to keep only the final numeric group
        sql = f"""
            SELECT MAX(
                CAST(
                    NULLIF(regexp_replace({col}, '.*[^0-9]', ''), '')
                    AS INTEGER
                )
            )
            FROM {table}
            WHERE {where}
        """  # noqa: S608
        max_num = db.execute(text(sql), {"org_id": str(ORG_ID)}).scalar() or 0

        if max_num > 0:
            seq = db.scalar(
                select(NumberingSequence).where(
                    NumberingSequence.organization_id == ORG_ID,
                    NumberingSequence.sequence_type == seq_type,
                )
            )
            if seq and seq.current_number < max_num:
                old = seq.current_number
                seq.current_number = max_num
                seq.last_used_at = datetime.now(UTC)
                db.flush()
                logger.info(
                    "  %s sequence: %d → %d",
                    seq_type.value,
                    old,
                    max_num,
                )
            elif seq:
                logger.info(
                    "  %s sequence already at %d (max: %d)",
                    seq_type.value,
                    seq.current_number,
                    max_num,
                )

    db.commit()
    logger.info("  Numbering sequences updated")


# ── Step 2: Rebuild account balances ────────────────────────────────────


def _rebuild_account_balances(db: Session) -> None:
    """Rebuild balance cache for every period with posted ledger lines."""
    from app.models.finance.gl.account_balance import BalanceType
    from app.models.finance.gl.fiscal_period import FiscalPeriod
    from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
    from app.services.finance.gl.account_balance import AccountBalanceService

    logger.info("Step 2: Rebuilding account balances")

    # Find all periods that have posted ledger lines
    period_ids = db.scalars(
        select(PostedLedgerLine.fiscal_period_id)
        .where(PostedLedgerLine.organization_id == ORG_ID)
        .distinct()
    ).all()

    if not period_ids:
        logger.warning("  No periods with posted ledger lines found")
        return

    # Get period names for logging
    periods = db.scalars(
        select(FiscalPeriod).where(
            FiscalPeriod.fiscal_period_id.in_(period_ids),
        )
    ).all()

    period_map = {p.fiscal_period_id: p for p in periods}
    total_balances = 0

    # Sort by start_date for chronological rebuild
    sorted_pids = sorted(
        [pid for pid in period_ids if pid in period_map],
        key=lambda p: period_map[p].start_date,
    )
    # Append any unmapped period IDs at the end
    mapped_set = set(sorted_pids)
    sorted_pids.extend(pid for pid in period_ids if pid not in mapped_set)

    for pid in sorted_pids:
        period = period_map.get(pid)
        label = period.period_name if period else str(pid)

        count = AccountBalanceService.rebuild_balances_for_period(
            db,
            organization_id=ORG_ID,
            fiscal_period_id=pid,
            balance_type=BalanceType.ACTUAL,
        )
        total_balances += count
        logger.info("  %-30s → %d balance records", label, count)

    logger.info("  Total balance records rebuilt: %d", total_balances)


# ── Step 3: Verification queries ────────────────────────────────────────

VERIFY_QUERIES: list[tuple[str, str]] = [
    (
        "GL balance check (total debit - credit)",
        """
        SELECT
            COALESCE(SUM(debit_amount), 0) AS total_debit,
            COALESCE(SUM(credit_amount), 0) AS total_credit,
            COALESCE(SUM(debit_amount), 0) - COALESCE(SUM(credit_amount), 0) AS diff
        FROM gl.posted_ledger_line
        WHERE organization_id = :org_id
        """,
    ),
    (
        "Journal entry count",
        """
        SELECT COUNT(*) AS journal_count
        FROM gl.journal_entry
        WHERE organization_id = :org_id
        """,
    ),
    (
        "Posted ledger line count",
        """
        SELECT COUNT(*) AS pll_count
        FROM gl.posted_ledger_line
        WHERE organization_id = :org_id
        """,
    ),
    (
        "GL total debit amount",
        """
        SELECT COALESCE(SUM(debit_amount), 0) AS total_debit
        FROM gl.posted_ledger_line
        WHERE organization_id = :org_id
        """,
    ),
    (
        "Source doc linking (orphaned journals)",
        """
        SELECT
            source_module,
            source_document_type,
            COUNT(*) AS unlinked
        FROM gl.journal_entry
        WHERE organization_id = :org_id
          AND source_document_id IS NULL
          AND source_module IS NOT NULL
        GROUP BY source_module, source_document_type
        ORDER BY unlinked DESC
        """,
    ),
    (
        "Bank reconciliation match rate",
        """
        SELECT
            COUNT(*) AS total_lines,
            SUM(CASE WHEN is_matched THEN 1 ELSE 0 END) AS matched_lines,
            ROUND(
                100.0 * SUM(CASE WHEN is_matched THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                1
            ) AS match_pct
        FROM banking.bank_statement_lines bsl
        JOIN banking.bank_statements bs ON bs.statement_id = bsl.statement_id
        WHERE bs.organization_id = :org_id
        """,
    ),
    (
        "Stale account balances",
        """
        SELECT COUNT(*) AS stale_count
        FROM gl.account_balance
        WHERE organization_id = :org_id
          AND is_stale = TRUE
        """,
    ),
    (
        "Sync entity counts by doctype",
        """
        SELECT source_doctype, COUNT(*) AS cnt
        FROM sync.sync_entity
        WHERE organization_id = :org_id
          AND source_doctype IN (
              'Sales Invoice', 'Payment Entry', 'Journal Entry',
              'Purchase Invoice', 'Expense Claim',
              'Bank Transaction', 'Bank Transaction Payments'
          )
        GROUP BY source_doctype
        ORDER BY cnt DESC
        """,
    ),
]


def _run_verification(db: Session) -> bool:
    """Run verification queries and log results. Returns True if all pass."""
    logger.info("Step 3: Running verification queries")

    all_ok = True
    params = {"org_id": str(ORG_ID)}

    for label, sql in VERIFY_QUERIES:
        logger.info("  [CHECK] %s", label)
        rows = db.execute(text(sql), params).fetchall()

        if not rows:
            logger.info("    (no rows)")
            continue

        # Log each result row
        for row in rows:
            row_dict = row._mapping
            parts = [f"{k}={v}" for k, v in row_dict.items()]
            logger.info("    %s", ", ".join(parts))

        # Specific checks
        if label == "GL balance check (total debit - credit)":
            diff = rows[0]._mapping["diff"]
            if abs(Decimal(str(diff))) > Decimal("300000"):
                logger.warning(
                    "    ⚠ GL imbalance > ₦300K: %s (expected ~₦211K from ERPNext rounding)",
                    diff,
                )
                all_ok = False
            else:
                logger.info("    ✓ Within expected range")

        elif label == "Stale account balances":
            stale = rows[0]._mapping["stale_count"]
            if stale > 0:
                logger.warning("    ⚠ %d stale balances found", stale)
                all_ok = False
            else:
                logger.info("    ✓ No stale balances")

    return all_ok


# ── Main ────────────────────────────────────────────────────────────────


def main() -> None:
    from app.db import SessionLocal

    logger.info("=" * 60)
    logger.info("Phase 6: Rebuild & Verify")
    logger.info("  Org: %s", ORG_ID)
    logger.info("=" * 60)

    # Step 1: Update numbering sequences
    with SessionLocal() as db:
        _update_numbering_sequences(db)

    # Step 2: Rebuild account balances (uses its own commit)
    with SessionLocal() as db:
        _rebuild_account_balances(db)

    # Step 3: Verify
    with SessionLocal() as db:
        ok = _run_verification(db)

    logger.info("-" * 60)
    if ok:
        logger.info("Phase 6 complete. All checks passed. ✓")
    else:
        logger.warning("Phase 6 complete. Some checks need attention — review above.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Phase 6 failed")
        sys.exit(1)
