"""
Test script for Stock Ledger Entry sync.

Run inside the app container:
  docker exec dotmac_erp_app python scripts/test_sle_sync.py

Steps:
1. Gets ERPNext config from integration_config table
2. Checks SLE count in ERPNext
3. Runs the sync (limited batch for testing)
4. Reports results
"""
import sys
import uuid

# Bootstrap the app
sys.path.insert(0, "/app")

from app.db import SessionLocal
from app.services.erpnext.client import ERPNextClient, ERPNextConfig
from app.services.erpnext.sync.orchestrator import (
    ERPNextSyncOrchestrator,
    MigrationConfig,
)

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
USER_ID = uuid.UUID("e1d3ecff-80aa-420d-91b2-a79c1450705c")


def get_erpnext_config(db):
    """Read ERPNext integration config from DB."""
    from sqlalchemy import text

    row = db.execute(
        text(
            "SELECT base_url, api_key, api_secret, company "
            "FROM sync.integration_config "
            "WHERE integration_type = 'ERPNEXT' AND is_active = true "
            "LIMIT 1"
        )
    ).fetchone()

    if not row:
        print("ERROR: No active ERPNext integration config found")
        sys.exit(1)

    return row


def decrypt_value(encrypted: str) -> str:
    """Decrypt an encrypted config value."""
    if not encrypted or not encrypted.startswith("enc:"):
        return encrypted or ""
    from app.services.encryption import decrypt_field

    return decrypt_field(encrypted)


def main():
    print("=" * 60)
    print("Stock Ledger Entry Sync Test")
    print("=" * 60)

    db = SessionLocal()

    try:
        # 1. Get config
        config_row = get_erpnext_config(db)
        base_url = config_row[0]
        api_key = decrypt_value(config_row[1])
        api_secret = decrypt_value(config_row[2])
        company = config_row[3]

        print(f"\nERPNext URL: {base_url}")
        print(f"Company: {company}")

        # 2. Test connection and count SLEs
        client = ERPNextClient(
            ERPNextConfig(
                url=base_url,
                api_key=api_key,
                api_secret=api_secret,
                company=company,
            )
        )

        print("\nTesting connection...")
        conn = client.test_connection()
        print(f"Connected as: {conn.get('user')}")

        print("\nCounting Stock Ledger Entries...")
        filters = {}
        if company:
            filters["company"] = company
        count = client.get_count("Stock Ledger Entry", filters)
        print(f"Total SLEs in ERPNext: {count:,}")

        # Preview a few entries
        print("\nSample SLEs:")
        sample = client.list_documents(
            "Stock Ledger Entry",
            filters=filters,
            fields=[
                "name", "item_code", "warehouse", "posting_date",
                "actual_qty", "valuation_rate", "voucher_type", "voucher_no",
            ],
            order_by="posting_date desc",
            limit_page_length=5,
        )
        for entry in sample:
            print(
                f"  {entry.get('posting_date')} | {entry.get('item_code', '')[:30]:30s} | "
                f"qty={entry.get('actual_qty'):>10} | {entry.get('voucher_type')} {entry.get('voucher_no')}"
            )

        client.close()

        # 3. Run the sync
        print(f"\nReady to sync {count:,} Stock Ledger Entries.")
        print("Proceeding with sync...\n")

        migration_config = MigrationConfig(
            erpnext_url=base_url,
            erpnext_api_key=api_key,
            erpnext_api_secret=api_secret,
            erpnext_company=company,
            entity_types=["stock_ledger_entries"],
        )

        orchestrator = ERPNextSyncOrchestrator(
            db=db,
            organization_id=ORG_ID,
            user_id=USER_ID,
            config=migration_config,
        )

        result = orchestrator.run_single("stock_ledger_entries")
        db.commit()

        # 4. Report results
        print("\n" + "=" * 60)
        print("SYNC RESULTS")
        print("=" * 60)
        print(f"Total records:  {result.total_records:,}")
        print(f"Synced:         {result.synced_count:,}")
        print(f"Skipped:        {result.skipped_count:,}")
        print(f"Errors:         {result.error_count:,}")
        print(f"Success rate:   {result.success_rate:.1f}%")

        if result.errors:
            print(f"\nFirst {min(10, len(result.errors))} errors:")
            for err in result.errors[:10]:
                print(f"  [{err.get('name')}] {err.get('error')}")

        # Verify in DB
        from sqlalchemy import text as sql_text
        txn_count = db.execute(
            sql_text("SELECT count(*) FROM inv.inventory_transaction")
        ).scalar()
        print(f"\nTotal inventory_transaction rows in DB: {txn_count:,}")

    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    main()
