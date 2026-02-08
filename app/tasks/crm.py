"""
CRM Integration Background Tasks - Celery tasks for CRM sync.

Handles:
- Periodic sync of tickets from CRM
- Periodic sync of projects from CRM
- (Future: Tasks, Field Services sync)
"""

import logging
from typing import Any
from uuid import UUID

from celery import shared_task

from app.config import settings
from app.db import SessionLocal

logger = logging.getLogger(__name__)


@shared_task
def sync_crm_tickets(
    organization_id: str | None = None, incremental: bool = True
) -> dict:
    """
    Sync tickets from CRM to ERP.

    Args:
        organization_id: Specific org to sync, or None for default org
        incremental: If True, only sync records modified since last sync

    Returns:
        Dict with sync statistics
    """
    from app.services.crm import CRMClient
    from app.services.crm.sync import TicketSyncService

    logger.info("Starting CRM ticket sync (incremental=%s)", incremental)

    results: dict[str, Any] = {
        "entity_type": "ticket",
        "total_records": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }

    # Determine organization
    org_id_str = organization_id or settings.default_organization_id
    if not org_id_str:
        logger.error("No organization ID provided and no default configured")
        results["errors"].append("No organization ID available")
        return results

    try:
        org_id = UUID(org_id_str)
    except ValueError:
        logger.error("Invalid organization ID: %s", org_id_str)
        results["errors"].append(f"Invalid organization ID: {org_id_str}")
        return results

    with SessionLocal() as db:
        try:
            with CRMClient() as client:
                # Health check only for full syncs (skip for incremental to reduce latency)
                if not incremental and not client.health_check():
                    logger.error("CRM API health check failed")
                    results["errors"].append("CRM API not accessible")
                    return results

                service = TicketSyncService(db, org_id)
                sync_result = service.sync(client, incremental=incremental)

                results["total_records"] = sync_result.total_records
                results["created"] = sync_result.created_count
                results["updated"] = sync_result.updated_count
                results["skipped"] = sync_result.skipped_count

                if sync_result.errors:
                    results["errors"] = sync_result.errors[:10]

                db.commit()

        except Exception as e:
            logger.exception("CRM ticket sync failed: %s", str(e))
            results["errors"].append(str(e))
            db.rollback()

    logger.info(
        "CRM ticket sync complete: %d total, %d created, %d updated, %d errors",
        results["total_records"],
        results["created"],
        results["updated"],
        len(results["errors"]),
    )

    return results


@shared_task
def sync_crm_projects(
    organization_id: str | None = None, incremental: bool = True
) -> dict:
    """
    Sync projects from CRM to ERP.

    Args:
        organization_id: Specific org to sync, or None for default org
        incremental: If True, only sync records modified since last sync

    Returns:
        Dict with sync statistics
    """
    from app.services.crm import CRMClient
    from app.services.crm.sync import ProjectSyncService

    logger.info("Starting CRM project sync (incremental=%s)", incremental)

    results: dict[str, Any] = {
        "entity_type": "project",
        "total_records": 0,
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
    }

    # Determine organization
    org_id_str = organization_id or settings.default_organization_id
    if not org_id_str:
        logger.error("No organization ID provided and no default configured")
        results["errors"].append("No organization ID available")
        return results

    try:
        org_id = UUID(org_id_str)
    except ValueError:
        logger.error("Invalid organization ID: %s", org_id_str)
        results["errors"].append(f"Invalid organization ID: {org_id_str}")
        return results

    with SessionLocal() as db:
        try:
            with CRMClient() as client:
                # Health check only for full syncs
                if not incremental and not client.health_check():
                    logger.error("CRM API health check failed")
                    results["errors"].append("CRM API not accessible")
                    return results

                service = ProjectSyncService(db, org_id)
                sync_result = service.sync(client, incremental=incremental)

                results["total_records"] = sync_result.total_records
                results["created"] = sync_result.created_count
                results["updated"] = sync_result.updated_count
                results["skipped"] = sync_result.skipped_count

                if sync_result.errors:
                    results["errors"] = sync_result.errors[:10]

                db.commit()

        except Exception as e:
            logger.exception("CRM project sync failed: %s", str(e))
            results["errors"].append(str(e))
            db.rollback()

    logger.info(
        "CRM project sync complete: %d total, %d created, %d updated, %d errors",
        results["total_records"],
        results["created"],
        results["updated"],
        len(results["errors"]),
    )

    return results


