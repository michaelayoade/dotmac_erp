#!/usr/bin/env python3
"""
Setup Dotmac Technologies and sync expense data from ERPNext.

1. Create/update organization to "Dotmac Technologies"
2. Configure ERPNext integration
3. Sync expense categories and claims
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Suppress noisy loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from app.db import SessionLocal
from app.models.finance.core_org.organization import Organization
from app.models.person import Person
from app.models.sync import IntegrationType
from app.services.erpnext.sync.orchestrator import (
    ERPNextSyncOrchestrator,
    MigrationConfig,
    SyncType,
)
from app.services.integration_config import IntegrationConfigService

# ERPNext credentials (from existing config)
ERPNEXT_URL = "https://erp.dotmac.ng"
ERPNEXT_API_KEY = "REDACTED_API_KEY"
ERPNEXT_API_SECRET = "REDACTED_API_SECRET"
ERPNEXT_COMPANY = "Dotmac Limited"

ORG_CODE = "DOTMAC"
ORG_NAME = "Dotmac Technologies Ltd"


def setup_organization(db):
    """Create or update organization."""
    org = db.execute(
        select(Organization).where(Organization.organization_code == ORG_CODE)
    ).scalar_one_or_none()

    if org:
        print(f"Updating organization: {org.legal_name} -> {ORG_NAME}")
        org.legal_name = ORG_NAME
    else:
        print(f"Creating organization: {ORG_NAME}")
        from uuid import uuid4

        org = Organization(
            organization_id=uuid4(),
            organization_code=ORG_CODE,
            legal_name=ORG_NAME,
            functional_currency_code="NGN",
            presentation_currency_code="NGN",
            fiscal_year_end_month=12,
            fiscal_year_end_day=31,
            jurisdiction_country_code="NG",
            is_active=True,
        )
        db.add(org)

    db.flush()
    print(f"  Organization ID: {org.organization_id}")
    return org


def setup_erpnext_integration(db, org):
    """Configure ERPNext integration."""
    service = IntegrationConfigService(db)

    print("\nConfiguring ERPNext integration...")
    print(f"  URL: {ERPNEXT_URL}")
    print(f"  Company: {ERPNEXT_COMPANY}")

    existing = service.get_config(org.organization_id, IntegrationType.ERPNEXT)
    if existing:
        print("  Updating existing configuration...")
        config = service.update_credentials(
            org.organization_id,
            IntegrationType.ERPNEXT,
            api_key=ERPNEXT_API_KEY,
            api_secret=ERPNEXT_API_SECRET,
            base_url=ERPNEXT_URL,
            company=ERPNEXT_COMPANY,
        )
    else:
        print("  Creating new configuration...")
        config = service.create_config(
            organization_id=org.organization_id,
            integration_type=IntegrationType.ERPNEXT,
            base_url=ERPNEXT_URL,
            api_key=ERPNEXT_API_KEY,
            api_secret=ERPNEXT_API_SECRET,
            company=ERPNEXT_COMPANY,
        )

    db.flush()
    print(f"  Config ID: {config.config_id}")
    return config


def get_admin_user(db, org):
    """Get or create admin user for audit tracking."""
    admin = db.query(Person).filter(Person.email == "admin@dotmac.ng").first()
    if admin:
        return admin.id

    # Fallback to organization ID as UUID
    return org.organization_id


def run_expense_sync(db, org, user_id):
    """Sync expense data from ERPNext."""
    print("\n" + "=" * 60)
    print("STARTING EXPENSE SYNC")
    print("=" * 60)

    config = MigrationConfig(
        erpnext_url=ERPNEXT_URL,
        erpnext_api_key=ERPNEXT_API_KEY,
        erpnext_api_secret=ERPNEXT_API_SECRET,
        erpnext_company=ERPNEXT_COMPANY,
        sync_type=SyncType.FULL,
        entity_types=["expense_categories", "expense_claims"],
    )

    orchestrator = ERPNextSyncOrchestrator(
        db=db,
        organization_id=org.organization_id,
        user_id=user_id,
        config=config,
    )

    # Test connection first
    print("\nTesting ERPNext connection...")
    conn_result = orchestrator.test_connection()
    if not conn_result.get("success"):
        print(f"Connection failed: {conn_result.get('error')}")
        return None

    print(f"Connected as: {conn_result.get('user')}")

    # Preview
    print("\nPreviewing data to sync...")
    preview = orchestrator.preview()
    for _entity_type, info in preview.get("entity_types", {}).items():
        print(f"  {info['name']}: {info['count']} records")

    # Run sync
    print("\nSyncing expense data...")
    history = orchestrator.run()

    print("\n" + "=" * 60)
    print("SYNC COMPLETE")
    print("=" * 60)
    print(f"Status: {history.status.value}")
    print(f"Total records: {history.total_records}")
    print(f"Synced: {history.synced_count}")
    print(f"Skipped: {history.skipped_count}")
    print(f"Errors: {history.error_count}")

    if history.errors:
        print("\nErrors (first 10):")
        for error in history.errors[:10]:
            print(f"  - {error}")

    return history


def main():
    print("=" * 60)
    print("DOTMAC TECHNOLOGIES SETUP & EXPENSE SYNC")
    print("=" * 60)

    with SessionLocal() as db:
        try:
            # Step 1: Setup organization
            org = setup_organization(db)
            db.commit()

            # Step 2: Setup ERPNext integration
            setup_erpnext_integration(db, org)
            db.commit()

            # Step 3: Get admin user
            user_id = get_admin_user(db, org)

            # Step 4: Run expense sync
            run_expense_sync(db, org, user_id)
            db.commit()

            print("\n✓ Setup and sync complete!")

        except Exception as e:
            logger.exception("Setup failed: %s", e)
            db.rollback()
            raise


if __name__ == "__main__":
    main()
