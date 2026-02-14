#!/usr/bin/env python3
"""
ERPNext Sync Script.

Test connection and run migration from ERPNext to DotMac ERP.

Usage:
    python scripts/erpnext_sync.py                    # Test connection + preview
    python scripts/erpnext_sync.py --sync             # Run full sync of all entities
    python scripts/erpnext_sync.py --sync inventory   # Sync only inventory entities
    python scripts/erpnext_sync.py --sync material_requests  # Sync only material requests
    python scripts/erpnext_sync.py --entity items     # Sync specific entity
"""

import argparse
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.erpnext.client import ERPNextClient, ERPNextConfig, ERPNextError

# ERPNext configuration — read from environment, fall back to defaults
ERPNEXT_URL = os.environ.get("ERPNEXT_URL", "https://erp.dotmac.ng")
ERPNEXT_API_KEY = os.environ.get("ERPNEXT_API_KEY", "")
ERPNEXT_API_SECRET = os.environ.get("ERPNEXT_API_SECRET", "")
ERPNEXT_COMPANY = "Dotmac Technologies"  # Must match company name in ERPNext exactly

# ERPNextConfig for preview/testing (used by client directly)
ERPNEXT_CONFIG = ERPNextConfig(
    url=ERPNEXT_URL,
    api_key=ERPNEXT_API_KEY,
    api_secret=ERPNEXT_API_SECRET,
    company=ERPNEXT_COMPANY,
)

# Entity groups for convenience
INVENTORY_ENTITIES = ["warehouses", "item_categories", "items"]
MATERIAL_REQUEST_ENTITIES = ["material_requests"]
ALL_INVENTORY = INVENTORY_ENTITIES + MATERIAL_REQUEST_ENTITIES


def test_connection():
    """Test connection to ERPNext."""
    print("Testing connection to ERPNext...")
    print(f"URL: {ERPNEXT_CONFIG.url}")

    with ERPNextClient(ERPNEXT_CONFIG) as client:
        try:
            result = client.test_connection()
            print(f"✓ Connected as: {result.get('user')}")
            return True
        except ERPNextError as e:
            print(f"✗ Connection failed: {e.message}")
            if e.status_code:
                print(f"  Status code: {e.status_code}")
            return False


def preview_data():
    """Preview available data in ERPNext."""
    print("\nPreviewing ERPNext data...")

    with ERPNextClient(ERPNEXT_CONFIG) as client:
        # Check counts for each entity type
        entities = [
            ("Account", "Chart of Accounts"),
            ("Item Group", "Item Categories"),
            ("Item", "Items"),
            ("Warehouse", "Warehouses"),
            ("Material Request", "Material Requests"),
            ("Asset Category", "Asset Categories"),
            ("Asset", "Assets"),
            ("Customer", "Customers"),
            ("Supplier", "Suppliers"),
            ("Project", "Projects"),
            ("Issue", "Support Tickets"),
            ("Task", "Tasks"),
            ("Employee", "Employees"),
            ("Expense Claim", "Expense Claims"),
        ]

        print("\n" + "=" * 55)
        print(f"{'DocType':<25} {'Count':>10}")
        print("=" * 55)

        total = 0
        for doctype, label in entities:
            try:
                count = client.get_count(doctype)
                print(f"{label:<25} {count:>10}")
                total += count
            except ERPNextError as e:
                print(f"{label:<25} {'ERROR':>10} - {e.message}")

        print("=" * 55)
        print(f"{'TOTAL':<25} {total:>10}")
        print("=" * 55)


