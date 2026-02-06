#!/usr/bin/env python3
"""
Data Import CLI Tool.

Import data from CSV files into the IFRS-based accounting system.

Usage:
    python scripts/import_data.py --help
    python scripts/import_data.py accounts --file data/chart_of_accounts.csv --org-id <uuid>
    python scripts/import_data.py customers --file data/contacts.csv --org-id <uuid>
    python scripts/import_data.py all --directory data/backup/ --org-id <uuid>

Import order:
1. accounts (Chart of Accounts)
2. customers (Contacts - Customers)
3. suppliers (Contacts - Vendors)
4. items (Inventory Items)
5. assets (Fixed Assets)
6. banking (Bank Accounts)
7. invoices (Customer Invoices)
8. expenses (Expense Entries)
9. payments (Customer/Supplier Payments)
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional
from uuid import UUID

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.db import get_db_session
from app.services.finance.import_export import (
    AccountImporter,
    CustomerImporter,
    SupplierImporter,
    ItemImporter,
    AssetImporter,
    BankAccountImporter,
    InvoiceImporter,
    ExpenseImporter,
    CustomerPaymentImporter,
    ImportConfig,
    ImportResult,
    ImportStatus,
    get_ar_control_account,
    get_ap_control_account,
)


def print_result(result: ImportResult) -> None:
    """Print import result summary."""
    status_colors = {
        ImportStatus.COMPLETED: "\033[92m",  # Green
        ImportStatus.COMPLETED_WITH_ERRORS: "\033[93m",  # Yellow
        ImportStatus.FAILED: "\033[91m",  # Red
        ImportStatus.IN_PROGRESS: "\033[94m",  # Blue
        ImportStatus.PENDING: "\033[90m",  # Gray
    }
    reset = "\033[0m"

    color = status_colors.get(result.status, "")
    print(f"\n{'=' * 60}")
    print(f"Import Result: {result.entity_type}")
    print(f"{'=' * 60}")
    print(f"Status: {color}{result.status.value}{reset}")
    print(f"Total Rows: {result.total_rows}")
    print(f"Imported: {result.imported_count}")
    print(f"Skipped: {result.skipped_count}")
    print(f"Duplicates: {result.duplicate_count}")
    print(f"Errors: {result.error_count}")
    print(f"Success Rate: {result.success_rate:.1f}%")
    print(f"Duration: {result.duration_seconds:.2f}s")

    if result.errors:
        print(f"\nFirst {min(10, len(result.errors))} Errors:")
        for error in result.errors[:10]:
            print(f"  - {error}")

    if result.warnings:
        print(f"\nFirst {min(5, len(result.warnings))} Warnings:")
        for warning in result.warnings[:5]:
            print(f"  - {warning}")

    print()


def import_accounts(args, db, config: ImportConfig) -> ImportResult:
    """Import chart of accounts."""
    print(f"Importing accounts from: {args.file}")
    importer = AccountImporter(db, config)
    return importer.import_file(args.file)


def import_customers(args, db, config: ImportConfig) -> ImportResult:
    """Import customers."""
    print(f"Importing customers from: {args.file}")

    # Get AR control account
    ar_control_id = get_ar_control_account(db, config.organization_id)
    if not ar_control_id:
        print("ERROR: No AR control account found. Import accounts first.")
        sys.exit(1)

    importer = CustomerImporter(db, config, ar_control_id)
    return importer.import_file(args.file)


def import_suppliers(args, db, config: ImportConfig) -> ImportResult:
    """Import suppliers/vendors."""
    print(f"Importing suppliers from: {args.file}")

    ap_control_id = get_ap_control_account(db, config.organization_id)
    if not ap_control_id:
        print("ERROR: No AP control account found. Import accounts first.")
        sys.exit(1)

    importer = SupplierImporter(db, config, ap_control_id)
    return importer.import_file(args.file)


def import_items(args, db, config: ImportConfig) -> ImportResult:
    """Import inventory items."""
    print(f"Importing items from: {args.file}")

    from sqlalchemy import select
    from app.models.finance.gl.account import Account

    # Find required accounts
    def find_account(subledger_type: str) -> Optional[UUID]:
        result = db.execute(
            select(Account).where(
                Account.organization_id == config.organization_id,
                Account.subledger_type == subledger_type,
            )
        ).scalar_one_or_none()
        return result.account_id if result else None

    inventory_account_id = find_account("INVENTORY")
    if not inventory_account_id:
        # Try to find by name
        result = db.execute(
            select(Account).where(
                Account.organization_id == config.organization_id,
                Account.account_name.ilike("%inventory%"),
            )
        ).first()
        inventory_account_id = result[0].account_id if result else None

    if not inventory_account_id:
        print("ERROR: No inventory account found. Import accounts first.")
        sys.exit(1)

    # For simplicity, use the same account for COGS, revenue, and adjustment
    # In production, you'd want to find the correct accounts
    importer = ItemImporter(
        db,
        config,
        inventory_account_id,
        inventory_account_id,  # COGS
        inventory_account_id,  # Revenue
        inventory_account_id,  # Adjustment
    )
    return importer.import_file(args.file)


def import_assets(args, db, config: ImportConfig) -> ImportResult:
    """Import fixed assets."""
    print(f"Importing fixed assets from: {args.file}")

    from sqlalchemy import select
    from app.models.finance.gl.account import Account

    # Find asset account
    result = db.execute(
        select(Account).where(
            Account.organization_id == config.organization_id,
            Account.subledger_type == "ASSET",
        )
    ).first()

    if not result:
        result = db.execute(
            select(Account).where(
                Account.organization_id == config.organization_id,
                Account.account_name.ilike("%fixed asset%"),
            )
        ).first()

    if not result:
        print("ERROR: No fixed asset account found. Import accounts first.")
        sys.exit(1)

    asset_account_id = result[0].account_id

    # Use same account for simplicity - in production, find correct accounts
    importer = AssetImporter(
        db,
        config,
        asset_account_id,
        asset_account_id,  # Accumulated depreciation
        asset_account_id,  # Depreciation expense
        asset_account_id,  # Gain/loss disposal
    )
    return importer.import_file(args.file)


def import_banking(args, db, config: ImportConfig) -> ImportResult:
    """Import bank accounts."""
    print(f"Importing bank accounts from: {args.file}")

    from sqlalchemy import select
    from app.models.finance.gl.account import Account

    # Find bank GL account
    result = db.execute(
        select(Account).where(
            Account.organization_id == config.organization_id,
            Account.subledger_type == "BANK",
        )
    ).first()

    default_gl_account_id = result[0].account_id if result else None

    importer = BankAccountImporter(db, config, default_gl_account_id)
    return importer.import_file(args.file)


def import_invoices(args, db, config: ImportConfig) -> ImportResult:
    """Import customer invoices."""
    print(f"Importing invoices from: {args.file}")

    from sqlalchemy import select
    from app.models.finance.gl.account import Account

    ar_control_id = get_ar_control_account(db, config.organization_id)
    if not ar_control_id:
        print("ERROR: No AR control account found. Import accounts first.")
        sys.exit(1)

    # Find revenue account
    result = db.execute(
        select(Account).where(
            Account.organization_id == config.organization_id,
            Account.account_name.ilike("%sales%"),
        )
    ).first()

    revenue_account_id = result[0].account_id if result else ar_control_id

    importer = InvoiceImporter(db, config, ar_control_id, revenue_account_id)
    return importer.import_file(args.file)


def import_expenses(args, db, config: ImportConfig) -> ImportResult:
    """Import expense entries."""
    print(f"Importing expenses from: {args.file}")

    from sqlalchemy import select
    from app.models.finance.gl.account import Account

    # Find expense account
    result = db.execute(
        select(Account).where(
            Account.organization_id == config.organization_id,
            Account.account_name.ilike("%expense%"),
        )
    ).first()

    if not result:
        print("ERROR: No expense account found. Import accounts first.")
        sys.exit(1)

    expense_account_id = result[0].account_id

    # Find payment account (bank or cash)
    payment_result = db.execute(
        select(Account).where(
            Account.organization_id == config.organization_id,
            Account.subledger_type == "BANK",
        )
    ).first()

    payment_account_id = payment_result[0].account_id if payment_result else None

    importer = ExpenseImporter(db, config, expense_account_id, payment_account_id)
    return importer.import_file(args.file)


def import_payments(args, db, config: ImportConfig) -> ImportResult:
    """Import customer payments."""
    print(f"Importing payments from: {args.file}")

    from sqlalchemy import select
    from app.models.finance.banking.bank_account import BankAccount

    # Find bank account
    result = db.execute(
        select(BankAccount).where(
            BankAccount.organization_id == config.organization_id,
        )
    ).first()

    bank_account_id = result[0].bank_account_id if result else None

    importer = CustomerPaymentImporter(db, config, bank_account_id)
    return importer.import_file(args.file)


def import_all(args, db, config: ImportConfig) -> List[ImportResult]:
    """Import all data from a directory."""
    results = []
    directory = Path(args.directory)

    if not directory.exists():
        print(f"ERROR: Directory not found: {directory}")
        sys.exit(1)

    # Define import order and file patterns
    import_sequence = [
        (
            "accounts",
            ["chart_of_accounts.csv", "Chart_of_Accounts.csv", "accounts.csv"],
        ),
        ("customers", ["contacts.csv", "Contacts.csv", "customers.csv"]),
        ("suppliers", ["vendors.csv", "Vendors.csv", "suppliers.csv"]),
        ("items", ["item.csv", "Item.csv", "items.csv", "products.csv"]),
        ("assets", ["fixed_asset.csv", "Fixed_Asset.csv", "assets.csv"]),
        # ("banking", ["bank_accounts.csv"]),
        # ("invoices", ["invoice.csv", "Invoice.csv", "invoices.csv"]),
        # ("expenses", ["expense.csv", "Expense.csv", "expenses.csv"]),
        # ("payments", ["payment.csv", "payments.csv"]),
    ]

    importers = {
        "accounts": import_accounts,
        "customers": import_customers,
        "suppliers": import_suppliers,
        "items": import_items,
        "assets": import_assets,
        "banking": import_banking,
        "invoices": import_invoices,
        "expenses": import_expenses,
        "payments": import_payments,
    }

    for entity_type, patterns in import_sequence:
        # Find file
        file_path = None
        for pattern in patterns:
            # Check direct match
            candidate = directory / pattern
            if candidate.exists():
                file_path = candidate
                break
            # Check subdirectories
            for subdir in directory.iterdir():
                if subdir.is_dir():
                    candidate = subdir / pattern
                    if candidate.exists():
                        file_path = candidate
                        break

        if file_path:
            print(f"\n{'=' * 60}")
            print(f"Found {entity_type} file: {file_path}")
            args.file = str(file_path)
            try:
                result = importers[entity_type](args, db, config)
                results.append(result)
                print_result(result)

                if result.status == ImportStatus.FAILED:
                    print(f"WARNING: {entity_type} import failed. Continuing...")
            except Exception as e:
                print(f"ERROR importing {entity_type}: {e}")
        else:
            print(f"\nSkipping {entity_type}: No matching file found")

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Import data from CSV files into the accounting system.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument("--org-id", required=True, help="Organization UUID")
    parser.add_argument("--user-id", required=True, help="User UUID for audit trail")
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate without saving to database"
    )
    parser.add_argument(
        "--skip-duplicates",
        action="store_true",
        default=True,
        help="Skip duplicate entries (default: True)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of records to commit at once (default: 100)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Import type")

    # Individual importers
    for cmd in [
        "accounts",
        "customers",
        "suppliers",
        "items",
        "assets",
        "banking",
        "invoices",
        "expenses",
        "payments",
    ]:
        sub = subparsers.add_parser(cmd, help=f"Import {cmd}")
        sub.add_argument("--file", "-f", required=True, help="CSV file path")

    # All command
    all_parser = subparsers.add_parser("all", help="Import all data from directory")
    all_parser.add_argument(
        "--directory", "-d", required=True, help="Directory containing CSV files"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Validate UUIDs
    try:
        org_id = UUID(args.org_id)
        user_id = UUID(args.user_id)
    except ValueError as e:
        print(f"ERROR: Invalid UUID: {e}")
        sys.exit(1)

    # Create import config
    config = ImportConfig(
        organization_id=org_id,
        user_id=user_id,
        skip_duplicates=args.skip_duplicates,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )

    # Get database session
    db = next(get_db_session())

    try:
        # Run import
        if args.command == "all":
            results = import_all(args, db, config)
            print("\n" + "=" * 60)
            print("IMPORT SUMMARY")
            print("=" * 60)
            for result in results:
                print(
                    f"  {result.entity_type}: {result.imported_count}/{result.total_rows} "
                    f"({result.success_rate:.1f}%)"
                )
        else:
            importers = {
                "accounts": import_accounts,
                "customers": import_customers,
                "suppliers": import_suppliers,
                "items": import_items,
                "assets": import_assets,
                "banking": import_banking,
                "invoices": import_invoices,
                "expenses": import_expenses,
                "payments": import_payments,
            }

            result = importers[args.command](args, db, config)
            print_result(result)

        # Commit if not dry run
        if not args.dry_run:
            db.commit()
            print("Changes committed to database.")
        else:
            db.rollback()
            print("DRY RUN - No changes saved.")

    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