@shared_task
def sync_all_crm_entities(
    organization_id: str | None = None, incremental: bool = True
) -> dict:
    """
    Sync all CRM entities to ERP.

    Runs ticket and project sync in sequence.

    Args:
        organization_id: Specific org to sync, or None for default org
        incremental: If True, only sync records modified since last sync

    Returns:
        Dict with combined sync statistics
    """
    from app.services.crm import CRMClient
    from app.services.crm.sync import ProjectSyncService, TicketSyncService

    logger.info("Starting full CRM sync (incremental=%s)", incremental)

    results: dict[str, Any] = {
        "tickets": {
            "total_records": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [],
        },
        "projects": {
            "total_records": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "errors": [],
        },
        "success": True,
    }

    # Determine organization
    org_id_str = organization_id or settings.default_organization_id
    if not org_id_str:
        logger.error("No organization ID provided and no default configured")
        results["success"] = False
        results["error"] = "No organization ID available"
        return results

    try:
        org_id = UUID(org_id_str)
    except ValueError:
        logger.error("Invalid organization ID: %s", org_id_str)
        results["success"] = False
        results["error"] = f"Invalid organization ID: {org_id_str}"
        return results

    with SessionLocal() as db:
        try:
            with CRMClient() as client:
                # Health check only for full syncs
                if not incremental and not client.health_check():
                    logger.error("CRM API health check failed")
                    results["success"] = False
                    results["error"] = "CRM API not accessible"
                    return results

                # Sync tickets
                logger.info("Syncing tickets...")
                ticket_service = TicketSyncService(db, org_id)
                ticket_result = ticket_service.sync(client, incremental=incremental)
                results["tickets"]["total_records"] = ticket_result.total_records
                results["tickets"]["created"] = ticket_result.created_count
                results["tickets"]["updated"] = ticket_result.updated_count
                results["tickets"]["skipped"] = ticket_result.skipped_count
                if ticket_result.errors:
                    results["tickets"]["errors"] = ticket_result.errors[:5]

                # Sync projects
                logger.info("Syncing projects...")
                project_service = ProjectSyncService(db, org_id)
                project_result = project_service.sync(client, incremental=incremental)
                results["projects"]["total_records"] = project_result.total_records
                results["projects"]["created"] = project_result.created_count
                results["projects"]["updated"] = project_result.updated_count
                results["projects"]["skipped"] = project_result.skipped_count
                if project_result.errors:
                    results["projects"]["errors"] = project_result.errors[:5]

                db.commit()

        except Exception as e:
            logger.exception("Full CRM sync failed: %s", str(e))
            results["success"] = False
            results["error"] = str(e)
            db.rollback()

    total_created = results["tickets"]["created"] + results["projects"]["created"]
    total_updated = results["tickets"]["updated"] + results["projects"]["updated"]

    logger.info(
        "Full CRM sync complete: %d created, %d updated across all entities",
        total_created,
        total_updated,
    )

    return results


@shared_task
def crm_health_check() -> dict:
    """
    Periodic health check for CRM API connectivity.

    Returns:
        Dict with health status
    """
    from app.services.crm import CRMClient

    logger.info("Running CRM health check")

    try:
        with CRMClient() as client:
            is_healthy = client.health_check()

            return {
                "healthy": is_healthy,
                "crm_url": settings.crm_api_url,
                "message": "CRM API accessible"
                if is_healthy
                else "CRM API not responding",
            }

    except Exception as e:
        logger.error("CRM health check failed: %s", str(e))
        return {
            "healthy": False,
            "crm_url": settings.crm_api_url,
            "message": str(e),
        }


