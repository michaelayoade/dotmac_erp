"""
Exchange Rate Background Tasks — daily auto-fetch of FX rates.

Handles:
- Fetching latest exchange rates from the Currency API for all organizations
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from sqlalchemy import select

from app.db import SessionLocal
from app.models.finance.core_org.organization import Organization

logger = logging.getLogger(__name__)


@shared_task
def fetch_daily_exchange_rates() -> dict[str, Any]:
    """Fetch exchange rates for all organizations.

    Iterates every org, calls the ExchangeRateFetcher per org.

    Returns:
        Dict with processing statistics.
    """
    from app.services.finance.platform.ecb_rate_fetcher import ExchangeRateFetcher

    logger.info("Starting daily exchange-rate fetch")

    results: dict[str, Any] = {
        "orgs_processed": 0,
        "total_created": 0,
        "total_updated": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        org_ids = list(db.scalars(select(Organization.organization_id)).all())

        fetcher = ExchangeRateFetcher()

        for org_id in org_ids:
            try:
                result = fetcher.fetch_latest_rates(db, org_id)
                results["orgs_processed"] += 1
                results["total_created"] += result.rates_created
                results["total_updated"] += result.rates_updated
                if result.errors:
                    results["errors"].extend(f"[{org_id}] {e}" for e in result.errors)
            except Exception as exc:
                logger.exception("FX fetch failed for org %s", org_id)
                results["errors"].append(f"[{org_id}] {exc}")

        db.commit()

    logger.info(
        "Daily FX fetch complete: %d orgs, %d created, %d updated, %d errors",
        results["orgs_processed"],
        results["total_created"],
        results["total_updated"],
        len(results["errors"]),
    )
    return results
