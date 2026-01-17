#!/usr/bin/env python3
"""
ERPNext Sync Script.

Test connection and run migration from ERPNext to DotMac Books.
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.erpnext.client import ERPNextClient, ERPNextConfig, ERPNextError


def test_connection():
    """Test connection to ERPNext."""
    config = ERPNextConfig(
        url="https://erp.dotmac.ng",
        api_key="REDACTED_API_KEY",
        api_secret="REDACTED_API_SECRET",
    )

    print("Testing connection to ERPNext...")
    print(f"URL: {config.url}")

    with ERPNextClient(config) as client:
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
    config = ERPNextConfig(
        url="https://erp.dotmac.ng",
        api_key="REDACTED_API_KEY",
        api_secret="REDACTED_API_SECRET",
    )

    print("\nPreviewing ERPNext data...")

    with ERPNextClient(config) as client:
        # Check counts for each entity type
        entities = [
            ("Account", "Chart of Accounts"),
            ("Item Group", "Item Categories"),
            ("Item", "Items"),
            ("Asset Category", "Asset Categories"),
            ("Asset", "Assets"),
            ("Warehouse", "Warehouses"),
            ("Customer", "Customers"),
            ("Supplier", "Suppliers"),
        ]

        print("\n" + "=" * 50)
        print(f"{'DocType':<20} {'Count':>10}")
        print("=" * 50)

        total = 0
        for doctype, label in entities:
            try:
                count = client.get_count(doctype)
                print(f"{label:<20} {count:>10}")
                total += count
            except ERPNextError as e:
                print(f"{label:<20} {'ERROR':>10} - {e.message}")

        print("=" * 50)
        print(f"{'TOTAL':<20} {total:>10}")
        print("=" * 50)


def list_sample_data():
    """List sample data from each entity type."""
    config = ERPNextConfig(
        url="https://erp.dotmac.ng",
        api_key="REDACTED_API_KEY",
        api_secret="REDACTED_API_SECRET",
    )

    with ERPNextClient(config) as client:
        # Sample accounts
        print("\n--- Sample Accounts (first 5) ---")
        for i, acc in enumerate(client.get_chart_of_accounts()):
            if i >= 5:
                break
            print(f"  {acc.get('name')} - {acc.get('account_name')} ({acc.get('root_type')})")

        # Sample items
        print("\n--- Sample Items (first 5) ---")
        for i, item in enumerate(client.get_items()):
            if i >= 5:
                break
            print(f"  {item.get('item_code')} - {item.get('item_name')}")

        # Sample customers
        print("\n--- Sample Customers (first 5) ---")
        for i, cust in enumerate(client.get_customers()):
            if i >= 5:
                break
            print(f"  {cust.get('name')} - {cust.get('customer_name')}")

        # Sample assets
        print("\n--- Sample Assets (first 5) ---")
        for i, asset in enumerate(client.get_assets()):
            if i >= 5:
                break
            print(f"  {asset.get('name')} - {asset.get('asset_name')}")


if __name__ == "__main__":
    if test_connection():
        preview_data()

        # Ask before showing sample data
        print("\nShow sample data? (y/n): ", end="")
        try:
            response = input().strip().lower()
            if response == 'y':
                list_sample_data()
        except EOFError:
            # Non-interactive mode - show sample data
            list_sample_data()
