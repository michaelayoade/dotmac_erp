#!/usr/bin/env python3
"""
Customer Deduplication & Renumbering Script.

Merges duplicate customers created by ERPNext and Splynx sync pipelines,
then renumbers all surviving customers with canonical CUST-NNNNN codes.

Usage:
    python scripts/dedup_customers.py --dry-run          # Report only
    python scripts/dedup_customers.py --execute           # Run all steps
    python scripts/dedup_customers.py --execute --step 3  # Run specific step
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

# ── Bootstrap ────────────────────────────────────────────────────────────
# Add project root to sys.path so we can import app modules.
sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Tables with customer_id FK ───────────────────────────────────────────
# (schema.table, customer_id_column, pk_column)
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

# external_sync uses local_entity_id (generic UUID), not customer_id
EXTERNAL_SYNC_TABLE = "ar.external_sync"


@dataclass
class MergeStats:
    """Track merge statistics."""

    fk_updates: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    customers_deleted: int = 0
    customers_merged: int = 0
    groups_processed: int = 0


@dataclass
class StepResult:
    """Result of a single step."""

    step: int
    name: str
    dry_run: bool
    details: dict = field(default_factory=dict)

    def log_summary(self) -> None:
        logger.info("=" * 60)
        mode = "DRY RUN" if self.dry_run else "EXECUTED"
        logger.info("Step %d: %s [%s]", self.step, self.name, mode)
        for k, v in self.details.items():
            logger.info("  %s: %s", k, v)
        logger.info("=" * 60)


# ── Helper: Reassign FKs ────────────────────────────────────────────────


def _reassign_fks(
    db: Session,
    winner_id: UUID,
    loser_id: UUID,
    stats: MergeStats,
    dry_run: bool,
) -> None:
    """Reassign all FK references from loser to winner across all tables."""
    for table, col, _pk in FK_TABLES:
        # For nullable FK tables (payee, project, ticket), this is safe.
        # For non-nullable FK tables, this is also safe (we're replacing one
        # valid customer_id with another).
        count_sql = text(f"SELECT count(*) FROM {table} WHERE {col} = :loser_id")
        count = db.execute(count_sql, {"loser_id": loser_id}).scalar() or 0
        if count > 0:
            if not dry_run:
                # Handle ar_aging_snapshot unique constraint:
                # (fiscal_period_id, customer_id, aging_bucket) must be unique.
                # If winner already has a snapshot for the same period+bucket,
                # we delete the loser's row instead of moving it.
                if table == "ar.ar_aging_snapshot":
                    _merge_aging_snapshots(db, winner_id, loser_id)
                else:
                    update_sql = text(
                        f"UPDATE {table} SET {col} = :winner_id WHERE {col} = :loser_id"
                    )
                    db.execute(
                        update_sql,
                        {"winner_id": winner_id, "loser_id": loser_id},
                    )
            stats.fk_updates[table] += count

    # external_sync: update local_entity_id
    count_sql = text(
        f"SELECT count(*) FROM {EXTERNAL_SYNC_TABLE} "
        f"WHERE local_entity_id = :loser_id AND entity_type = 'CUSTOMER'"
    )
    count = db.execute(count_sql, {"loser_id": loser_id}).scalar() or 0
    if count > 0:
        if not dry_run:
            update_sql = text(
                f"UPDATE {EXTERNAL_SYNC_TABLE} "
                f"SET local_entity_id = :winner_id "
                f"WHERE local_entity_id = :loser_id AND entity_type = 'CUSTOMER'"
            )
            db.execute(
                update_sql,
                {"winner_id": winner_id, "loser_id": loser_id},
            )
        stats.fk_updates[EXTERNAL_SYNC_TABLE] += count


def _merge_aging_snapshots(
    db: Session,
    winner_id: UUID,
    loser_id: UUID,
) -> None:
    """Merge aging snapshots, handling unique constraint conflicts."""
    # Delete loser snapshots that would conflict with winner's existing ones
    db.execute(
        text("""
        DELETE FROM ar.ar_aging_snapshot
        WHERE customer_id = :loser_id
          AND (fiscal_period_id, aging_bucket) IN (
              SELECT fiscal_period_id, aging_bucket
              FROM ar.ar_aging_snapshot
              WHERE customer_id = :winner_id
          )
    """),
        {"winner_id": winner_id, "loser_id": loser_id},
    )

    # Move remaining non-conflicting snapshots
    db.execute(
        text("""
        UPDATE ar.ar_aging_snapshot
        SET customer_id = :winner_id
        WHERE customer_id = :loser_id
    """),
        {"winner_id": winner_id, "loser_id": loser_id},
    )


def _delete_customer(db: Session, customer_id: UUID, dry_run: bool) -> None:
    """Delete a customer row."""
    if not dry_run:
        db.execute(
            text("DELETE FROM ar.customer WHERE customer_id = :cid"),
            {"cid": customer_id},
        )


# ── Step 1: Backfill erpnext_id and splynx_id ───────────────────────────


def step1_backfill_external_ids(db: Session, dry_run: bool) -> StepResult:
    """Backfill erpnext_id and splynx_id from existing data."""
    result = StepResult(step=1, name="Backfill external IDs", dry_run=dry_run)

    # Count Splynx customers to backfill
    splynx_count = (
        db.execute(
            text("""
        SELECT count(*) FROM ar.customer c
        JOIN ar.external_sync es
          ON es.local_entity_id = c.customer_id
         AND es.source = 'SPLYNX'
         AND es.entity_type = 'CUSTOMER'
        WHERE c.splynx_id IS NULL
    """)
        ).scalar()
        or 0
    )

    # Count ERPNext customers to backfill (codes that aren't SPLYNX- or CUST-)
    erpnext_count = (
        db.execute(
            text("""
        SELECT count(*) FROM ar.customer
        WHERE erpnext_id IS NULL
          AND customer_code NOT LIKE 'SPLYNX-%'
          AND customer_code NOT LIKE 'CUST-%'
    """)
        ).scalar()
        or 0
    )

    if not dry_run:
        # Backfill splynx_id from external_sync
        db.execute(
            text("""
            UPDATE ar.customer c
            SET splynx_id = es.external_id
            FROM ar.external_sync es
            WHERE es.local_entity_id = c.customer_id
              AND es.source = 'SPLYNX'
              AND es.entity_type = 'CUSTOMER'
              AND c.splynx_id IS NULL
        """)
        )

        # Backfill erpnext_id from customer_code for ERPNext-sourced customers
        db.execute(
            text("""
            UPDATE ar.customer
            SET erpnext_id = customer_code
            WHERE erpnext_id IS NULL
              AND customer_code NOT LIKE 'SPLYNX-%'
              AND customer_code NOT LIKE 'CUST-%'
        """)
        )

        db.flush()

    result.details = {
        "splynx_id backfilled": splynx_count,
        "erpnext_id backfilled": erpnext_count,
    }
    result.log_summary()
    return result


# ── Step 2: Merge Splynx-internal duplicates ─────────────────────────────


def step2_merge_splynx_internal_dupes(db: Session, dry_run: bool) -> StepResult:
    """Merge Splynx customers with the same legal_name."""
    result = StepResult(
        step=2, name="Merge Splynx-internal duplicates", dry_run=dry_run
    )
    stats = MergeStats()

    # Find groups of Splynx customers with duplicate legal_name
    dupe_groups = db.execute(
        text("""
        SELECT lower(trim(legal_name)) AS norm_name,
               count(*) AS cnt
        FROM ar.customer
        WHERE customer_code LIKE 'SPLYNX-%'
        GROUP BY lower(trim(legal_name))
        HAVING count(*) > 1
        ORDER BY count(*) DESC
    """)
    ).fetchall()

    logger.info("Found %d Splynx-internal duplicate groups", len(dupe_groups))

    for row in dupe_groups:
        norm_name = row[0]
        # Get all customers in this group, ordered by invoice count DESC,
        # then splynx_id ASC (tiebreaker)
        members = db.execute(
            text("""
            SELECT c.customer_id,
                   c.customer_code,
                   c.splynx_id,
                   c.legal_name,
                   (SELECT count(*) FROM ar.invoice i
                    WHERE i.customer_id = c.customer_id) AS inv_count
            FROM ar.customer c
            WHERE lower(trim(c.legal_name)) = :norm_name
              AND c.customer_code LIKE 'SPLYNX-%%'
            ORDER BY inv_count DESC, c.splynx_id ASC NULLS LAST
        """),
            {"norm_name": norm_name},
        ).fetchall()

        if len(members) < 2:
            continue

        winner = members[0]
        winner_id = winner[0]
        winner_splynx_id = winner[2]
        losers = members[1:]

        # Collect all splynx_ids for the merged record
        all_splynx_ids = [winner_splynx_id] if winner_splynx_id else []
        for loser in losers:
            if loser[2] and loser[2] not in all_splynx_ids:
                all_splynx_ids.append(loser[2])

        for loser in losers:
            loser_id = loser[0]
            _reassign_fks(db, winner_id, loser_id, stats, dry_run)
            _delete_customer(db, loser_id, dry_run)
            stats.customers_deleted += 1

        # Update winner's splynx_id to comma-separated list if multiple
        if len(all_splynx_ids) > 1 and not dry_run:
            merged_splynx_id = ",".join(str(s) for s in all_splynx_ids)
            db.execute(
                text("""
                UPDATE ar.customer
                SET splynx_id = :merged_id
                WHERE customer_id = :winner_id
            """),
                {"merged_id": merged_splynx_id[:100], "winner_id": winner_id},
            )

        stats.groups_processed += 1
        stats.customers_merged += 1

    if not dry_run:
        db.flush()

    result.details = {
        "duplicate groups": len(dupe_groups),
        "groups processed": stats.groups_processed,
        "customers deleted": stats.customers_deleted,
        "FK updates": dict(stats.fk_updates),
    }
    result.log_summary()
    return result


# ── Step 3: Merge ERPNext↔Splynx duplicates ──────────────────────────────


def step3_merge_erpnext_splynx_dupes(db: Session, dry_run: bool) -> StepResult:
    """Merge ERPNext customers that name-match Splynx customers."""
    result = StepResult(step=3, name="Merge ERPNext↔Splynx duplicates", dry_run=dry_run)
    stats = MergeStats()

    # Find ERPNext customers that match a Splynx customer by legal_name.
    # Winner = Splynx customer (has financial data).
    # We join on normalized legal_name.
    pairs = db.execute(
        text("""
        SELECT e.customer_id AS erpnext_cid,
               e.customer_code AS erpnext_code,
               e.erpnext_id,
               s.customer_id AS splynx_cid,
               s.customer_code AS splynx_code,
               s.splynx_id,
               e.legal_name
        FROM ar.customer e
        JOIN ar.customer s
          ON lower(trim(e.legal_name)) = lower(trim(s.legal_name))
         AND s.customer_code LIKE 'SPLYNX-%%'
        WHERE e.customer_code NOT LIKE 'SPLYNX-%%'
          AND e.customer_code NOT LIKE 'CUST-%%'
        ORDER BY e.legal_name
    """)
    ).fetchall()

    logger.info("Found %d ERPNext↔Splynx duplicate pairs", len(pairs))

    # Group by ERPNext customer (one ERPNext customer may match multiple
    # Splynx customers if Splynx had internal dupes that weren't caught
    # in step 2 — shouldn't happen after step 2, but be safe).
    erpnext_to_splynx: dict[UUID, list] = defaultdict(list)
    for pair in pairs:
        erpnext_cid = pair[0]
        erpnext_to_splynx[erpnext_cid].append(pair)

    for erpnext_cid, matches in erpnext_to_splynx.items():
        # Pick the first Splynx match as winner
        best = matches[0]
        winner_id = best[3]  # splynx_cid
        erpnext_id_value = best[2]  # erpnext_id from the ERPNext row
        loser_id = erpnext_cid

        # Copy erpnext_id onto the Splynx winner
        if not dry_run and erpnext_id_value:
            db.execute(
                text("""
                UPDATE ar.customer
                SET erpnext_id = :erpnext_id
                WHERE customer_id = :winner_id
                  AND erpnext_id IS NULL
            """),
                {"erpnext_id": erpnext_id_value, "winner_id": winner_id},
            )

        # Reassign FKs and delete loser
        _reassign_fks(db, winner_id, loser_id, stats, dry_run)
        _delete_customer(db, loser_id, dry_run)
        stats.customers_deleted += 1
        stats.customers_merged += 1

    if not dry_run:
        db.flush()

    result.details = {
        "duplicate pairs found": len(pairs),
        "ERPNext customers merged": len(erpnext_to_splynx),
        "customers deleted": stats.customers_deleted,
        "FK updates": dict(stats.fk_updates),
    }
    result.log_summary()
    return result


# ── Step 4: ERPNext-only customers (no action needed) ────────────────────


def step4_report_erpnext_only(db: Session, dry_run: bool) -> StepResult:
    """Report on ERPNext-only customers (no Splynx match). No merge needed."""
    result = StepResult(
        step=4, name="ERPNext-only customers (kept as-is)", dry_run=dry_run
    )

    erpnext_only = (
        db.execute(
            text("""
        SELECT count(*) FROM ar.customer
        WHERE customer_code NOT LIKE 'SPLYNX-%%'
          AND customer_code NOT LIKE 'CUST-%%'
    """)
        ).scalar()
        or 0
    )

    # Count those with linked data
    with_projects = (
        db.execute(
            text("""
        SELECT count(DISTINCT c.customer_id)
        FROM ar.customer c
        JOIN core_org.project p ON p.customer_id = c.customer_id
        WHERE c.customer_code NOT LIKE 'SPLYNX-%%'
          AND c.customer_code NOT LIKE 'CUST-%%'
    """)
        ).scalar()
        or 0
    )

    with_invoices = (
        db.execute(
            text("""
        SELECT count(DISTINCT c.customer_id)
        FROM ar.customer c
        JOIN ar.invoice i ON i.customer_id = c.customer_id
        WHERE c.customer_code NOT LIKE 'SPLYNX-%%'
          AND c.customer_code NOT LIKE 'CUST-%%'
    """)
        ).scalar()
        or 0
    )

    result.details = {
        "ERPNext-only customers remaining": erpnext_only,
        "with projects": with_projects,
        "with invoices": with_invoices,
    }
    result.log_summary()
    return result


# ── Step 5: Renumber all customers to CUST-NNNNN ─────────────────────────


def step5_renumber_customers(db: Session, dry_run: bool) -> StepResult:
    """Renumber all surviving customers with CUST-NNNNN codes."""
    result = StepResult(step=5, name="Renumber to CUST-NNNNN", dry_run=dry_run)

    # Get all customers ordered chronologically
    customers = db.execute(
        text("""
        SELECT customer_id, customer_code, created_at
        FROM ar.customer
        ORDER BY created_at ASC, customer_id ASC
    """)
    ).fetchall()

    total = len(customers)
    already_numbered = sum(1 for c in customers if c[1].startswith("CUST-"))
    to_renumber = total - already_numbered

    logger.info(
        "Total customers: %d, already CUST-: %d, to renumber: %d",
        total,
        already_numbered,
        to_renumber,
    )

    if not dry_run:
        for seq, row in enumerate(customers, start=1):
            new_code = f"CUST-{seq:05d}"
            db.execute(
                text("""
                UPDATE ar.customer
                SET customer_code = :new_code
                WHERE customer_id = :cid
            """),
                {"new_code": new_code, "cid": row[0]},
            )

        # Update numbering sequence to reflect final number
        db.execute(
            text("""
            UPDATE core_config.numbering_sequence
            SET current_number = :final_num,
                include_year = false,
                include_month = false,
                prefix = 'CUST',
                separator = '-',
                min_digits = 5,
                reset_frequency = 'NEVER'
            WHERE sequence_type = 'CUSTOMER'
        """),
            {"final_num": total},
        )

        # If no CUSTOMER sequence exists, create one
        affected = (
            db.execute(
                text("""
            SELECT count(*) FROM core_config.numbering_sequence
            WHERE sequence_type = 'CUSTOMER'
        """)
            ).scalar()
            or 0
        )

        if affected == 0:
            logger.warning(
                "No CUSTOMER numbering sequence found. "
                "It will be auto-created on next use by SyncNumberingService."
            )

        db.flush()

    # Sample first 5 and last 5 for verification
    sample_first = customers[:5] if len(customers) >= 5 else customers
    sample_last = customers[-5:] if len(customers) >= 5 else []

    result.details = {
        "total customers": total,
        "renumbered": to_renumber if not dry_run else f"{to_renumber} (would be)",
        "final sequence number": total,
        "first 5 (old codes)": [c[1] for c in sample_first],
        "last 5 (old codes)": [c[1] for c in sample_last],
    }
    result.log_summary()
    return result


# ── Step 6: Cleanup and verify ────────────────────────────────────────────


def step6_verify_integrity(db: Session, dry_run: bool) -> StepResult:
    """Verify FK integrity and report final stats."""
    result = StepResult(step=6, name="Verify integrity", dry_run=dry_run)
    orphans: dict[str, int] = {}

    # Check for orphaned FK references
    for table, col, _pk in FK_TABLES:
        # For nullable columns, only check non-null references
        nullable_check = (
            f"AND t.{col} IS NOT NULL"
            if table in ("banking.payee", "core_org.project", "support.ticket")
            else ""
        )

        count = (
            db.execute(
                text(f"""
            SELECT count(*)
            FROM {table} t
            LEFT JOIN ar.customer c ON c.customer_id = t.{col}
            WHERE c.customer_id IS NULL {nullable_check}
        """)
            ).scalar()
            or 0
        )

        if count > 0:
            orphans[table] = count

    # Final counts
    total_customers = db.execute(text("SELECT count(*) FROM ar.customer")).scalar() or 0
    cust_numbered = (
        db.execute(
            text("SELECT count(*) FROM ar.customer WHERE customer_code LIKE 'CUST-%%'")
        ).scalar()
        or 0
    )
    with_splynx = (
        db.execute(
            text("SELECT count(*) FROM ar.customer WHERE splynx_id IS NOT NULL")
        ).scalar()
        or 0
    )
    with_erpnext = (
        db.execute(
            text("SELECT count(*) FROM ar.customer WHERE erpnext_id IS NOT NULL")
        ).scalar()
        or 0
    )
    with_both = (
        db.execute(
            text(
                "SELECT count(*) FROM ar.customer "
                "WHERE splynx_id IS NOT NULL AND erpnext_id IS NOT NULL"
            )
        ).scalar()
        or 0
    )

    result.details = {
        "total customers": total_customers,
        "CUST-NNNNN numbered": cust_numbered,
        "with splynx_id": with_splynx,
        "with erpnext_id": with_erpnext,
        "with both IDs": with_both,
        "orphaned FK references": orphans if orphans else "NONE (clean)",
    }

    if orphans:
        logger.error("INTEGRITY CHECK FAILED — orphaned references found!")
    else:
        logger.info("Integrity check passed — no orphaned references.")

    result.log_summary()
    return result


# ── Main ──────────────────────────────────────────────────────────────────

STEPS = {
    1: step1_backfill_external_ids,
    2: step2_merge_splynx_internal_dupes,
    3: step3_merge_erpnext_splynx_dupes,
    4: step4_report_erpnext_only,
    5: step5_renumber_customers,
    6: step6_verify_integrity,
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Customer deduplication and renumbering"
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
        help="Actually run the migration",
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
        "Customer Dedup & Renumbering — mode=%s, steps=%s",
        "DRY RUN" if dry_run else "EXECUTE",
        steps_to_run,
    )

    with SessionLocal() as db:
        try:
            for step_num in steps_to_run:
                step_fn = STEPS[step_num]
                step_fn(db, dry_run)

            if not dry_run:
                logger.info("Committing all changes...")
                db.commit()
                logger.info("Done! All changes committed.")
            else:
                logger.info("Dry run complete — no changes made.")
                db.rollback()

        except Exception:
            logger.exception("Migration failed! Rolling back.")
            db.rollback()
            sys.exit(1)


if __name__ == "__main__":
    main()
