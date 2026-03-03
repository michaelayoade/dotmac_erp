"""
Backfill 2025 tax returns from existing tax transactions.

For each (fiscal_period, jurisdiction) combination with transactions:
  1. Temporarily reopen the tax period (CLOSED → OPEN)
  2. Call TaxReturnService.auto_refresh_return()
  3. Re-close the period (OPEN → CLOSED)

Idempotent: auto_refresh_return() creates or updates DRAFT returns.
Safe to re-run.

Usage:
    docker exec dotmac_erp_app python -m scripts.backfill_2025_tax_returns
    # or locally:
    poetry run python -m scripts.backfill_2025_tax_returns
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import func, select

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    from app.db import SessionLocal
    from app.models.finance.tax.tax_period import TaxPeriod, TaxPeriodStatus
    from app.models.finance.tax.tax_transaction import TaxTransaction
    from app.services.finance.tax.tax_return import TaxReturnService

    # Hard-coded org ID — single-tenant deployment
    ORG_ID = UUID("fb1e43e4-23e0-4a70-a7c4-d59a4068b3f3")

    # System user for audit trail
    SYSTEM_USER_ID = ORG_ID  # fallback — no real user for backfill

    with SessionLocal() as db:
        # Find distinct (fiscal_period_id, jurisdiction_id) combos with transactions
        stmt = (
            select(
                TaxTransaction.fiscal_period_id,
                TaxTransaction.jurisdiction_id,
                func.count(TaxTransaction.transaction_id).label("txn_count"),
            )
            .where(TaxTransaction.organization_id == ORG_ID)
            .group_by(
                TaxTransaction.fiscal_period_id,
                TaxTransaction.jurisdiction_id,
            )
        )
        combos = db.execute(stmt).all()

        logger.info(
            "Found %d (period, jurisdiction) combinations with tax transactions",
            len(combos),
        )

        created = 0
        updated = 0
        skipped = 0
        errors = 0

        for fp_id, jur_id, txn_count in combos:
            # Look up tax period for this combo
            period = db.scalar(
                select(TaxPeriod).where(
                    TaxPeriod.organization_id == ORG_ID,
                    TaxPeriod.fiscal_period_id == fp_id,
                    TaxPeriod.jurisdiction_id == jur_id,
                )
            )

            if not period:
                logger.warning(
                    "No TaxPeriod for fiscal_period=%s jurisdiction=%s (%d txns) — skipping",
                    fp_id,
                    jur_id,
                    txn_count,
                )
                skipped += 1
                continue

            original_status = period.status

            # Temporarily reopen if closed
            if period.status in {
                TaxPeriodStatus.CLOSED,
                TaxPeriodStatus.FILED,
                TaxPeriodStatus.PAID,
            }:
                period.status = TaxPeriodStatus.OPEN
                db.flush()

            try:
                from app.models.finance.tax.tax_return import TaxReturn

                # Check if return already exists before calling
                existing = db.scalar(
                    select(TaxReturn).where(
                        TaxReturn.tax_period_id == period.period_id,
                        TaxReturn.organization_id == ORG_ID,
                    )
                )
                was_existing = existing is not None

                result = TaxReturnService.auto_refresh_return(
                    db,
                    organization_id=ORG_ID,
                    fiscal_period_id=fp_id,
                    jurisdiction_id=jur_id,
                    system_user_id=SYSTEM_USER_ID,
                )

                if result:
                    if was_existing:
                        updated += 1
                        logger.info(
                            "Updated return for %s (%d txns): output=%.2f input=%.2f net=%.2f",
                            period.period_name,
                            txn_count,
                            result.total_output_tax,
                            result.total_input_tax,
                            result.net_tax_payable,
                        )
                    else:
                        created += 1
                        logger.info(
                            "Created return for %s (%d txns): output=%.2f input=%.2f net=%.2f",
                            period.period_name,
                            txn_count,
                            result.total_output_tax,
                            result.total_input_tax,
                            result.net_tax_payable,
                        )
                else:
                    skipped += 1
                    logger.info("Skipped %s — no return needed", period.period_name)

            except Exception:
                logger.exception("Failed to process period %s", period.period_name)
                errors += 1
            finally:
                # Restore original status
                period.status = original_status
                db.flush()

            db.commit()

        logger.info(
            "Backfill complete: %d created, %d updated, %d skipped, %d errors",
            created,
            updated,
            skipped,
            errors,
        )


if __name__ == "__main__":
    main()