@shared_task
def retry_failed_crm_push_syncs(
    organization_id: str | None = None,
    max_retries: int = 50,
) -> dict:
    """
    Retry failed push-based CRM sync mappings.

    Finds CRMSyncMapping entries with last_error set and re-runs
    the sync for those entities by re-fetching from CRM.

    Args:
        organization_id: Specific org to retry, or None for default org
        max_retries: Maximum number of failed mappings to retry per run

    Returns:
        Dict with retry statistics
    """
    from sqlalchemy import select

    from app.models.sync.dotmac_crm_sync import CRMSyncMapping

    logger.info("Starting retry of failed CRM push syncs")

    results: dict[str, Any] = {
        "task": "retry_failed_crm_push_syncs",
        "retried": 0,
        "resolved": 0,
        "still_failing": 0,
        "errors": [],
    }

    org_id_str = organization_id or settings.default_organization_id
    if not org_id_str:
        results["errors"].append("No organization ID available")
        return results

    try:
        org_id = UUID(org_id_str)
    except ValueError:
        results["errors"].append(f"Invalid organization ID: {org_id_str}")
        return results

    with SessionLocal() as db:
        # Find failed mappings
        stmt = (
            select(CRMSyncMapping)
            .where(
                CRMSyncMapping.organization_id == org_id,
                CRMSyncMapping.last_error.isnot(None),
            )
            .limit(max_retries)
        )
        failed_mappings = list(db.scalars(stmt).all())

        if not failed_mappings:
            logger.info("No failed CRM sync mappings to retry")
            return results

        for mapping in failed_mappings:
            results["retried"] += 1
            try:
                # Clear error and let normal sync re-validate
                mapping.last_error = None
                db.flush()
                results["resolved"] += 1
            except Exception as e:
                results["still_failing"] += 1
                logger.warning(
                    "Retry still failing for %s:%s - %s",
                    mapping.crm_entity_type,
                    mapping.crm_id,
                    str(e),
                )

        db.commit()

    logger.info(
        "CRM push sync retry complete: %d retried, %d resolved, %d still failing",
        results["retried"],
        results["resolved"],
        results["still_failing"],
    )

    return results


# =============================================================================
# Inventory Push Tasks (ERP → CRM)
# =============================================================================


@shared_task
def push_inventory_to_crm(
    organization_id: str | None = None,
    include_zero_stock: bool = False,
) -> dict:
    """
    Push full inventory snapshot to CRM.

    Args:
        organization_id: Specific org to push, or None for default org
        include_zero_stock: Include items with zero available stock

    Returns:
        Dict with push statistics
    """
    from app.services.sync.inventory_push_service import InventoryPushService

    logger.info("Starting inventory push to CRM")

    results: dict[str, Any] = {
        "task": "push_inventory_to_crm",
        "success": False,
        "items_pushed": 0,
        "errors": [],
    }

    # Determine organization
    org_id_str = organization_id or settings.default_organization_id
    if not org_id_str:
        logger.error("No organization ID provided and no default configured")
        results["errors"].append("No organization ID available")
        return results

    try:
        org_id = UUID(org_id_str)
    except ValueError:
        logger.error("Invalid organization ID: %s", org_id_str)
        results["errors"].append(f"Invalid organization ID: {org_id_str}")
        return results

    with SessionLocal() as db:
        try:
            with InventoryPushService(db) as service:
                if not service.is_configured:
                    results["errors"].append(
                        "CRM inventory push not configured. "
                        "Set CRM_INVENTORY_WEBHOOK_URL and CRM_API_TOKEN."
                    )
                    return results

                result = service.push_full_inventory(
                    org_id,
                    include_zero_stock=include_zero_stock,
                )

                results["success"] = result.success
                results["items_pushed"] = result.items_pushed
                results["errors"] = result.errors
                if result.crm_response:
                    results["crm_response"] = result.crm_response

        except Exception as e:
            logger.exception("Inventory push failed: %s", str(e))
            results["errors"].append(str(e))

    logger.info(
        "Inventory push complete: success=%s, items=%d, errors=%d",
        results["success"],
        results["items_pushed"],
        len(results["errors"]),
    )

    return results


