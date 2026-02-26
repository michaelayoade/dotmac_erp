#!/usr/bin/env python3
"""
AR Transaction Deduplication Script.

Both Splynx and ERPNext synced the same AR transactions into DotMac without
cross-referencing each other's IDs, creating duplicate payments, invoices,
and GL journal entries.  This script identifies and voids the ERPNext-side
duplicates, cleans up the GL, recalculates invoice balances, merges the
14 remaining duplicate customers, and rebuilds account balances.

Approach:
    - Splynx record = keeper  (has splynx_id, no erpnext_id)
    - ERPNext record = duplicate (has erpnext_id, no splynx_id)
    - Duplicate ERPNext records are VOIDED (not deleted) for audit trail.
    - Their posted_ledger_lines ARE deleted (import error, not business reversal).
    - The erpnext_id is copied to the keeper so both IDs are linked.

Usage:
    python scripts/dedup_ar_transactions.py --dry-run          # Report only
    python scripts/dedup_ar_transactions.py --execute           # Run all steps
    python scripts/dedup_ar_transactions.py --execute --step 3  # Run specific step
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

# ── Bootstrap ────────────────────────────────────────────────────────────
sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

ORG_ID = "00000000-0000-0000-0000-000000000001"
BATCH_SIZE = 500


# ── Precondition checks ─────────────────────────────────────────────────


def _check_customer_dedup_prerequisite(db: Session) -> None:
    """Verify that dedup_customers.py has already been run.

    Steps 1-4 match duplicates on customer_id, so ERPNext↔Splynx customers
    must already share the same customer_id.  If unmerged name-duplicates
    exist, payment/invoice matching will miss cross-customer pairs.
    """
    unmerged = (
        db.execute(
            text("""
        SELECT count(*) FROM ar.customer e
        JOIN ar.customer s
          ON lower(trim(e.legal_name)) = lower(trim(s.legal_name))
         AND s.splynx_id IS NOT NULL
        WHERE e.erpnext_id IS NOT NULL
          AND e.splynx_id IS NULL
          AND e.customer_id != s.customer_id
          AND e.organization_id = :org
          AND s.organization_id = :org
        """),
            {"org": ORG_ID},
        ).scalar()
        or 0
    )

    if unmerged > 0:
        logger.error(
            "PRECONDITION FAILED: %d unmerged ERPNext↔Splynx customer pairs. "
            "Run dedup_customers.py first, or use --step 6 to merge them.",
            unmerged,
        )
        sys.exit(1)

    logger.info("Precondition OK: no unmerged ERPNext↔Splynx customer pairs.")


# ── Data classes ─────────────────────────────────────────────────────────


@dataclass
class StepResult:
    """Result of a single step."""

    step: int
    name: str
    dry_run: bool
    details: dict[str, object] = field(default_factory=dict)

    def log_summary(self) -> None:
        logger.info("=" * 70)
        mode = "DRY RUN" if self.dry_run else "EXECUTED"
        logger.info("Step %d: %s [%s]", self.step, self.name, mode)
        for k, v in self.details.items():
            logger.info("  %s: %s", k, v)
        logger.info("=" * 70)


@dataclass
class DupPair:
    """A matched pair of keeper (Splynx) and duplicate (ERPNext) records."""

    keeper_id: UUID
    dup_id: UUID
    dup_journal_entry_id: UUID | None
    dup_posting_batch_id: UUID | None
    dup_erpnext_id: str | None


# ── Helpers ──────────────────────────────────────────────────────────────


def _count(db: Session, sql: str, params: dict[str, object] | None = None) -> int:
    """Execute a COUNT query and return the scalar result."""
    return db.execute(text(sql), params or {}).scalar() or 0


def _exec(db: Session, sql: str, params: dict[str, object] | None = None) -> int:
    """Execute a DML statement and return rowcount."""
    result = db.execute(text(sql), params or {})
    return int(result.rowcount)  # type: ignore[attr-defined]


def _void_gl_for_journal(
    db: Session,
    journal_entry_id: UUID,
    posting_batch_id: UUID | None,
) -> dict[str, int]:
    """Delete posted_ledger_lines and void a journal entry.

    Returns counts of rows affected.
    """
    stats: dict[str, int] = {
        "ledger_lines_deleted": 0,
        "batches_deleted": 0,
        "journals_voided": 0,
    }

    if not journal_entry_id:
        return stats

    je_id = str(journal_entry_id)

    # 1. Delete posted ledger lines
    stats["ledger_lines_deleted"] = _exec(
        db,
        "DELETE FROM gl.posted_ledger_line WHERE journal_entry_id = :je_id",
        {"je_id": je_id},
    )

    # 2. Delete posting batch if it was single-entry
    if posting_batch_id:
        pb_id = str(posting_batch_id)
        remaining = _count(
            db,
            """
            SELECT count(*) FROM gl.journal_entry
            WHERE posting_batch_id = :pb_id AND status != 'VOID'
              AND journal_entry_id != :je_id
            """,
            {"pb_id": pb_id, "je_id": je_id},
        )
        if remaining == 0:
            stats["batches_deleted"] = _exec(
                db,
                "DELETE FROM gl.posting_batch WHERE batch_id = :pb_id",
                {"pb_id": pb_id},
            )

    # 3. Void the journal entry
    stats["journals_voided"] = _exec(
        db,
        "UPDATE gl.journal_entry SET status = 'VOID' WHERE journal_entry_id = :je_id",
        {"je_id": je_id},
    )

    return stats


# ── Step 1: Identify duplicate payments ──────────────────────────────────


def step1_identify_dup_payments(db: Session, dry_run: bool) -> StepResult:
    """Find ERPNext-only payments matching a Splynx payment on
    (customer_id, amount, payment_date).  Uses ROW_NUMBER() for 1:1 pairing.
    """
    result = StepResult(step=1, name="Identify duplicate payments", dry_run=dry_run)

    # Count total payments by source
    total_payments = _count(
        db,
        "SELECT count(*) FROM ar.customer_payment WHERE organization_id = :org",
        {"org": ORG_ID},
    )
    splynx_payments = _count(
        db,
        "SELECT count(*) FROM ar.customer_payment WHERE splynx_id IS NOT NULL AND erpnext_id IS NULL AND organization_id = :org",
        {"org": ORG_ID},
    )
    erpnext_payments = _count(
        db,
        "SELECT count(*) FROM ar.customer_payment WHERE erpnext_id IS NOT NULL AND splynx_id IS NULL AND organization_id = :org",
        {"org": ORG_ID},
    )

    # 1:1 pairing with ROW_NUMBER
    pair_sql = """
    WITH spx AS (
        SELECT payment_id, customer_id, amount, payment_date, splynx_id,
               journal_entry_id, posting_batch_id,
               ROW_NUMBER() OVER (
                   PARTITION BY customer_id, amount, payment_date
                   ORDER BY created_at ASC, payment_id ASC
               ) AS rn
        FROM ar.customer_payment
        WHERE splynx_id IS NOT NULL AND erpnext_id IS NULL
          AND status != 'VOID'
          AND organization_id = :org
    ),
    erp AS (
        SELECT payment_id, customer_id, amount, payment_date, erpnext_id,
               journal_entry_id, posting_batch_id,
               ROW_NUMBER() OVER (
                   PARTITION BY customer_id, amount, payment_date
                   ORDER BY created_at ASC, payment_id ASC
               ) AS rn
        FROM ar.customer_payment
        WHERE erpnext_id IS NOT NULL AND splynx_id IS NULL
          AND status != 'VOID'
          AND organization_id = :org
    )
    SELECT spx.payment_id   AS keeper_id,
           erp.payment_id   AS dup_id,
           erp.erpnext_id   AS dup_erpnext_id,
           erp.journal_entry_id AS dup_je_id,
           erp.posting_batch_id AS dup_pb_id
    FROM spx
    JOIN erp
      ON spx.customer_id = erp.customer_id
     AND spx.amount = erp.amount
     AND spx.payment_date = erp.payment_date
     AND spx.rn = erp.rn
    """
    pairs = db.execute(text(pair_sql), {"org": ORG_ID}).fetchall()

    # Count ERPNext payments that didn't match (extras)
    matched_erp_ids = {str(r[1]) for r in pairs}
    all_erp_sql = """
    SELECT payment_id FROM ar.customer_payment
    WHERE erpnext_id IS NOT NULL AND splynx_id IS NULL
      AND status != 'VOID' AND organization_id = :org
    """
    all_erp = db.execute(text(all_erp_sql), {"org": ORG_ID}).fetchall()
    unmatched_erp = sum(1 for r in all_erp if str(r[0]) not in matched_erp_ids)

    result.details = {
        "total payments": total_payments,
        "Splynx-only payments": splynx_payments,
        "ERPNext-only payments": erpnext_payments,
        "matched pairs (exact: customer+amount+date)": len(pairs),
        "unmatched ERPNext payments (kept)": unmatched_erp,
    }
    result.log_summary()

    # Stash pairs for step 2 (in-memory, not persisted)
    return result


def _get_payment_pairs(db: Session) -> list[DupPair]:
    """Re-run the pairing query and return structured pairs."""
    pair_sql = """
    WITH spx AS (
        SELECT payment_id, customer_id, amount, payment_date,
               ROW_NUMBER() OVER (
                   PARTITION BY customer_id, amount, payment_date
                   ORDER BY created_at ASC, payment_id ASC
               ) AS rn
        FROM ar.customer_payment
        WHERE splynx_id IS NOT NULL AND erpnext_id IS NULL
          AND status != 'VOID'
          AND organization_id = :org
    ),
    erp AS (
        SELECT payment_id, customer_id, amount, payment_date, erpnext_id,
               journal_entry_id, posting_batch_id,
               ROW_NUMBER() OVER (
                   PARTITION BY customer_id, amount, payment_date
                   ORDER BY created_at ASC, payment_id ASC
               ) AS rn
        FROM ar.customer_payment
        WHERE erpnext_id IS NOT NULL AND splynx_id IS NULL
          AND status != 'VOID'
          AND organization_id = :org
    )
    SELECT spx.payment_id   AS keeper_id,
           erp.payment_id   AS dup_id,
           erp.erpnext_id   AS dup_erpnext_id,
           erp.journal_entry_id AS dup_je_id,
           erp.posting_batch_id AS dup_pb_id
    FROM spx
    JOIN erp
      ON spx.customer_id = erp.customer_id
     AND spx.amount = erp.amount
     AND spx.payment_date = erp.payment_date
     AND spx.rn = erp.rn
    """
    rows = db.execute(text(pair_sql), {"org": ORG_ID}).fetchall()
    return [
        DupPair(
            keeper_id=UUID(str(r[0])),
            dup_id=UUID(str(r[1])),
            dup_erpnext_id=r[2],
            dup_journal_entry_id=UUID(str(r[3])) if r[3] else None,
            dup_posting_batch_id=UUID(str(r[4])) if r[4] else None,
        )
        for r in rows
    ]


# ── Step 2: Void duplicate payments ─────────────────────────────────────


def step2_void_dup_payments(db: Session, dry_run: bool) -> StepResult:
    """Void duplicate ERPNext payments and clean up their GL entries."""
    result = StepResult(step=2, name="Void duplicate payments", dry_run=dry_run)

    pairs = _get_payment_pairs(db)
    logger.info("Processing %d duplicate payment pairs", len(pairs))

    total_allocations_deleted = 0
    total_ledger_lines_deleted = 0
    total_batches_deleted = 0
    total_journals_voided = 0
    total_payments_voided = 0
    total_erpnext_ids_linked = 0
    errors: list[str] = []

    for i in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[i : i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1

        if not dry_run:
            sp = db.begin_nested()  # SAVEPOINT

        try:
            for pair in batch:
                dup_id = str(pair.dup_id)
                keeper_id = str(pair.keeper_id)

                if not dry_run:
                    # 1. Delete payment allocations for the duplicate
                    alloc_deleted = _exec(
                        db,
                        "DELETE FROM ar.payment_allocation WHERE payment_id = :pid",
                        {"pid": dup_id},
                    )
                    total_allocations_deleted += alloc_deleted

                    # 2. Clean up GL
                    if pair.dup_journal_entry_id:
                        gl_stats = _void_gl_for_journal(
                            db, pair.dup_journal_entry_id, pair.dup_posting_batch_id
                        )
                        total_ledger_lines_deleted += gl_stats["ledger_lines_deleted"]
                        total_batches_deleted += gl_stats["batches_deleted"]
                        total_journals_voided += gl_stats["journals_voided"]

                    # 3. Void the payment
                    _exec(
                        db,
                        "UPDATE ar.customer_payment SET status = 'VOID' WHERE payment_id = :pid",
                        {"pid": dup_id},
                    )
                    total_payments_voided += 1

                    # 4. Link erpnext_id to keeper
                    if pair.dup_erpnext_id:
                        _exec(
                            db,
                            """UPDATE ar.customer_payment
                               SET erpnext_id = :eid
                               WHERE payment_id = :kid AND erpnext_id IS NULL""",
                            {"eid": pair.dup_erpnext_id, "kid": keeper_id},
                        )
                        total_erpnext_ids_linked += 1
                else:
                    total_payments_voided += 1

            if not dry_run:
                sp.commit()
                if batch_num % 10 == 0:
                    logger.info(
                        "  Batch %d committed (%d/%d)",
                        batch_num,
                        i + len(batch),
                        len(pairs),
                    )

        except Exception as e:
            logger.exception("Batch %d failed", batch_num)
            errors.append(f"Batch {batch_num}: {e}")
            if not dry_run:
                sp.rollback()

    result.details = {
        "duplicate payments processed": total_payments_voided,
        "payment allocations deleted": total_allocations_deleted,
        "ledger lines deleted": total_ledger_lines_deleted,
        "posting batches deleted": total_batches_deleted,
        "journals voided": total_journals_voided,
        "erpnext_ids linked to keepers": total_erpnext_ids_linked,
        "errors": errors if errors else "none",
    }
    result.log_summary()
    return result


# ── Step 3: Identify duplicate invoices ──────────────────────────────────


def step3_identify_dup_invoices(db: Session, dry_run: bool) -> StepResult:
    """Find ERPNext-only invoices matching a Splynx invoice on
    (customer_id, total_amount) within ±3 days of invoice_date.
    """
    result = StepResult(step=3, name="Identify duplicate invoices", dry_run=dry_run)

    total_invoices = _count(
        db,
        "SELECT count(*) FROM ar.invoice WHERE organization_id = :org",
        {"org": ORG_ID},
    )
    splynx_invoices = _count(
        db,
        "SELECT count(*) FROM ar.invoice WHERE splynx_id IS NOT NULL AND erpnext_id IS NULL AND organization_id = :org AND status != 'VOID'",
        {"org": ORG_ID},
    )
    erpnext_invoices = _count(
        db,
        "SELECT count(*) FROM ar.invoice WHERE erpnext_id IS NOT NULL AND splynx_id IS NULL AND organization_id = :org AND status != 'VOID'",
        {"org": ORG_ID},
    )

    pairs = _get_invoice_pairs(db)

    # Count unmatched ERPNext invoices
    matched_erp_ids = {str(p.dup_id) for p in pairs}
    all_erp_sql = """
    SELECT invoice_id FROM ar.invoice
    WHERE erpnext_id IS NOT NULL AND splynx_id IS NULL
      AND status != 'VOID' AND organization_id = :org
    """
    all_erp = db.execute(text(all_erp_sql), {"org": ORG_ID}).fetchall()
    unmatched_erp = sum(1 for r in all_erp if str(r[0]) not in matched_erp_ids)

    result.details = {
        "total invoices": total_invoices,
        "Splynx-only invoices (non-void)": splynx_invoices,
        "ERPNext-only invoices (non-void)": erpnext_invoices,
        "matched pairs (fuzzy: customer+amount ±3 days)": len(pairs),
        "unmatched ERPNext invoices (kept)": unmatched_erp,
    }
    result.log_summary()
    return result


def _get_invoice_pairs(db: Session) -> list[DupPair]:
    """Match ERPNext invoices to Splynx invoices using fuzzy date window."""
    pair_sql = """
    WITH spx AS (
        SELECT invoice_id, customer_id, total_amount, invoice_date,
               ROW_NUMBER() OVER (
                   PARTITION BY customer_id, total_amount
                   ORDER BY invoice_date ASC, created_at ASC, invoice_id ASC
               ) AS rn
        FROM ar.invoice
        WHERE splynx_id IS NOT NULL AND erpnext_id IS NULL
          AND status != 'VOID'
          AND organization_id = :org
    ),
    erp AS (
        SELECT invoice_id, customer_id, total_amount, invoice_date,
               erpnext_id, journal_entry_id, posting_batch_id,
               ROW_NUMBER() OVER (
                   PARTITION BY customer_id, total_amount
                   ORDER BY invoice_date ASC, created_at ASC, invoice_id ASC
               ) AS rn
        FROM ar.invoice
        WHERE erpnext_id IS NOT NULL AND splynx_id IS NULL
          AND status != 'VOID'
          AND organization_id = :org
    )
    SELECT spx.invoice_id   AS keeper_id,
           erp.invoice_id   AS dup_id,
           erp.erpnext_id   AS dup_erpnext_id,
           erp.journal_entry_id AS dup_je_id,
           erp.posting_batch_id AS dup_pb_id
    FROM spx
    JOIN erp
      ON spx.customer_id = erp.customer_id
     AND spx.total_amount = erp.total_amount
     AND spx.rn = erp.rn
     AND ABS(spx.invoice_date - erp.invoice_date) <= 3
    """
    rows = db.execute(text(pair_sql), {"org": ORG_ID}).fetchall()
    return [
        DupPair(
            keeper_id=UUID(str(r[0])),
            dup_id=UUID(str(r[1])),
            dup_erpnext_id=r[2],
            dup_journal_entry_id=UUID(str(r[3])) if r[3] else None,
            dup_posting_batch_id=UUID(str(r[4])) if r[4] else None,
        )
        for r in rows
    ]


# ── Step 4: Void duplicate invoices ─────────────────────────────────────


def step4_void_dup_invoices(db: Session, dry_run: bool) -> StepResult:
    """Void duplicate ERPNext invoices and clean up their GL entries."""
    result = StepResult(step=4, name="Void duplicate invoices", dry_run=dry_run)

    pairs = _get_invoice_pairs(db)
    logger.info("Processing %d duplicate invoice pairs", len(pairs))

    total_allocations_deleted = 0
    total_line_taxes_deleted = 0
    total_ledger_lines_deleted = 0
    total_batches_deleted = 0
    total_journals_voided = 0
    total_invoices_voided = 0
    total_erpnext_ids_linked = 0
    errors: list[str] = []

    for i in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[i : i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1

        if not dry_run:
            sp = db.begin_nested()

        try:
            for pair in batch:
                dup_id = str(pair.dup_id)
                keeper_id = str(pair.keeper_id)

                if not dry_run:
                    # 1. Delete payment allocations referencing the dup invoice
                    alloc_deleted = _exec(
                        db,
                        "DELETE FROM ar.payment_allocation WHERE invoice_id = :iid",
                        {"iid": dup_id},
                    )
                    total_allocations_deleted += alloc_deleted

                    # 2. Delete invoice line taxes
                    lt_deleted = _exec(
                        db,
                        """DELETE FROM ar.invoice_line_tax
                           WHERE line_id IN (
                               SELECT line_id FROM ar.invoice_line
                               WHERE invoice_id = :iid
                           )""",
                        {"iid": dup_id},
                    )
                    total_line_taxes_deleted += lt_deleted

                    # 3. Clean up GL
                    if pair.dup_journal_entry_id:
                        gl_stats = _void_gl_for_journal(
                            db, pair.dup_journal_entry_id, pair.dup_posting_batch_id
                        )
                        total_ledger_lines_deleted += gl_stats["ledger_lines_deleted"]
                        total_batches_deleted += gl_stats["batches_deleted"]
                        total_journals_voided += gl_stats["journals_voided"]

                    # 4. Void the invoice
                    _exec(
                        db,
                        "UPDATE ar.invoice SET status = 'VOID' WHERE invoice_id = :iid",
                        {"iid": dup_id},
                    )
                    total_invoices_voided += 1

                    # 5. Link erpnext_id to keeper
                    if pair.dup_erpnext_id:
                        _exec(
                            db,
                            """UPDATE ar.invoice
                               SET erpnext_id = :eid
                               WHERE invoice_id = :kid AND erpnext_id IS NULL""",
                            {"eid": pair.dup_erpnext_id, "kid": keeper_id},
                        )
                        total_erpnext_ids_linked += 1
                else:
                    total_invoices_voided += 1

            if not dry_run:
                sp.commit()
                if batch_num % 10 == 0:
                    logger.info(
                        "  Batch %d committed (%d/%d)",
                        batch_num,
                        i + len(batch),
                        len(pairs),
                    )

        except Exception as e:
            logger.exception("Batch %d failed", batch_num)
            errors.append(f"Batch {batch_num}: {e}")
            if not dry_run:
                sp.rollback()

    result.details = {
        "duplicate invoices processed": total_invoices_voided,
        "payment allocations deleted": total_allocations_deleted,
        "invoice line taxes deleted": total_line_taxes_deleted,
        "ledger lines deleted": total_ledger_lines_deleted,
        "posting batches deleted": total_batches_deleted,
        "journals voided": total_journals_voided,
        "erpnext_ids linked to keepers": total_erpnext_ids_linked,
        "errors": errors if errors else "none",
    }
    result.log_summary()
    return result


# ── Step 5: Recalculate invoice amount_paid ──────────────────────────────


def step5_recalculate_amount_paid(db: Session, dry_run: bool) -> StepResult:
    """Recalculate amount_paid for all non-void invoices from surviving
    payment allocations, then fix invoice statuses accordingly.
    """
    result = StepResult(step=5, name="Recalculate invoice amount_paid", dry_run=dry_run)

    # Find invoices where amount_paid doesn't match surviving allocations
    mismatch_sql = """
    SELECT i.invoice_id,
           i.amount_paid AS current_amount_paid,
           i.total_amount,
           i.status,
           COALESCE(alloc.total_alloc, 0) AS correct_amount_paid
    FROM ar.invoice i
    LEFT JOIN (
        SELECT pa.invoice_id, SUM(pa.allocated_amount) AS total_alloc
        FROM ar.payment_allocation pa
        JOIN ar.customer_payment p ON p.payment_id = pa.payment_id
        WHERE p.status != 'VOID'
        GROUP BY pa.invoice_id
    ) alloc ON alloc.invoice_id = i.invoice_id
    WHERE i.status != 'VOID'
      AND i.organization_id = :org
      AND i.amount_paid != COALESCE(alloc.total_alloc, 0)
    """
    mismatches = db.execute(text(mismatch_sql), {"org": ORG_ID}).fetchall()
    logger.info("Found %d invoices with incorrect amount_paid", len(mismatches))

    amount_paid_fixes = 0
    status_fixes = 0

    if not dry_run and mismatches:
        # Bulk update amount_paid
        _exec(
            db,
            """
            UPDATE ar.invoice i
            SET amount_paid = COALESCE((
                SELECT SUM(pa.allocated_amount)
                FROM ar.payment_allocation pa
                JOIN ar.customer_payment p ON p.payment_id = pa.payment_id
                WHERE pa.invoice_id = i.invoice_id AND p.status != 'VOID'
            ), 0)
            WHERE i.organization_id = :org
              AND i.status != 'VOID'
            """,
            {"org": ORG_ID},
        )
        amount_paid_fixes = len(mismatches)

        # Fix statuses: PAID if fully paid, PARTIALLY_PAID if partial, POSTED if zero
        # Only fix invoices in GL-impacting statuses (POSTED, PARTIALLY_PAID, PAID, OVERDUE)
        status_fixes += _exec(
            db,
            """
            UPDATE ar.invoice
            SET status = 'PAID'
            WHERE organization_id = :org
              AND status IN ('POSTED', 'PARTIALLY_PAID', 'OVERDUE')
              AND amount_paid >= total_amount
              AND total_amount > 0
            """,
            {"org": ORG_ID},
        )
        status_fixes += _exec(
            db,
            """
            UPDATE ar.invoice
            SET status = 'PARTIALLY_PAID'
            WHERE organization_id = :org
              AND status IN ('POSTED', 'PAID', 'OVERDUE')
              AND amount_paid > 0
              AND amount_paid < total_amount
            """,
            {"org": ORG_ID},
        )
        status_fixes += _exec(
            db,
            """
            UPDATE ar.invoice
            SET status = 'POSTED'
            WHERE organization_id = :org
              AND status IN ('PARTIALLY_PAID', 'PAID')
              AND amount_paid = 0
              AND total_amount > 0
            """,
            {"org": ORG_ID},
        )

        db.flush()

    # Sample some mismatches for reporting
    sample = mismatches[:5]
    sample_details = [
        {
            "invoice_id": str(r[0])[:8],
            "was": float(r[1]),
            "correct": float(r[4]),
            "total": float(r[2]),
        }
        for r in sample
    ]

    result.details = {
        "invoices with wrong amount_paid": len(mismatches),
        "amount_paid corrected": amount_paid_fixes,
        "status transitions applied": status_fixes,
        "sample mismatches (first 5)": sample_details,
    }
    result.log_summary()
    return result


# ── Step 6: Merge duplicate customers ────────────────────────────────────

# Tables with customer_id FK (from dedup_customers.py)
FK_TABLES: list[tuple[str, str, str]] = [
    ("ar.invoice", "customer_id", "invoice_id"),
    ("ar.customer_payment", "customer_id", "payment_id"),
    ("ar.quote", "customer_id", "quote_id"),
    ("ar.sales_order", "customer_id", "sales_order_id"),
    ("ar.contract", "customer_id", "contract_id"),
    ("ar.ar_aging_snapshot", "customer_id", "snapshot_id"),
    ("banking.payee", "customer_id", "payee_id"),
    ("core_org.project", "customer_id", "project_id"),
    ("support.ticket", "customer_id", "ticket_id"),
]


def _reassign_customer_fks(
    db: Session,
    winner_id: UUID,
    loser_id: UUID,
) -> dict[str, int]:
    """Reassign all FK references from loser to winner."""
    updates: dict[str, int] = {}
    for table, col, _pk in FK_TABLES:
        count = _count(
            db,
            f"SELECT count(*) FROM {table} WHERE {col} = :loser_id",
            {"loser_id": str(loser_id)},
        )
        if count > 0:
            # Handle aging snapshot unique constraint
            if table == "ar.ar_aging_snapshot":
                # Delete conflicting snapshots first
                _exec(
                    db,
                    """DELETE FROM ar.ar_aging_snapshot
                       WHERE customer_id = :loser_id
                         AND (fiscal_period_id, aging_bucket) IN (
                             SELECT fiscal_period_id, aging_bucket
                             FROM ar.ar_aging_snapshot
                             WHERE customer_id = :winner_id
                         )""",
                    {"winner_id": str(winner_id), "loser_id": str(loser_id)},
                )
                _exec(
                    db,
                    f"UPDATE {table} SET {col} = :winner_id WHERE {col} = :loser_id",
                    {"winner_id": str(winner_id), "loser_id": str(loser_id)},
                )
            else:
                _exec(
                    db,
                    f"UPDATE {table} SET {col} = :winner_id WHERE {col} = :loser_id",
                    {"winner_id": str(winner_id), "loser_id": str(loser_id)},
                )
            updates[table] = count

    # external_sync
    es_count = _count(
        db,
        """SELECT count(*) FROM ar.external_sync
           WHERE local_entity_id = :loser_id AND entity_type = 'CUSTOMER'""",
        {"loser_id": str(loser_id)},
    )
    if es_count > 0:
        _exec(
            db,
            """UPDATE ar.external_sync
               SET local_entity_id = :winner_id
               WHERE local_entity_id = :loser_id AND entity_type = 'CUSTOMER'""",
            {"winner_id": str(winner_id), "loser_id": str(loser_id)},
        )
        updates["ar.external_sync"] = es_count

    return updates


def step6_merge_dup_customers(db: Session, dry_run: bool) -> StepResult:
    """Merge 14 duplicate customers (ERPNext↔Splynx name match)."""
    result = StepResult(step=6, name="Merge duplicate customers", dry_run=dry_run)

    # Find name-match duplicates: ERPNext customer matching Splynx customer
    pairs_sql = """
    SELECT e.customer_id AS erp_cid,
           e.customer_code AS erp_code,
           e.erpnext_id,
           s.customer_id AS spx_cid,
           s.customer_code AS spx_code,
           s.splynx_id,
           e.legal_name
    FROM ar.customer e
    JOIN ar.customer s
      ON lower(trim(e.legal_name)) = lower(trim(s.legal_name))
     AND s.splynx_id IS NOT NULL
    WHERE e.erpnext_id IS NOT NULL
      AND e.splynx_id IS NULL
      AND e.customer_id != s.customer_id
      AND e.organization_id = :org
      AND s.organization_id = :org
    ORDER BY e.legal_name
    """
    pairs = db.execute(text(pairs_sql), {"org": ORG_ID}).fetchall()
    logger.info("Found %d customer duplicate pairs", len(pairs))

    total_merged = 0
    total_deleted = 0
    all_fk_updates: dict[str, int] = {}
    errors: list[str] = []

    for row in pairs:
        erp_cid = UUID(str(row[0]))
        erpnext_id = row[2]
        spx_cid = UUID(str(row[3]))  # winner
        name = row[6]

        try:
            if not dry_run:
                # Copy erpnext_id to winner
                if erpnext_id:
                    _exec(
                        db,
                        """UPDATE ar.customer
                           SET erpnext_id = :eid
                           WHERE customer_id = :cid AND erpnext_id IS NULL""",
                        {"eid": erpnext_id, "cid": str(spx_cid)},
                    )

                # Reassign FKs from ERPNext → Splynx customer
                fk_updates = _reassign_customer_fks(db, spx_cid, erp_cid)
                for table, count in fk_updates.items():
                    all_fk_updates[table] = all_fk_updates.get(table, 0) + count

                # Delete ERPNext customer
                _exec(
                    db,
                    "DELETE FROM ar.customer WHERE customer_id = :cid",
                    {"cid": str(erp_cid)},
                )
                total_deleted += 1

            total_merged += 1
            logger.info("  Merged: %s (ERPNext → Splynx)", name)

        except Exception as e:
            logger.exception("Failed to merge customer: %s", name)
            errors.append(f"{name}: {e}")

    if not dry_run:
        db.flush()

    result.details = {
        "customer pairs found": len(pairs),
        "customers merged": total_merged,
        "ERPNext customers deleted": total_deleted,
        "FK updates": all_fk_updates if all_fk_updates else "none",
        "errors": errors if errors else "none",
    }
    result.log_summary()
    return result


# ── Step 7: Rebuild account balances ─────────────────────────────────────


def step7_rebuild_account_balances(db: Session, dry_run: bool) -> StepResult:
    """Rebuild gl.account_balance from posted_ledger_line data.

    IMPORTANT: rebuild_account_balances() opens its own SessionLocal(), so
    all dedup changes from steps 1-6 must be committed first — otherwise the
    rebuild reads stale (pre-dedup) data and produces inflated balances.
    """
    result = StepResult(step=7, name="Rebuild account balances", dry_run=dry_run)

    if dry_run:
        # Just report current state
        balance_rows = _count(
            db,
            "SELECT count(*) FROM gl.account_balance WHERE organization_id = :org",
            {"org": ORG_ID},
        )
        ledger_line_count = _count(
            db,
            "SELECT count(*) FROM gl.posted_ledger_line WHERE organization_id = :org",
            {"org": ORG_ID},
        )
        result.details = {
            "current account_balance rows": balance_rows,
            "posted_ledger_line rows": ledger_line_count,
            "action": "would rebuild all account balances",
        }
    else:
        # Commit pending changes so the rebuild's independent session sees
        # the voided payments/invoices and deleted ledger lines.
        logger.info("Committing dedup changes before balance rebuild...")
        db.commit()

        from app.tasks.data_health import rebuild_account_balances

        rebuild_result = rebuild_account_balances(ORG_ID)
        result.details = {
            "rows_written": rebuild_result.get("rows_written", 0),
            "errors": rebuild_result.get("errors", []),
        }

    result.log_summary()
    return result


# ── Step 8: Verify integrity ─────────────────────────────────────────────


def step8_verify_integrity(db: Session, dry_run: bool) -> StepResult:
    """Run integrity checks on the cleaned data."""
    result = StepResult(step=8, name="Verify integrity", dry_run=dry_run)
    issues: dict[str, object] = {}

    # 1. No orphaned payment allocations
    orphan_alloc = _count(
        db,
        """SELECT count(*) FROM ar.payment_allocation pa
           WHERE NOT EXISTS (
               SELECT 1 FROM ar.customer_payment p
               WHERE p.payment_id = pa.payment_id AND p.status != 'VOID'
           )""",
    )
    if orphan_alloc > 0:
        issues["orphaned allocations (void payments)"] = orphan_alloc

    orphan_alloc_inv = _count(
        db,
        """SELECT count(*) FROM ar.payment_allocation pa
           WHERE NOT EXISTS (
               SELECT 1 FROM ar.invoice i
               WHERE i.invoice_id = pa.invoice_id AND i.status != 'VOID'
           )""",
    )
    if orphan_alloc_inv > 0:
        issues["orphaned allocations (void invoices)"] = orphan_alloc_inv

    # 2. No posted ledger lines referencing VOID journals
    void_ledger = _count(
        db,
        """SELECT count(*) FROM gl.posted_ledger_line pll
           JOIN gl.journal_entry je ON je.journal_entry_id = pll.journal_entry_id
           WHERE je.status = 'VOID'""",
    )
    if void_ledger > 0:
        issues["ledger lines on VOID journals"] = void_ledger

    # 3. Invoice amount_paid matches allocations
    amount_mismatch = _count(
        db,
        """SELECT count(*) FROM ar.invoice i
           WHERE i.status != 'VOID'
             AND i.organization_id = :org
             AND i.amount_paid != COALESCE((
                 SELECT SUM(pa.allocated_amount)
                 FROM ar.payment_allocation pa
                 JOIN ar.customer_payment p ON p.payment_id = pa.payment_id
                 WHERE pa.invoice_id = i.invoice_id AND p.status != 'VOID'
             ), 0)""",
        {"org": ORG_ID},
    )
    if amount_mismatch > 0:
        issues["invoices with wrong amount_paid"] = amount_mismatch

    # 4. GL trial balance (debits = credits)
    tb = db.execute(
        text("""
        SELECT SUM(debit_amount) AS total_debit,
               SUM(credit_amount) AS total_credit,
               SUM(debit_amount) - SUM(credit_amount) AS diff
        FROM gl.posted_ledger_line
        WHERE organization_id = :org
        """),
        {"org": ORG_ID},
    ).fetchone()
    if tb:
        diff = float(tb[2] or 0)
        if abs(diff) > 0.01:
            issues["trial balance difference"] = f"{diff:,.2f}"

    # 5. No remaining exact-match payment duplicates
    remaining_dup_payments = _count(
        db,
        """SELECT count(*) FROM ar.customer_payment p1
           JOIN ar.customer_payment p2
             ON p1.customer_id = p2.customer_id
            AND p1.amount = p2.amount
            AND p1.payment_date = p2.payment_date
            AND p1.payment_id != p2.payment_id
           WHERE p1.splynx_id IS NOT NULL AND p1.erpnext_id IS NULL
             AND p2.erpnext_id IS NOT NULL AND p2.splynx_id IS NULL
             AND p1.status != 'VOID' AND p2.status != 'VOID'
             AND p1.organization_id = :org""",
        {"org": ORG_ID},
    )
    if remaining_dup_payments > 0:
        issues["remaining duplicate payments"] = remaining_dup_payments

    # 6. Final counts
    final_counts = {}
    for label, sql in [
        (
            "total payments",
            "SELECT count(*) FROM ar.customer_payment WHERE organization_id = :org",
        ),
        (
            "void payments",
            "SELECT count(*) FROM ar.customer_payment WHERE status = 'VOID' AND organization_id = :org",
        ),
        (
            "active payments",
            "SELECT count(*) FROM ar.customer_payment WHERE status != 'VOID' AND organization_id = :org",
        ),
        (
            "total invoices",
            "SELECT count(*) FROM ar.invoice WHERE organization_id = :org",
        ),
        (
            "void invoices",
            "SELECT count(*) FROM ar.invoice WHERE status = 'VOID' AND organization_id = :org",
        ),
        (
            "active invoices",
            "SELECT count(*) FROM ar.invoice WHERE status != 'VOID' AND organization_id = :org",
        ),
        (
            "void journals (AR)",
            "SELECT count(*) FROM gl.journal_entry WHERE status = 'VOID' AND source_module = 'AR' AND organization_id = :org",
        ),
        (
            "total customers",
            "SELECT count(*) FROM ar.customer WHERE organization_id = :org",
        ),
        (
            "customers with both IDs",
            "SELECT count(*) FROM ar.customer WHERE splynx_id IS NOT NULL AND erpnext_id IS NOT NULL AND organization_id = :org",
        ),
    ]:
        final_counts[label] = _count(db, sql, {"org": ORG_ID})

    if issues:
        logger.error("INTEGRITY ISSUES FOUND:")
        for k, v in issues.items():
            logger.error("  %s: %s", k, v)
    else:
        logger.info("All integrity checks passed.")

    result.details = {
        "integrity issues": issues if issues else "NONE (all clean)",
        "trial balance": {
            "total_debit": f"{float(tb[0] or 0):,.2f}" if tb else "N/A",
            "total_credit": f"{float(tb[1] or 0):,.2f}" if tb else "N/A",
            "difference": f"{float(tb[2] or 0):,.2f}" if tb else "N/A",
        },
        "final counts": final_counts,
    }
    result.log_summary()
    return result


# ── Main ──────────────────────────────────────────────────────────────────

STEPS: dict[int, tuple[str, object]] = {
    1: ("Merge duplicate customers", step6_merge_dup_customers),
    2: ("Identify duplicate payments", step1_identify_dup_payments),
    3: ("Void duplicate payments", step2_void_dup_payments),
    4: ("Identify duplicate invoices", step3_identify_dup_invoices),
    5: ("Void duplicate invoices", step4_void_dup_invoices),
    6: ("Recalculate invoice amount_paid", step5_recalculate_amount_paid),
    7: ("Rebuild account balances", step7_rebuild_account_balances),
    8: ("Verify integrity", step8_verify_integrity),
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AR transaction deduplication (Splynx↔ERPNext)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Report only, no changes",
    )
    group.add_argument(
        "--execute",
        action="store_true",
        help="Actually run the deduplication",
    )
    parser.add_argument(
        "--step",
        type=int,
        choices=list(STEPS.keys()),
        help="Run only a specific step",
    )
    args = parser.parse_args()

    dry_run = args.dry_run
    steps_to_run = [args.step] if args.step else sorted(STEPS.keys())

    logger.info(
        "AR Transaction Dedup — mode=%s, steps=%s",
        "DRY RUN" if dry_run else "EXECUTE",
        steps_to_run,
    )

    with SessionLocal() as db:
        # Steps 2-6 match on customer_id, which requires customers to be
        # merged first (step 1).  If running a single step >= 2 without
        # step 1, verify that no unmerged pairs exist.
        runs_customer_merge = 1 in steps_to_run
        needs_merged_customers = any(2 <= s <= 6 for s in steps_to_run)
        if needs_merged_customers and not runs_customer_merge:
            _check_customer_dedup_prerequisite(db)

        try:
            for step_num in steps_to_run:
                _name, step_fn = STEPS[step_num]
                step_fn(db, dry_run)  # type: ignore[operator]

            if not dry_run:
                logger.info("Committing all changes...")
                db.commit()
                logger.info("Done! All changes committed.")
            else:
                logger.info("Dry run complete — no changes made.")
                db.rollback()

        except Exception:
            logger.exception("Dedup failed! Rolling back.")
            db.rollback()
            sys.exit(1)


if __name__ == "__main__":
    main()