def list_sample_data():
    """List sample data from each entity type."""
    with ERPNextClient(ERPNEXT_CONFIG) as client:
        # Sample accounts
        print("\n--- Sample Accounts (first 5) ---")
        for i, acc in enumerate(client.get_chart_of_accounts()):
            if i >= 5:
                break
            print(
                f"  {acc.get('name')} - {acc.get('account_name')} ({acc.get('root_type')})"
            )

        # Sample items
        print("\n--- Sample Items (first 5) ---")
        for i, item in enumerate(client.get_items()):
            if i >= 5:
                break
            print(f"  {item.get('item_code')} - {item.get('item_name')}")

        # Sample warehouses
        print("\n--- Sample Warehouses (first 5) ---")
        for i, wh in enumerate(client.get_warehouses()):
            if i >= 5:
                break
            print(f"  {wh.get('name')} - {wh.get('warehouse_name')}")

        # Sample material requests
        print("\n--- Sample Material Requests (first 5) ---")
        for i, mr in enumerate(client.get_material_requests()):
            if i >= 5:
                break
            print(
                f"  {mr.get('name')} - {mr.get('material_request_type')} ({mr.get('status')})"
            )

        # Sample customers
        print("\n--- Sample Customers (first 5) ---")
        for i, cust in enumerate(client.get_customers()):
            if i >= 5:
                break
            print(f"  {cust.get('name')} - {cust.get('customer_name')}")


