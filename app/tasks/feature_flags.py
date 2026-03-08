"""Feature Flag Background Tasks."""

import logging
from typing import Any

from celery import shared_task

from app.db import SessionLocal

logger = logging.getLogger(__name__)


@shared_task
def archive_expired_feature_flags() -> dict:
    """Archive feature flags that have passed their expiry date.

    Returns:
        Dict with processing statistics.
    """
    logger.info("Checking for expired feature flags")

    results: dict[str, Any] = {"archived": 0, "errors": []}

    with SessionLocal() as db:
        from app.services.feature_flag_service import FeatureFlagService

        service = FeatureFlagService(db)
        expired = service.get_expired_flags()

        for flag in expired:
            try:
                service.archive_flag(flag.flag_key)
                results["archived"] += 1
                logger.info("Auto-archived expired flag: %s", flag.flag_key)
            except Exception as e:
                logger.exception("Failed to archive flag %s", flag.flag_key)
                results["errors"].append(str(e))

        db.commit()

    logger.info(
        "Feature flag archival complete: %s archived, %s errors",
        results["archived"],
        len(results["errors"]),
    )
    return results
