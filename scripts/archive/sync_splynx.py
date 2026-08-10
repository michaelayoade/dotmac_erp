#!/usr/bin/env python
"""
Splynx Sync Script.

Syncs customers, invoices, payments, and credit notes from Splynx
to Dotmac ERP. Supports incremental sync with batch processing.

Usage:
    # Test connection
    python scripts/sync_splynx.py --test

    # Sync all data from 2022 (in batches of 500)
    python scripts/sync_splynx.py --org-id <uuid> --ar-account <uuid> --batch-size 500

    # Sync only customers
    python scripts/sync_splynx.py --org-id <uuid> --ar-account <uuid> --customers-only

    # Sync only invoices from specific date range
    python scripts/sync_splynx.py --org-id <uuid> --ar-account <uuid> --invoices-only --from-date 2024-01-01

    # Force re-sync (ignore change detection)
    python scripts/sync_splynx.py --org-id <uuid> --ar-account <uuid> --force
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime
from uuid import UUID

# Setup path for imports
sys.path.insert(0, "/root/dotmac")

from app.db import SessionLocal
from app.services.splynx import SplynxClient, SplynxConfig, SplynxSyncService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def test_connection() -> bool:
    """Test Splynx API connection."""
    config = SplynxConfig.from_settings()

    if not config.is_configured():
        logger.error(
            "Splynx not configured. Set SPLYNX_API_KEY and SPLYNX_API_SECRET in .env"
        )
        return False

    logger.info("Testing connection to %s...", config.api_url)

    with SplynxClient(config) as client:
        if client.test_connection():
            logger.info("✓ Connection successful!")

            # Show some stats
            logger.info("Fetching sample data...")
            customers = list(client.get_customers())[:5]
            logger.info("  Found %d+ customers", len(customers))

            return True
        else:
            logger.error("✗ Connection failed!")
            return False


def run_sync(
    organization_id: UUID,
    ar_control_account_id: UUID,
    revenue_account_id: UUID | None,
    from_date: date | None,
    to_date: date | None,
    batch_size: int | None = None,
    force: bool = False,
    dry_run: bool = False,
    entity_types: list[str] | None = None,
) -> dict:
    """Run the sync with specified options."""
    logger.info("=" * 60)
    logger.info("SPLYNX SYNC")
    logger.info("=" * 60)
    logger.info("  Organization: %s", organization_id)
    logger.info("  AR Control Account: %s", ar_control_account_id)
    logger.info("  Revenue Account: %s", revenue_account_id)
    logger.info("  Date range: %s to %s", from_date or "beginning", to_date or "now")
    logger.info("  Batch size: %s", batch_size or "unlimited")
    logger.info("  Force re-sync: %s", force)
    logger.info("  Entity types: %s", entity_types or "all")
    logger.info("=" * 60)

    skip_unchanged = not force
    results = {}

    with SessionLocal() as db:
        service = SplynxSyncService(
            db=db,
            organization_id=organization_id,
            ar_control_account_id=ar_control_account_id,
            default_revenue_account_id=revenue_account_id,
        )

        try:
            # Sync in dependency order
            if not entity_types or "customers" in entity_types:
                logger.info("\n>>> Syncing CUSTOMERS...")
                result = service.sync_customers(
                    date_from=from_date,
                    date_to=to_date,
                    batch_size=batch_size,
                    skip_unchanged=skip_unchanged,
                )
                results["customers"] = result.to_dict()
                logger.info("    %s", result.message)

            if not entity_types or "invoices" in entity_types:
                logger.info("\n>>> Syncing INVOICES...")
                result = service.sync_invoices(
                    date_from=from_date,
                    date_to=to_date,
                    batch_size=batch_size,
                    skip_unchanged=skip_unchanged,
                )
                results["invoices"] = result.to_dict()
                logger.info("    %s", result.message)

            if not entity_types or "payments" in entity_types:
                logger.info("\n>>> Syncing PAYMENTS...")
                result = service.sync_payments(
                    date_from=from_date,
                    date_to=to_date,
                    batch_size=batch_size,
                    skip_unchanged=skip_unchanged,
                )
                results["payments"] = result.to_dict()
                logger.info("    %s", result.message)

            if not entity_types or "credit_notes" in entity_types:
                logger.info("\n>>> Syncing CREDIT NOTES...")
                result = service.sync_credit_notes(
                    date_from=from_date,
                    date_to=to_date,
                )
                results["credit_notes"] = result.to_dict()
                logger.info("    %s", result.message)

            if dry_run:
                logger.info("\n[DRY RUN] Rolling back changes...")
                db.rollback()
            else:
                logger.info("\nCommitting changes...")
                db.commit()
                logger.info("✓ Sync completed successfully!")

        except Exception as e:
            logger.exception("Sync failed: %s", e)
            db.rollback()
            results["error"] = str(e)
        finally:
            service.close()

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Sync data from Splynx to Dotmac ERP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test API connection
  python scripts/sync_splynx.py --test

  # Sync all from 2022 in batches
  python scripts/sync_splynx.py --org-id UUID --ar-account UUID --batch-size 500

  # Sync only new/changed customers
  python scripts/sync_splynx.py --org-id UUID --ar-account UUID --customers-only

  # Force re-sync everything (ignore change detection)
  python scripts/sync_splynx.py --org-id UUID --ar-account UUID --force
        """,
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Test API connection only",
    )
    parser.add_argument(
        "--org-id",
        type=str,
        help="Organization UUID (required for sync)",
    )
    parser.add_argument(
        "--ar-account",
        type=str,
        help="AR Control Account UUID (required for sync)",
    )
    parser.add_argument(
        "--revenue-account",
        type=str,
        help="Default Revenue Account UUID (optional)",
    )
    parser.add_argument(
        "--from-date",
        type=str,
        help="Start date for sync (YYYY-MM-DD), default: 2022-01-01",
        default="2022-01-01",
    )
    parser.add_argument(
        "--to-date",
        type=str,
        help="End date for sync (YYYY-MM-DD), default: today",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        help="Max records per entity type (enables incremental sync)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-sync even if records haven't changed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run sync but don't commit changes",
    )
    parser.add_argument(
        "--customers-only",
        action="store_true",
        help="Only sync customers",
    )
    parser.add_argument(
        "--invoices-only",
        action="store_true",
        help="Only sync invoices",
    )
    parser.add_argument(
        "--payments-only",
        action="store_true",
        help="Only sync payments",
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    # Test connection
    if args.test:
        success = test_connection()
        sys.exit(0 if success else 1)

    # Validate required args for sync
    if not args.org_id:
        parser.error("--org-id is required for sync")
    if not args.ar_account:
        parser.error("--ar-account is required for sync")

    # Parse dates
    from_date = datetime.strptime(args.from_date, "%Y-%m-%d").date()
    to_date = (
        datetime.strptime(args.to_date, "%Y-%m-%d").date()
        if args.to_date
        else date.today()
    )

    # Parse UUIDs
    org_id = UUID(args.org_id)
    ar_account_id = UUID(args.ar_account)
    revenue_account_id = UUID(args.revenue_account) if args.revenue_account else None

    # Determine entity types to sync
    entity_types = None
    if args.customers_only:
        entity_types = ["customers"]
    elif args.invoices_only:
        entity_types = ["invoices"]
    elif args.payments_only:
        entity_types = ["payments"]

    # Run sync
    results = run_sync(
        organization_id=org_id,
        ar_control_account_id=ar_account_id,
        revenue_account_id=revenue_account_id,
        from_date=from_date,
        to_date=to_date,
        batch_size=args.batch_size,
        force=args.force,
        dry_run=args.dry_run,
        entity_types=entity_types,
    )

    if args.output_json:
        print(json.dumps(results, indent=2))
    else:
        print("\n" + "=" * 60)
        print("SYNC RESULTS SUMMARY")
        print("=" * 60)
        for entity, data in results.items():
            if isinstance(data, dict):
                print(f"\n{entity.upper()}:")
                print(f"  Created: {data.get('created', 0)}")
                print(f"  Updated: {data.get('updated', 0)}")
                print(f"  Skipped: {data.get('skipped', 0)}")
                if data.get("errors"):
                    print(f"  Errors: {len(data['errors'])}")


if __name__ == "__main__":
    main()
