"""
Backfill GL postings for historical inventory transactions.

Plain terms:
- Inventory transactions already exist in `inv.inventory_transaction`.
- Many of them were never posted into the general ledger.
- This script finds those transactions and posts them using the normal
  inventory posting adapter.

Usage:
    poetry run python -m scripts.backfill_inventory_gl_postings --dry-run
    poetry run python -m scripts.backfill_inventory_gl_postings --execute
    poetry run python -m scripts.backfill_inventory_gl_postings --execute --org-id <UUID>
    poetry run python -m scripts.backfill_inventory_gl_postings --execute --transaction-type RECEIPT
"""

from __future__ import annotations

import argparse
import calendar
import logging
import sys
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

sys.path.insert(0, ".")

from app.db.session_context import cross_org_session, session_for_org  # noqa: E402
from app.models.finance.core_org.organization import Organization  # noqa: E402
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus  # noqa: E402
from app.models.finance.gl.fiscal_year import FiscalYear  # noqa: E402
from app.models.inventory.inventory_transaction import (  # noqa: E402
    InventoryTransaction,
    TransactionType,
)
from app.services.inventory.inv_posting_adapter import inv_posting_adapter  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_inventory_gl")

SUPPORTED_TYPES = {
    TransactionType.RECEIPT,
    TransactionType.RETURN,
    TransactionType.ASSEMBLY,
    TransactionType.ISSUE,
    TransactionType.SALE,
    TransactionType.DISASSEMBLY,
    TransactionType.ADJUSTMENT,
    TransactionType.COUNT_ADJUSTMENT,
    TransactionType.SCRAP,
}


@dataclass
class PeriodStatusRestore:
    fiscal_period_id: UUID
    original_status: PeriodStatus


def count_missing_inventory_gl(
    db: Session,
    org_id: UUID | None = None,
) -> dict[str, int]:
    stmt = select(
        func.count().label("total"),
        func.count()
        .filter(InventoryTransaction.journal_entry_id.is_(None))
        .label("missing_gl"),
        func.count()
        .filter(
            InventoryTransaction.journal_entry_id.is_(None),
            InventoryTransaction.total_cost == 0,
        )
        .label("zero_cost_missing"),
    ).where(InventoryTransaction.transaction_type.in_(tuple(SUPPORTED_TYPES)))
    if org_id:
        stmt = stmt.where(InventoryTransaction.organization_id == org_id)

    total, missing_gl, zero_cost_missing = db.execute(stmt).one()
    return {
        "total": total or 0,
        "missing_gl": missing_gl or 0,
        "zero_cost_missing": zero_cost_missing or 0,
    }


def load_candidates(
    db: Session,
    batch_size: int,
    org_id: UUID | None = None,
    transaction_type: TransactionType | None = None,
) -> list[InventoryTransaction]:
    stmt = (
        select(InventoryTransaction)
        .where(
            InventoryTransaction.transaction_type.in_(tuple(SUPPORTED_TYPES)),
            InventoryTransaction.journal_entry_id.is_(None),
        )
        .order_by(
            InventoryTransaction.transaction_date.asc(),
            InventoryTransaction.created_at.asc(),
            InventoryTransaction.transaction_id.asc(),
        )
        .limit(batch_size)
    )
    if org_id:
        stmt = stmt.where(InventoryTransaction.organization_id == org_id)
    if transaction_type:
        stmt = stmt.where(InventoryTransaction.transaction_type == transaction_type)
    return list(db.scalars(stmt).all())


def _get_inventory_date_range(
    db: Session,
    org_id: UUID | None = None,
    transaction_type: TransactionType | None = None,
) -> tuple[date | None, date | None]:
    stmt = select(
        func.min(func.date(InventoryTransaction.transaction_date)),
        func.max(func.date(InventoryTransaction.transaction_date)),
    ).where(
        InventoryTransaction.transaction_type.in_(tuple(SUPPORTED_TYPES)),
        InventoryTransaction.journal_entry_id.is_(None),
    )
    if org_id:
        stmt = stmt.where(InventoryTransaction.organization_id == org_id)
    if transaction_type:
        stmt = stmt.where(InventoryTransaction.transaction_type == transaction_type)

    min_date, max_date = db.execute(stmt).one()
    return min_date, max_date


