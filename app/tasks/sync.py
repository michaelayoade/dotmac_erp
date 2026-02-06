"""
ERPNext Sync Tasks - Celery background tasks for data synchronization.

These tasks handle:
- Full sync of all entities from ERPNext
- Incremental sync of modified records
- Entity-specific sync for retrying failures
- Outbound sync to push changes back to ERPNext
"""

import logging
import uuid
from typing import Optional

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.sync import SyncHistory, SyncType, IntegrationType
from app.models.finance.core_org.organization import Organization
from app.services.erpnext.client import ERPNextClient, ERPNextConfig
from app.services.erpnext.sync.orchestrator import (
    ERPNextSyncOrchestrator,
    MigrationConfig,
)

logger = logging.getLogger(__name__)


def _get_erpnext_config(db: "Session", org: Organization) -> Optional[ERPNextConfig]:
    """
    Get ERPNext configuration from IntegrationConfig table.

    Looks up ERPNext integration settings for the organization.
    Decrypts credentials if they are encrypted.
    Returns None if ERPNext integration is not configured.
    """
    from app.services.integration_config import IntegrationConfigService

    service = IntegrationConfigService(db)
    creds = service.get_decrypted_credentials(
        org.organization_id,
        IntegrationType.ERPNEXT,
    )

    if not creds:
        return None

    if not creds["base_url"] or not creds["api_key"] or not creds["api_secret"]:
        return None

    return ERPNextConfig(
        url=creds["base_url"],
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        company=creds.get("company"),
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def run_full_erpnext_sync(
    self,
    organization_id: str,
    user_id: str,
    entity_types: Optional[list[str]] = None,
) -> dict:
    """
    Run full ERPNext sync for an organization.

    Args:
        organization_id: UUID of the organization
        user_id: UUID of the user initiating the sync
        entity_types: Optional list of entity types to sync (default: all)

    Returns:
        Dict with sync statistics
    """
    logger.info(
        "Starting full ERPNext sync for organization %s",
        organization_id,
    )

    with SessionLocal() as db:
        org = db.get(Organization, uuid.UUID(organization_id))
        if not org:
            logger.error("Organization not found: %s", organization_id)
            return {"success": False, "error": "Organization not found"}

        config_data = _get_erpnext_config(db, org)
        if not config_data:
            logger.warning(
                "ERPNext not configured for organization %s",
                organization_id,
            )
            return {"success": False, "error": "ERPNext not configured"}

        config = MigrationConfig(
            erpnext_url=config_data.url,
            erpnext_api_key=config_data.api_key,
            erpnext_api_secret=config_data.api_secret,
            erpnext_company=config_data.company,
            sync_type=SyncType.FULL,
            entity_types=entity_types,
        )

        orchestrator = ERPNextSyncOrchestrator(
            db=db,
            organization_id=uuid.UUID(organization_id),
            user_id=uuid.UUID(user_id),
            config=config,
        )

        try:
            history = orchestrator.run()
            db.commit()

            return {
                "success": True,
                "history_id": str(history.history_id),
                "total_records": history.total_records,
                "synced_count": history.synced_count,
                "error_count": history.error_count,
                "status": history.status.value,
            }

        except Exception as e:
            logger.exception("Full sync failed: %s", e)
            db.rollback()
            raise self.retry(exc=e)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def run_incremental_erpnext_sync(
    self,
    organization_id: str,
    user_id: str,
    entity_types: Optional[list[str]] = None,
) -> dict:
    """
    Run incremental ERPNext sync - only sync records modified since last sync.

    Args:
        organization_id: UUID of the organization
        user_id: UUID of the user initiating the sync
        entity_types: Optional list of entity types to sync

    Returns:
        Dict with sync statistics
    """
    logger.info(
        "Starting incremental ERPNext sync for organization %s",
        organization_id,
    )

    with SessionLocal() as db:
        org = db.get(Organization, uuid.UUID(organization_id))
        if not org:
            return {"success": False, "error": "Organization not found"}

        config_data = _get_erpnext_config(db, org)
        if not config_data:
            return {"success": False, "error": "ERPNext not configured"}

        config = MigrationConfig(
            erpnext_url=config_data.url,
            erpnext_api_key=config_data.api_key,
            erpnext_api_secret=config_data.api_secret,
            erpnext_company=config_data.company,
            sync_type=SyncType.INCREMENTAL,
            entity_types=entity_types,
        )

        orchestrator = ERPNextSyncOrchestrator(
            db=db,
            organization_id=uuid.UUID(organization_id),
            user_id=uuid.UUID(user_id),
            config=config,
        )

        try:
            history = orchestrator.run()
            db.commit()

            return {
                "success": True,
                "history_id": str(history.history_id),
                "total_records": history.total_records,
                "synced_count": history.synced_count,
                "error_count": history.error_count,
            }

        except Exception as e:
            logger.exception("Incremental sync failed: %s", e)
            db.rollback()
            raise self.retry(exc=e)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def sync_single_entity_type(
    self,
    organization_id: str,
    user_id: str,
    entity_type: str,
    incremental: bool = True,
) -> dict:
    """
    Sync a single entity type from ERPNext.

    Useful for:
    - Retrying failed entity types
    - Testing sync configuration
    - On-demand refresh of specific data

    Args:
        organization_id: UUID of the organization
        user_id: UUID of the user
        entity_type: The entity type to sync (e.g., "employees", "departments")
        incremental: Whether to do incremental sync

    Returns:
        Dict with sync statistics
    """
    logger.info(
        "Syncing %s for organization %s",
        entity_type,
        organization_id,
    )

    with SessionLocal() as db:
        org = db.get(Organization, uuid.UUID(organization_id))
        if not org:
            return {"success": False, "error": "Organization not found"}

        config_data = _get_erpnext_config(db, org)
        if not config_data:
            return {"success": False, "error": "ERPNext not configured"}

        config = MigrationConfig(
            erpnext_url=config_data.url,
            erpnext_api_key=config_data.api_key,
            erpnext_api_secret=config_data.api_secret,
            erpnext_company=config_data.company,
            sync_type=SyncType.INCREMENTAL if incremental else SyncType.FULL,
            entity_types=[entity_type],
        )

        orchestrator = ERPNextSyncOrchestrator(
            db=db,
            organization_id=uuid.UUID(organization_id),
            user_id=uuid.UUID(user_id),
            config=config,
        )

        try:
            result = orchestrator.run_single(entity_type)
            db.commit()

            return {
                "success": True,
                "entity_type": entity_type,
                "total_records": result.total_records,
                "synced_count": result.synced_count,
                "skipped_count": result.skipped_count,
                "error_count": result.error_count,
            }

        except Exception as e:
            logger.exception("Single entity sync failed: %s", e)
            db.rollback()
            raise self.retry(exc=e)


@shared_task
def scheduled_hr_sync() -> dict:
    """
    Scheduled task to sync HR data from ERPNext for all configured organizations.

    This task runs on a schedule (e.g., every hour) to keep HR data current.
    Only syncs HR-related entities: employees, departments, leave, attendance.

    Organizations are found by looking at successful SyncHistory records,
    meaning they must have completed at least one manual sync first.
    """
    hr_entity_types = [
        "departments",
        "designations",
        "employment_types",
        "employee_grades",
        "employees",
        "leave_types",
        "shift_types",
        "leave_allocations",
        "leave_applications",
        "attendance",
    ]

    results = []

    with SessionLocal() as db:
        # Find organizations that have successfully synced from ERPNext before
        # Uses subquery to get the most recent sync per organization
        from sqlalchemy import func as sqlfunc

        recent_syncs = db.execute(
            select(
                SyncHistory.organization_id,
                SyncHistory.created_by_user_id,
                sqlfunc.max(SyncHistory.started_at).label("last_sync"),
            )
            .where(
                SyncHistory.source_system == "erpnext",
                SyncHistory.status.in_(["COMPLETED", "COMPLETED_WITH_ERRORS"]),
            )
            .group_by(SyncHistory.organization_id, SyncHistory.created_by_user_id)
        ).all()

        for row in recent_syncs:
            org_id = row.organization_id
            user_id = row.created_by_user_id

            org = db.get(Organization, org_id)
            if not org or not org.is_active:
                continue

            config_data = _get_erpnext_config(db, org)
            if not config_data:
                continue

            try:
                # Dispatch incremental sync task
                task = run_incremental_erpnext_sync.delay(
                    str(org_id),
                    str(user_id),
                    hr_entity_types,
                )
                results.append(
                    {
                        "organization_id": str(org_id),
                        "task_id": task.id,
                    }
                )
            except Exception as e:
                logger.error(
                    "Failed to dispatch HR sync for org %s: %s",
                    org_id,
                    e,
                )
                results.append(
                    {
                        "organization_id": str(org_id),
                        "error": str(e),
                    }
                )

    return {"organizations_processed": len(results), "results": results}


@shared_task
def scheduled_expense_sync() -> dict:
    """
    Scheduled task to sync expense data from ERPNext.

    Runs less frequently than HR sync since expense data changes less often.

    Organizations are found by looking at successful SyncHistory records,
    meaning they must have completed at least one manual sync first.
    """
    expense_entity_types = [
        "expense_categories",
        "expense_claims",
        "projects",
        "tickets",
    ]

    results = []

    with SessionLocal() as db:
        from sqlalchemy import func as sqlfunc

        # Find organizations that have successfully synced from ERPNext before
        recent_syncs = db.execute(
            select(
                SyncHistory.organization_id,
                SyncHistory.created_by_user_id,
                sqlfunc.max(SyncHistory.started_at).label("last_sync"),
            )
            .where(
                SyncHistory.source_system == "erpnext",
                SyncHistory.status.in_(["COMPLETED", "COMPLETED_WITH_ERRORS"]),
            )
            .group_by(SyncHistory.organization_id, SyncHistory.created_by_user_id)
        ).all()

        for row in recent_syncs:
            org_id = row.organization_id
            user_id = row.created_by_user_id

            org = db.get(Organization, org_id)
            if not org or not org.is_active:
                continue

            config_data = _get_erpnext_config(db, org)
            if not config_data:
                continue

            try:
                task = run_incremental_erpnext_sync.delay(
                    str(org_id),
                    str(user_id),
                    expense_entity_types,
                )
                results.append(
                    {
                        "organization_id": str(org_id),
                        "task_id": task.id,
                    }
                )
            except Exception as e:
                logger.error(
                    "Failed to dispatch expense sync for org %s: %s",
                    org_id,
                    e,
                )

    return {"organizations_processed": len(results), "results": results}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def push_expense_claim_to_erpnext(
    self,
    organization_id: str,
    user_id: str,
    claim_id: str,
    submit: bool = False,
) -> dict:
    """
    Push a single expense claim to ERPNext.

    Used when creating/updating expense claims in DotMac to sync back to ERPNext.

    Args:
        organization_id: UUID of the organization
        user_id: UUID of the user
        claim_id: UUID of the expense claim to push
        submit: Whether to submit the claim in ERPNext (for approval workflow)

    Returns:
        Dict with result
    """
    from app.models.expense.expense_claim import ExpenseClaim
    from app.services.erpnext.export.expense import ExpenseClaimExportService

    logger.info(
        "Pushing expense claim %s to ERPNext",
        claim_id,
    )

    with SessionLocal() as db:
        org = db.get(Organization, uuid.UUID(organization_id))
        if not org:
            return {"success": False, "error": "Organization not found"}

        config_data = _get_erpnext_config(db, org)
        if not config_data:
            return {"success": False, "error": "ERPNext not configured"}

        claim = db.get(ExpenseClaim, uuid.UUID(claim_id))
        if not claim:
            return {"success": False, "error": "Expense claim not found"}

        client = ERPNextClient(config_data)
        try:
            export_service = ExpenseClaimExportService(
                db=db,
                client=client,
                organization_id=uuid.UUID(organization_id),
                user_id=uuid.UUID(user_id),
                company=config_data.company or "",
            )

            success, error = export_service.export_single(claim)

            if success and submit:
                # Submit for approval in ERPNext
                success, error = export_service.submit_claim(claim)

            db.commit()

            return {
                "success": success,
                "erpnext_id": claim.erpnext_id,
                "error": error,
            }

        except Exception as e:
            logger.exception("Failed to push expense claim: %s", e)
            db.rollback()
            raise self.retry(exc=e)

        finally:
            client.close()
