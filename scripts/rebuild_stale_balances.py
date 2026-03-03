"""One-off script: Rebuild all stale account balances after voided ERPNext invoice cleanup.

Usage:
    docker exec dotmac_erp_app python scripts/rebuild_stale_balances.py
"""

from __future__ import annotations

import logging
import sys
from uuid import UUID

from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def main() -> None:
    from app.db import SessionLocal
    from app.models.finance.gl.account_balance import AccountBalance
    from app.services.finance.gl.account_balance import AccountBalanceService

    with SessionLocal() as db:
        # Find all stale periods
        stmt = (
            select(AccountBalance.fiscal_period_id)
            .where(
                AccountBalance.organization_id == ORG_ID,
                AccountBalance.is_stale.is_(True),
            )
            .distinct()
        )
        stale_period_ids = list(db.scalars(stmt).all())
        logger.info("Found %d stale periods to rebuild", len(stale_period_ids))

        if not stale_period_ids:
            logger.info("Nothing to do — no stale balances")
            return

        total_created = 0
        for period_id in stale_period_ids:
            count = AccountBalanceService.rebuild_balances_for_period(
                db,
                organization_id=ORG_ID,
                fiscal_period_id=period_id,
            )
            logger.info("  Period %s: %d balance records created", period_id, count)
            total_created += count

        logger.info("Done. Total balance records created: %d", total_created)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.exception("Failed to rebuild balances")
        sys.exit(1)
