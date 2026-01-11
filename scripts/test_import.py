#!/usr/bin/env python3
"""
Test Import Script.

Tests the import functionality without requiring a database connection.
Validates CSV parsing, field mapping, and transformation logic.
"""

import csv
import sys
from pathlib import Path
from uuid import uuid4

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.services.ifrs.import_export.base import ImportConfig, ImportResult
from app.services.ifrs.import_export.accounts import (
    ZOHO_ACCOUNT_TYPE_MAPPING,
    AccountImporter,
)


def test_csv_parsing(file_path: str) -> None:
    """Test CSV parsing and show statistics."""
    print(f"\n{'='*60}")
    print(f"Testing CSV: {file_path}")
    print('='*60)

    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"\nTotal rows: {len(rows)}")
    print(f"Columns: {list(rows[0].keys()) if rows else 'N/A'}")

    # Analyze account types
    account_types = {}
    for row in rows:
        acc_type = row.get('Account Type', 'Unknown')
        account_types[acc_type] = account_types.get(acc_type, 0) + 1

    print(f"\nAccount Types Distribution:")
    for acc_type, count in sorted(account_types.items(), key=lambda x: -x[1]):
        mapped = ZOHO_ACCOUNT_TYPE_MAPPING.get(acc_type, ('UNKNOWN', 'UNKNOWN'))
        print(f"  {acc_type}: {count} -> {mapped[0].value if hasattr(mapped[0], 'value') else mapped[0]}")

    # Check for active/inactive
    statuses = {}
    for row in rows:
        status = row.get('Account Status', 'Unknown')
        statuses[status] = statuses.get(status, 0) + 1

    print(f"\nStatus Distribution:")
    for status, count in statuses.items():
        print(f"  {status}: {count}")

    # Check currencies
    currencies = {}
    for row in rows:
        currency = row.get('Currency', 'Unknown')
        currencies[currency] = currencies.get(currency, 0) + 1

    print(f"\nCurrencies:")
    for currency, count in currencies.items():
        print(f"  {currency}: {count}")

    # Sample data
    print(f"\nSample accounts (first 5):")
    for i, row in enumerate(rows[:5], 1):
        print(f"  {i}. {row.get('Account Name', 'N/A')[:40]} ({row.get('Account Type', 'N/A')})")

    # Validate required fields
    missing_name = sum(1 for r in rows if not r.get('Account Name'))
    missing_type = sum(1 for r in rows if not r.get('Account Type'))

    print(f"\nValidation:")
    print(f"  Missing Account Name: {missing_name}")
    print(f"  Missing Account Type: {missing_type}")

    # Check for unmapped account types
    unmapped = set()
    for row in rows:
        acc_type = row.get('Account Type', '')
        if acc_type and acc_type not in ZOHO_ACCOUNT_TYPE_MAPPING:
            unmapped.add(acc_type)

    if unmapped:
        print(f"\n⚠️  Unmapped Account Types: {unmapped}")
    else:
        print(f"\n✓ All account types are mapped")

    print(f"\n{'='*60}")
    print("CSV validation complete!")
    print('='*60)


def test_field_transformation() -> None:
    """Test field transformation logic."""
    print(f"\n{'='*60}")
    print("Testing Field Transformations")
    print('='*60)

    from app.services.ifrs.import_export.base import BaseImporter

    # Test date parsing
    test_dates = ["2022-01-01", "01/01/2022", "2022/01/01", "01-01-2022"]
    print("\nDate parsing:")
    for d in test_dates:
        try:
            result = BaseImporter.parse_date(d)
            print(f"  '{d}' -> {result}")
        except Exception as e:
            print(f"  '{d}' -> ERROR: {e}")

    # Test decimal parsing
    test_decimals = ["1,234.56", "1234.56", "₦1,234.56", "-500.00", ""]
    print("\nDecimal parsing:")
    for d in test_decimals:
        try:
            result = BaseImporter.parse_decimal(d)
            print(f"  '{d}' -> {result}")
        except Exception as e:
            print(f"  '{d}' -> ERROR: {e}")

    # Test boolean parsing
    test_bools = ["true", "false", "yes", "no", "1", "0", "Active", "Inactive"]
    print("\nBoolean parsing:")
    for b in test_bools:
        try:
            result = BaseImporter.parse_boolean(b)
            print(f"  '{b}' -> {result}")
        except Exception as e:
            print(f"  '{b}' -> ERROR: {e}")

    print(f"\n{'='*60}")


def simulate_import(file_path: str) -> None:
    """Simulate import without database (dry run validation)."""
    print(f"\n{'='*60}")
    print("Simulating Import (Validation Only)")
    print('='*60)

    # Create mock config
    config = ImportConfig(
        organization_id=uuid4(),
        user_id=uuid4(),
        skip_duplicates=True,
        dry_run=True,
        batch_size=100,
    )

    # Read and validate rows
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    valid_count = 0
    error_count = 0
    errors = []

    for i, row in enumerate(rows, 1):
        # Validate required fields
        account_name = row.get('Account Name', '').strip()
        account_type = row.get('Account Type', '').strip()

        if not account_name:
            errors.append(f"Row {i}: Missing Account Name")
            error_count += 1
            continue

        if not account_type:
            errors.append(f"Row {i}: Missing Account Type")
            error_count += 1
            continue

        if account_type not in ZOHO_ACCOUNT_TYPE_MAPPING:
            errors.append(f"Row {i}: Unknown Account Type '{account_type}'")
            error_count += 1
            continue

        valid_count += 1

    print(f"\nSimulation Results:")
    print(f"  Total rows: {len(rows)}")
    print(f"  Valid: {valid_count}")
    print(f"  Errors: {error_count}")
    print(f"  Success rate: {(valid_count/len(rows)*100):.1f}%")

    if errors:
        print(f"\nFirst 10 errors:")
        for err in errors[:10]:
            print(f"  - {err}")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    # Default test file
    test_file = "/Users/michaelayoade/Downloads/Projects/dotmac_books/books_backup/Books backup/Chart_of_Accounts.csv"

    if len(sys.argv) > 1:
        test_file = sys.argv[1]

    if not Path(test_file).exists():
        print(f"ERROR: File not found: {test_file}")
        sys.exit(1)

    test_csv_parsing(test_file)
    test_field_transformation()
    simulate_import(test_file)

    print("\n✓ All tests completed!")