def prepare_inventory_fiscal_periods(
    db: Session,
    org_id: UUID | None = None,
    transaction_type: TransactionType | None = None,
) -> list[PeriodStatusRestore]:
    """
    Ensure inventory backfill dates have postable fiscal periods.

    Unlike the generic GL backfill script, this derives the date range from
    inventory transactions themselves.
    """
    min_date, max_date = _get_inventory_date_range(db, org_id, transaction_type)
    if not min_date or not max_date:
        return []

    logger.info("Preparing fiscal periods for date range %s to %s", min_date, max_date)

    if org_id:
        org_ids = [org_id]
    else:
        org_ids = list(
            db.scalars(
                select(InventoryTransaction.organization_id)
                .where(
                    InventoryTransaction.transaction_type.in_(tuple(SUPPORTED_TYPES)),
                    InventoryTransaction.journal_entry_id.is_(None),
                )
                .distinct()
            ).all()
        )

    restored: list[PeriodStatusRestore] = []

    for oid in org_ids:
        y, m = min_date.year, min_date.month
        end_y, end_m = max_date.year, max_date.month

        while (y, m) <= (end_y, end_m):
            month_start = date(y, m, 1)
            _, last_day = calendar.monthrange(y, m)
            month_end = date(y, m, last_day)

            period = db.scalar(
                select(FiscalPeriod).where(
                    FiscalPeriod.organization_id == oid,
                    FiscalPeriod.start_date <= month_start,
                    FiscalPeriod.end_date >= month_start,
                )
            )

            if period:
                if period.status not in PeriodStatus.accepts_postings():
                    restored.append(
                        PeriodStatusRestore(
                            fiscal_period_id=period.fiscal_period_id,
                            original_status=period.status,
                        )
                    )
                    period.status = PeriodStatus.OPEN
            else:
                fiscal_year = db.scalar(
                    select(FiscalYear).where(
                        FiscalYear.organization_id == oid,
                        FiscalYear.year_code == str(y),
                    )
                )
                if not fiscal_year:
                    fiscal_year = FiscalYear(
                        fiscal_year_id=uuid_lib.uuid4(),
                        organization_id=oid,
                        year_code=str(y),
                        year_name=f"Fiscal Year {y}",
                        start_date=date(y, 1, 1),
                        end_date=date(y, 12, 31),
                    )
                    db.add(fiscal_year)
                    db.flush()
                    logger.info("  Created fiscal year %s", y)

                period = FiscalPeriod(
                    fiscal_period_id=uuid_lib.uuid4(),
                    organization_id=oid,
                    fiscal_year_id=fiscal_year.fiscal_year_id,
                    period_number=m,
                    period_name=f"{calendar.month_name[m]} {y}",
                    start_date=month_start,
                    end_date=month_end,
                    status=PeriodStatus.OPEN,
                )
                db.add(period)
                logger.info("  Created fiscal period %s %s", calendar.month_name[m], y)

            m += 1
            if m > 12:
                m = 1
                y += 1

    db.flush()
    db.commit()

    if restored:
        logger.info(
            "  Temporarily opened %d non-postable periods for backfill", len(restored)
        )

    return restored


def restore_inventory_fiscal_periods(
    db: Session, restores: list[PeriodStatusRestore]
) -> None:
    if not restores:
        return

    for restore in restores:
        period = db.get(FiscalPeriod, restore.fiscal_period_id)
        if not period:
            continue
        period.status = restore.original_status

    db.commit()
    logger.info("Restored %d periods to their original status", len(restores))


def process_batch(
    db: Session,
    transactions: list[InventoryTransaction],
) -> dict[str, int]:
    posted = 0
    failed = 0
    skipped_zero_cost = 0

    for index, txn in enumerate(transactions, start=1):
        if (txn.total_cost or 0) == 0:
            skipped_zero_cost += 1
            continue

        savepoint = db.begin_nested()
        try:
            result = inv_posting_adapter.post_transaction(
                db=db,
                organization_id=txn.organization_id,
                transaction_id=txn.transaction_id,
                posting_date=txn.transaction_date.date(),
                posted_by_user_id=txn.created_by_user_id,
            )
            if result.success:
                posted += 1
                savepoint.commit()
            else:
                failed += 1
                savepoint.rollback()
                logger.error(
                    "FAILED %s %s: %s",
                    txn.transaction_type,
                    txn.transaction_id,
                    result.message,
                )
        except Exception as exc:
            failed += 1
            savepoint.rollback()
            logger.exception(
                "FAILED %s %s: %s",
                txn.transaction_type,
                txn.transaction_id,
                exc,
            )

        if index % 50 == 0:
            try:
                db.commit()
            except Exception as exc:
                db.rollback()
                logger.error("Commit failed after %d transactions: %s", index, exc)
            logger.info(
                "Processed %d/%d (%d posted, %d failed, %d skipped zero-cost)",
                index,
                len(transactions),
                posted,
                failed,
                skipped_zero_cost,
            )

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Final commit failed: %s", exc)

    return {
        "total": len(transactions),
        "posted": posted,
        "failed": failed,
        "skipped_zero_cost": skipped_zero_cost,
    }