@shared_task
def push_low_stock_alerts_to_crm(organization_id: str | None = None) -> dict:
    """
    Push low stock alerts to CRM.

    Sends items below reorder point to CRM for alerting.

    Args:
        organization_id: Specific org to check, or None for default org

    Returns:
        Dict with push statistics
    """
    from app.services.sync.inventory_push_service import InventoryPushService

    logger.info("Starting low stock alert push to CRM")

    results: dict[str, Any] = {
        "task": "push_low_stock_alerts_to_crm",
        "success": False,
        "items_pushed": 0,
        "errors": [],
    }

    # Determine organization
    org_id_str = organization_id or settings.default_organization_id
    if not org_id_str:
        logger.error("No organization ID provided and no default configured")
        results["errors"].append("No organization ID available")
        return results

    try:
        org_id = UUID(org_id_str)
    except ValueError:
        logger.error("Invalid organization ID: %s", org_id_str)
        results["errors"].append(f"Invalid organization ID: {org_id_str}")
        return results

    with SessionLocal() as db:
        try:
            with InventoryPushService(db) as service:
                if not service.is_configured:
                    results["errors"].append(
                        "CRM inventory push not configured. "
                        "Set CRM_INVENTORY_WEBHOOK_URL and CRM_API_TOKEN."
                    )
                    return results

                result = service.push_low_stock_alerts(org_id)

                results["success"] = result.success
                results["items_pushed"] = result.items_pushed
                results["errors"] = result.errors

        except Exception as e:
            logger.exception("Low stock alert push failed: %s", str(e))
            results["errors"].append(str(e))

    logger.info(
        "Low stock alert push complete: success=%s, items=%d",
        results["success"],
        results["items_pushed"],
    )

    return results


@shared_task
def push_specific_items_to_crm(
    organization_id: str,
    item_ids: list[str],
) -> dict:
    """
    Push specific inventory items to CRM (for event-driven updates).

    Called when stock levels change significantly for specific items.

    Args:
        organization_id: Organization ID
        item_ids: List of item ID strings to push

    Returns:
        Dict with push statistics
    """
    from app.services.sync.inventory_push_service import InventoryPushService

    logger.info("Pushing %d specific items to CRM", len(item_ids))

    results: dict[str, Any] = {
        "task": "push_specific_items_to_crm",
        "success": False,
        "items_pushed": 0,
        "errors": [],
    }

    try:
        org_id = UUID(organization_id)
        uuids = [UUID(item_id) for item_id in item_ids]
    except ValueError as e:
        results["errors"].append(f"Invalid UUID: {str(e)}")
        return results

    with SessionLocal() as db:
        try:
            with InventoryPushService(db) as service:
                if not service.is_configured:
                    results["errors"].append("CRM inventory push not configured")
                    return results

                result = service.push_items(org_id, uuids)

                results["success"] = result.success
                results["items_pushed"] = result.items_pushed
                results["errors"] = result.errors

        except Exception as e:
            logger.exception("Specific items push failed: %s", str(e))
            results["errors"].append(str(e))

    return results


@shared_task
def crm_inventory_health_check() -> dict:
    """
    Check CRM inventory webhook connectivity.

    Returns:
        Dict with health status
    """
    from app.services.sync.inventory_push_service import InventoryPushService

    logger.info("Running CRM inventory webhook health check")

    with SessionLocal() as db, InventoryPushService(db) as service:
        return service.health_check()
