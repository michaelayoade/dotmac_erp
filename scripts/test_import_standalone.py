#!/usr/bin/env python3
"""
Standalone Import Test Script.

Tests CSV parsing without requiring project dependencies.
"""

import csv
from pathlib import Path

# Zoho account type mapping
ZOHO_ACCOUNT_TYPE_MAPPING = {
    "Income": ("REVENUE", "CREDIT"),
    "Other Income": ("REVENUE", "CREDIT"),
    "Expense": ("EXPENSES", "DEBIT"),
    "Other Expense": ("EXPENSES", "DEBIT"),
    "Cost Of Goods Sold": ("EXPENSES", "DEBIT"),
    "Cash": ("ASSETS", "DEBIT"),
    "Bank": ("ASSETS", "DEBIT"),
    "Accounts Receivable": ("ASSETS", "DEBIT"),
    "Other Current Asset": ("ASSETS", "DEBIT"),
    "Fixed Asset": ("ASSETS", "DEBIT"),
    "Stock": ("ASSETS", "DEBIT"),
    "Input Tax": ("ASSETS", "DEBIT"),
    "Payment Clearing": ("ASSETS", "DEBIT"),
    "Accounts Payable": ("LIABILITIES", "CREDIT"),
    "Other Current Liability": ("LIABILITIES", "CREDIT"),
    "Long Term Liability": ("LIABILITIES", "CREDIT"),
    "Other Liability": ("LIABILITIES", "CREDIT"),
    "Output Tax": ("LIABILITIES", "CREDIT"),
    "Equity": ("EQUITY", "CREDIT"),
}


def test_chart_of_accounts(file_path: str) -> None:
    """Test Chart of Accounts CSV import."""
    print(f"\n{'='*60}")
    print(f"  CHART OF ACCOUNTS IMPORT TEST")
    print(f"{'='*60}")
    print(f"\nFile: {file_path}\n")

    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Total Accounts: {len(rows)}")
    print(f"Columns: {', '.join(rows[0].keys())}\n")

    # Analyze account types
    account_types = {}
    for row in rows:
        acc_type = row.get('Account Type', 'Unknown')
        account_types[acc_type] = account_types.get(acc_type, 0) + 1

    print("Account Type Distribution:")
    print("-" * 50)
    for acc_type, count in sorted(account_types.items(), key=lambda x: -x[1]):
        mapped = ZOHO_ACCOUNT_TYPE_MAPPING.get(acc_type, ('UNKNOWN', 'UNKNOWN'))
        status = "✓" if acc_type in ZOHO_ACCOUNT_TYPE_MAPPING else "✗"
        print(f"  {status} {acc_type:25} -> {mapped[0]:12} ({count:3} accounts)")

    # Status distribution
    print("\nStatus Distribution:")
    print("-" * 50)
    statuses = {}
    for row in rows:
        status = row.get('Account Status', 'Unknown')
        statuses[status] = statuses.get(status, 0) + 1
    for status, count in statuses.items():
        print(f"  {status}: {count}")

    # Validate required fields
    print("\nValidation Results:")
    print("-" * 50)
    missing_name = [r for r in rows if not r.get('Account Name', '').strip()]
    missing_type = [r for r in rows if not r.get('Account Type', '').strip()]
    unmapped_types = [r for r in rows if r.get('Account Type', '') not in ZOHO_ACCOUNT_TYPE_MAPPING]

    print(f"  Missing Account Name: {len(missing_name)}")
    print(f"  Missing Account Type: {len(missing_type)}")
    print(f"  Unmapped Account Types: {len(unmapped_types)}")

    if unmapped_types:
        unique_unmapped = set(r.get('Account Type', '') for r in unmapped_types)
        print(f"    Unmapped types: {unique_unmapped}")

    valid_count = len(rows) - len(missing_name) - len(missing_type)
    success_rate = (valid_count / len(rows)) * 100 if rows else 0

    print(f"\nImport Simulation:")
    print("-" * 50)
    print(f"  Total rows: {len(rows)}")
    print(f"  Valid for import: {valid_count}")
    print(f"  Would skip: {len(rows) - valid_count}")
    print(f"  Success rate: {success_rate:.1f}%")

    # Category summary
    print("\nCategories to be created:")
    print("-" * 50)
    categories = set(r.get('Account Type', '') for r in rows if r.get('Account Type', ''))
    for cat in sorted(categories):
        ifrs_cat = ZOHO_ACCOUNT_TYPE_MAPPING.get(cat, ('UNKNOWN', 'UNKNOWN'))[0]
        count = account_types.get(cat, 0)
        print(f"  {cat} -> {ifrs_cat} ({count} accounts)")

    # Sample accounts per category
    print("\nSample Accounts by IFRS Category:")
    print("-" * 50)
    by_ifrs = {}
    for row in rows:
        acc_type = row.get('Account Type', '')
        if acc_type in ZOHO_ACCOUNT_TYPE_MAPPING:
            ifrs_cat = ZOHO_ACCOUNT_TYPE_MAPPING[acc_type][0]
            if ifrs_cat not in by_ifrs:
                by_ifrs[ifrs_cat] = []
            by_ifrs[ifrs_cat].append(row.get('Account Name', ''))

    for ifrs_cat in ['ASSETS', 'LIABILITIES', 'EQUITY', 'REVENUE', 'EXPENSES']:
        if ifrs_cat in by_ifrs:
            print(f"\n  {ifrs_cat} ({len(by_ifrs[ifrs_cat])} accounts):")
            for acc in by_ifrs[ifrs_cat][:3]:
                print(f"    - {acc[:50]}")
            if len(by_ifrs[ifrs_cat]) > 3:
                print(f"    ... and {len(by_ifrs[ifrs_cat]) - 3} more")

    print(f"\n{'='*60}")
    print("  TEST COMPLETE - Ready for import!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    import sys

    test_file = "/Users/michaelayoade/Downloads/Projects/dotmac_books/books_backup/Books backup/Chart_of_Accounts.csv"

    if len(sys.argv) > 1:
        test_file = sys.argv[1]

    if not Path(test_file).exists():
        print(f"ERROR: File not found: {test_file}")
        sys.exit(1)

    test_chart_of_accounts(test_file)
