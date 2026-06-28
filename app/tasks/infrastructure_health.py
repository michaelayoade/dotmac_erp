from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.services.infrastructure_health import run_infrastructure_health_checks

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.infrastructure_health.run_infrastructure_health_checks"
)
def run_infrastructure_health_checks_task() -> dict:
    """Collect infrastructure health and maintain persistent alerts."""
    result = run_infrastructure_health_checks()
    logger.info("Infrastructure health checks completed: %s", result)
    return result