def run_sync(entity_types=None, organization_id=None, user_id=None, incremental=False):
    """
    Run sync from ERPNext.

    Args:
        entity_types: List of entity types to sync, or None for all
        organization_id: UUID of organization to sync to
        user_id: UUID of user performing sync
        incremental: If True, only sync records modified since last sync
    """
    from app.db import SessionLocal
    from app.models.sync import SyncType
    from app.services.erpnext.sync.orchestrator import (
        ERPNextSyncOrchestrator,
        MigrationConfig,
    )

    # Default IDs (should be configured per environment)
    if organization_id is None:
        # Try to get first organization from database
        db = SessionLocal()
        try:
            from app.models.finance.core_org import Organization

            org = db.query(Organization).first()
            if org:
                organization_id = org.organization_id
            else:
                print("Error: No organization found in database")
                return False
        finally:
            db.close()

    if user_id is None:
        # Try to get first active person from the organization
        db = SessionLocal()
        try:
            from app.models.person import Person

            person = (
                db.query(Person)
                .filter(
                    Person.organization_id == organization_id,
                    Person.is_active.is_(True),
                )
                .first()
            )
            if person:
                user_id = person.id
            else:
                print("Error: No active person found in organization")
                return False
        finally:
            db.close()

    print(f"\n{'=' * 60}")
    print("ERPNext Sync")
    print(f"{'=' * 60}")
    print(f"Organization ID: {organization_id}")
    print(f"User ID: {user_id}")
    print(f"Sync Type: {'Incremental' if incremental else 'Full'}")
    if entity_types:
        print(f"Entities: {', '.join(entity_types)}")
    else:
        print("Entities: All supported")
    print(f"{'=' * 60}\n")

    db = SessionLocal()
    try:
        # Resolve AR/AP control accounts for the organization
        from sqlalchemy import select as sa_select

        from app.models.finance.gl.account import Account

        ar_account = db.scalar(
            sa_select(Account.account_id).where(
                Account.organization_id == organization_id,
                Account.account_code == "1400",
            )
        )
        ap_account = db.scalar(
            sa_select(Account.account_id).where(
                Account.organization_id == organization_id,
                Account.account_code == "2000",
            )
        )
        print(f"DEBUG org_id={organization_id} type={type(organization_id)}")
        print(f"AR Control Account: {ar_account}")
        print(f"AP Control Account: {ap_account}")

        # Create MigrationConfig for the orchestrator
        config = MigrationConfig(
            erpnext_url=ERPNEXT_URL,
            erpnext_api_key=ERPNEXT_API_KEY,
            erpnext_api_secret=ERPNEXT_API_SECRET,
            erpnext_company=ERPNEXT_COMPANY,
            sync_type=SyncType.INCREMENTAL if incremental else SyncType.FULL,
            entity_types=entity_types,
            ar_control_account_id=ar_account,
            ap_control_account_id=ap_account,
        )

        orchestrator = ERPNextSyncOrchestrator(
            db=db,
            organization_id=organization_id,
            user_id=user_id,
            config=config,
        )

        # Test connection first
        print("Testing ERPNext connection...")
        conn_result = orchestrator.test_connection()
        if not conn_result.get("success"):
            print(f"✗ Connection test failed: {conn_result.get('error')}")
            return False
        print(f"✓ Connection successful (user: {conn_result.get('user')})\n")

        # Preview what will be synced
        print("Previewing sync...")
        preview = orchestrator.preview()
        print(f"\n  Entity Types: {len(preview.get('entity_types', {}))}")
        print(f"  Total Records: {preview.get('total_records', 0)}")
        for entity_name, info in preview.get("entity_types", {}).items():
            count = info.get("count", 0)
            label = info.get("name", entity_name)
            if info.get("error"):
                print(f"    {label}: ERROR - {info.get('error')}")
            else:
                print(f"    {label}: {count} records")
        print()

        # Run sync
        if entity_types:
            # Sync specific entities
            for entity in entity_types:
                print(f"\nSyncing {entity}...")
                try:
                    result = orchestrator.run_single(entity)
                    print(
                        f"  ✓ {entity}: {result.synced_count} synced, {result.skipped_count} skipped, {result.error_count} errors"
                    )
                except Exception as e:
                    print(f"  ✗ {entity}: {str(e)}")
        else:
            # Full sync
            print("Running full sync...")
            history = orchestrator.run()
            print("\nSync completed!")
            print(f"  Total records: {history.total_records}")
            print(f"  Synced: {history.synced_count}")
            print(f"  Skipped: {history.skipped_count}")
            print(f"  Errors: {history.error_count}")
            if history.status:
                print(f"  Status: {history.status.value}")

        db.commit()
        return True

    except Exception as e:
        db.rollback()
        print(f"\n✗ Sync failed: {str(e)}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(
        description="ERPNext Sync Script for DotMac ERP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/erpnext_sync.py                         # Test connection + preview
  python scripts/erpnext_sync.py --sync                  # Full sync all entities
  python scripts/erpnext_sync.py --sync inventory        # Sync inventory-related entities
  python scripts/erpnext_sync.py --sync material_requests # Sync only material requests
  python scripts/erpnext_sync.py --entity items          # Sync specific entity
  python scripts/erpnext_sync.py --entity warehouses --entity items  # Multiple entities
  python scripts/erpnext_sync.py --sync --incremental    # Incremental sync
  python scripts/erpnext_sync.py --sample                # Show sample data
        """,
    )

    parser.add_argument(
        "--sync",
        nargs="?",
        const="all",
        help="Run sync. Optional: 'all', 'inventory', 'material_requests'",
    )
    parser.add_argument(
        "--entity",
        action="append",
        dest="entities",
        help="Specific entity to sync (can be repeated)",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Only sync records modified since last sync",
    )
    parser.add_argument(
        "--sample", action="store_true", help="Show sample data from ERPNext"
    )
    parser.add_argument("--org-id", help="Organization ID (UUID) to sync to")
    parser.add_argument("--user-id", help="User ID (UUID) performing sync")

    args = parser.parse_args()

    # Always test connection first
    if not test_connection():
        sys.exit(1)

    # Preview data
    preview_data()

    # Show sample data if requested
    if args.sample:
        list_sample_data()

    # Run sync if requested
    if args.sync or args.entities:
        entity_types = None

        if args.entities:
            entity_types = args.entities
        elif args.sync == "inventory":
            entity_types = ALL_INVENTORY
        elif args.sync == "material_requests":
            entity_types = MATERIAL_REQUEST_ENTITIES
        # else: sync all

        org_id = args.org_id
        user_id = args.user_id

        # Convert string UUIDs
        if org_id:
            from uuid import UUID

            org_id = UUID(org_id)
        if user_id:
            from uuid import UUID

            user_id = UUID(user_id)

        success = run_sync(
            entity_types=entity_types,
            organization_id=org_id,
            user_id=user_id,
            incremental=args.incremental,
        )

        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
