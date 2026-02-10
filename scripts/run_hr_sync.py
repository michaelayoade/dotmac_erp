#!/usr/bin/env python3
"""
Run ERPNext HR sync directly (not as a Celery task).
"""

import logging
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

# Suppress noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from app.db import SessionLocal
from app.models.finance.core_org.organization import Organization
from app.models.sync import IntegrationType
from app.services.erpnext.client import ERPNextConfig
from app.services.erpnext.sync.orchestrator import (
    ERPNextSyncOrchestrator,
    MigrationConfig,
    SyncType,
)

logger = logging.getLogger(__name__)


def _get_erpnext_config(db, org):
    """Get ERPNext config from integration settings."""
    from app.services.integration_config import IntegrationConfigService

    service = IntegrationConfigService(db)
    creds = service.get_decrypted_credentials(
        org.organization_id,
        IntegrationType.ERPNEXT,
    )

    if not creds:
        return None

    if (
        not creds.get("base_url")
        or not creds.get("api_key")
        or not creds.get("api_secret")
    ):
        return None

    return ERPNextConfig(
        url=creds["base_url"],
        api_key=creds["api_key"],
        api_secret=creds["api_secret"],
        company=creds.get("company"),
    )


def run_sync(org_code: str = "DEFAULT", entity_types: list[str] | None = None):
    """
    Run ERPNext sync.

    Args:
        org_code: Organization code to sync
        entity_types: List of entity types to sync
    """
    with SessionLocal() as db:
        # Find organization
        org = (
            db.query(Organization)
            .filter(Organization.organization_code == org_code)
            .first()
        )

        if not org:
            print(f"Organization not found: {org_code}")
            return

        print(f"Organization: {org.legal_name} ({org.organization_code})")

        config_data = _get_erpnext_config(db, org)
        if not config_data:
            print("ERPNext not configured for this organization")
            return

        print(f"ERPNext URL: {config_data.url}")
        print(f"Company: {config_data.company}")

        # Get admin user for audit
        from app.models.person import Person

        admin = db.query(Person).filter(Person.email == "admin@dotmac.ng").first()
        user_id = admin.id if admin else org.organization_id  # Fallback

        config = MigrationConfig(
            erpnext_url=config_data.url,
            erpnext_api_key=config_data.api_key,
            erpnext_api_secret=config_data.api_secret,
            erpnext_company=config_data.company,
            sync_type=SyncType.FULL,
            entity_types=entity_types,
        )

        print("\nStarting sync...")
        print(f"Entity types: {entity_types or 'all'}")
        print("-" * 50)

        orchestrator = ERPNextSyncOrchestrator(
            db=db,
            organization_id=org.organization_id,
            user_id=user_id,
            config=config,
        )

        try:
            history = orchestrator.run()
            db.commit()

            print("\n" + "=" * 50)
            print("=== Sync Complete ===")
            print("=" * 50)
            print(f"Status: {history.status.value}")
            print(f"Total: {history.total_records}")
            print(f"Synced: {history.synced_count}")
            print(f"Skipped: {history.skipped_count}")
            print(f"Errors: {history.error_count}")

            if hasattr(history, "details") and history.details:
                print("\n--- Details ---")
                for entity_type, stats in history.details.items():
                    if isinstance(stats, dict):
                        count = stats.get("synced", 0)
                        print(f"  {entity_type}: {count}")

            if history.errors:
                print("\n--- Errors (first 10) ---")
                for error in history.errors[:10]:
                    print(f"  - {error}")

        except Exception as e:
            logger.exception("Sync failed: %s", e)
            db.rollback()
            raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run ERPNext HR sync")
    parser.add_argument(
        "--org-code",
        default="DEFAULT",
        help="Organization code to sync",
    )
    parser.add_argument(
        "--entities",
        type=str,
        help="Comma-separated entity types (default: all HR entities)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Sync all entity types including legacy",
    )

    args = parser.parse_args()

    entity_types = None
    if args.entities:
        entity_types = [e.strip() for e in args.entities.split(",")]
    elif not args.all:
        # Default to HR entities only
        entity_types = [
            "departments",
            "designations",
            "employment_types",
            "employee_grades",
            "employees",
        ]

    run_sync(org_code=args.org_code, entity_types=entity_types)
