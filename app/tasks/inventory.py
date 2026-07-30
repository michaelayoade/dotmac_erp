"""Inventory background tasks."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from app.db.session_context import cross_org_session, session_for_org
from app.models.finance.core_org.organization import Organization
from app.services.sync.dotmac_crm_sync_service import DotMacCRMSyncService

logger = logging.getLogger(__name__)


def _list_active_organization_ids() -> list[UUID]:
    with cross_org_session() as db:
        return list(
            db.scalars(
                select(Organization.organization_id).where(
                    Organization.is_active.is_(True)
                )
            ).all()
        )


@shared_task
def auto_issue_pending_stock_material_requests(
    limit_per_org: int = 100,
) -> dict[str, Any]:
    """Issue CRM material requests that were waiting for stock and notify requesters."""
    results: dict[str, Any] = {
        "organizations_checked": 0,
        "checked": 0,
        "issued": 0,
        "still_pending": 0,
        "notifications_sent": 0,
        "errors": [],
    }

    for org_id in _list_active_organization_ids():
        results["organizations_checked"] += 1
        try:
            with session_for_org(org_id) as db:
                org_result = DotMacCRMSyncService(
                    db
                ).process_pending_stock_material_requests(
                    org_id,
                    limit=limit_per_org,
                )
                db.commit()

            results["checked"] += org_result["checked"]
            results["issued"] += org_result["issued"]
            results["still_pending"] += org_result["still_pending"]
            results["notifications_sent"] += org_result["notifications_sent"]
            results["errors"].extend(org_result["errors"])
        except Exception as exc:
            logger.exception(
                "Failed to process pending-stock material requests for organization %s",
                org_id,
            )
            results["errors"].append(
                {"organization_id": str(org_id), "error": str(exc)}
            )

    return results


@shared_task
def send_low_stock_notifications() -> dict[str, Any]:
    """Send daily low-stock summaries to authorized inventory users."""
    results: dict[str, Any] = {
        "organizations_checked": 0,
        "low_stock_items": 0,
        "notifications_sent": 0,
        "errors": [],
    }

    for org_id in _list_active_organization_ids():
        results["organizations_checked"] += 1
        try:
            with session_for_org(org_id) as db:
                from app.services.inventory.notifications import (
                    InventoryNotificationService,
                )

                org_result = InventoryNotificationService(db).notify_low_stock(org_id)
                db.commit()

            results["low_stock_items"] += org_result["low_stock_items"]
            results["notifications_sent"] += org_result["notifications_sent"]
        except Exception as exc:
            logger.exception(
                "Failed to send low-stock notifications for organization %s",
                org_id,
            )
            results["errors"].append(
                {"organization_id": str(org_id), "error": str(exc)}
            )

    return results