def main(
    *,
    organization_id: UUID | None = None,
    args: argparse.Namespace | None = None,
    candidate_limit: int | None = None,
) -> int:
    if args is None:
        parser = argparse.ArgumentParser(
            description="Backfill GL postings for inventory transactions"
        )
        parser.add_argument("--dry-run", action="store_true", help="Report only")
        parser.add_argument("--execute", action="store_true", help="Post to GL")
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Max inventory transactions to process (default: 1000)",
        )
        parser.add_argument(
            "--org-id",
            type=str,
            default=None,
            help="Limit to one organization UUID",
        )
        parser.add_argument(
            "--transaction-type",
            choices=[t.value for t in SUPPORTED_TYPES],
            default=None,
            help="Limit to one inventory transaction type",
        )
        args = parser.parse_args()

        if not args.dry_run and not args.execute:
            parser.error("Specify --dry-run or --execute")

    org_id = organization_id or (UUID(args.org_id) if args.org_id else None)
    txn_type = TransactionType(args.transaction_type) if args.transaction_type else None
    requested_limit = args.batch_size if candidate_limit is None else candidate_limit
    effective_limit = min(requested_limit, 25) if args.dry_run else requested_limit
    if org_id is None:
        # Cross-tenant authority is limited to discovering organizations.
        # Each organization's report and writes use a fresh tenant session.
        with cross_org_session() as cross_db:
            org_ids = list(cross_db.scalars(select(Organization.organization_id)).all())
        remaining = effective_limit
        for target_org_id in org_ids:
            if remaining <= 0:
                break
            selected = main(
                organization_id=target_org_id,
                args=args,
                candidate_limit=remaining,
            )
            remaining -= selected
        return effective_limit - remaining

    with session_for_org(org_id) as db:
        logger.info("=" * 60)
        logger.info("INVENTORY GL BACKFILL REPORT")
        logger.info("=" * 60)

        counts = count_missing_inventory_gl(db, org_id)
        logger.info("  Supported inventory transactions: %d", counts["total"])
        logger.info("  Missing GL postings:            %d", counts["missing_gl"])
        logger.info("  Zero-cost rows (will skip):     %d", counts["zero_cost_missing"])
        logger.info("=" * 60)

        if args.dry_run:
            candidates = load_candidates(
                db,
                batch_size=effective_limit,
                org_id=org_id,
                transaction_type=txn_type,
            )
            for txn in candidates:
                logger.info(
                    "  %s | %s | %s | total_cost=%s | ref=%s",
                    txn.transaction_date.date(),
                    txn.transaction_type.value,
                    txn.transaction_id,
                    txn.total_cost,
                    txn.reference,
                )
            logger.info("DRY RUN — no changes made.")
            return len(candidates)

        if counts["missing_gl"] == 0:
            logger.info(
                "Nothing to do — all inventory transactions are already posted."
            )
            return 0

        reopened_periods = prepare_inventory_fiscal_periods(db, org_id, txn_type)

        try:
            candidates = load_candidates(
                db,
                batch_size=effective_limit,
                org_id=org_id,
                transaction_type=txn_type,
            )
            result = process_batch(db, candidates)
            logger.info("-" * 60)
            logger.info("Backfill complete")
            logger.info("  Selected:           %d", result["total"])
            logger.info("  Posted:             %d", result["posted"])
            logger.info("  Failed:             %d", result["failed"])
            logger.info("  Skipped zero-cost:  %d", result["skipped_zero_cost"])
            logger.info("-" * 60)
        finally:
            restore_inventory_fiscal_periods(db, reopened_periods)

        return result["total"]


if __name__ == "__main__":
    main()
