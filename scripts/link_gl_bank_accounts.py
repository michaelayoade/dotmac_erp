#!/usr/bin/env python3
"""
Link GL Bank Accounts to Banking Module.

Creates BankAccount records from existing GL accounts with subledger_type='BANK'.
This enables bank reconciliation and statement import features.
"""

import os
import sys
from uuid import UUID

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal
from app.models.finance.gl.account import Account
from app.models.finance.banking.bank_account import (
    BankAccount,
    BankAccountType,
    BankAccountStatus,
)

# Organization ID - use existing org or override via env var
ORG_ID = UUID(os.environ.get("ORG_ID", "00000000-0000-0000-0000-000000000001"))

# Bank name mapping based on GL account name patterns
# Maps account name keywords to (bank_name, bank_code, account_type)
BANK_MAPPINGS = {
    "zenith": ("Zenith Bank", "ZENITBNG", BankAccountType.checking),
    "heritage": ("Heritage Bank", "HABORNG", BankAccountType.checking),
    "uba": ("United Bank for Africa", "ABORPNG", BankAccountType.checking),
    "first bank": ("First Bank of Nigeria", "FBNNPNG", BankAccountType.checking),
    "paystack": ("Paystack", "PAYSTACK", BankAccountType.other),
    "flutterwave": ("Flutterwave", "FLUTTER", BankAccountType.other),
    "fultterwave": ("Flutterwave", "FLUTTER", BankAccountType.other),  # Handle typo
    "quick teller": ("QuickTeller", "QTELLER", BankAccountType.other),
    "cash": ("Cash", "CASH", BankAccountType.other),
}


def get_bank_info(account_name: str) -> tuple[str, str, BankAccountType]:
    """
    Derive bank info from GL account name.

    Returns (bank_name, bank_code, account_type).
    """
    name_lower = account_name.lower()

    for keyword, (bank_name, bank_code, acc_type) in BANK_MAPPINGS.items():
        if keyword in name_lower:
            return bank_name, bank_code, acc_type

    # Default fallback - use account name as bank name
    return account_name, "UNKNOWN", BankAccountType.other


def create_bank_accounts(db) -> list[BankAccount]:
    """Create BankAccount records from GL bank accounts."""
    print("\n--- Linking GL Bank Accounts to Banking Module ---\n")

    # Get all GL accounts with subledger_type='BANK'
    gl_bank_accounts = (
        db.query(Account)
        .filter(
            Account.organization_id == ORG_ID,
            Account.subledger_type == "BANK",
            Account.is_active.is_(True),
        )
        .order_by(Account.account_code)
        .all()
    )

    if not gl_bank_accounts:
        print("  No GL bank accounts found (subledger_type='BANK').")
        print("  Run the TB import script first: python scripts/import_dotmac_tb.py")
        return []

    print(f"  Found {len(gl_bank_accounts)} GL bank accounts\n")

    created = []
    skipped = []

    for gl_account in gl_bank_accounts:
        # Check if BankAccount already exists for this GL account
        existing = (
            db.query(BankAccount)
            .filter(BankAccount.gl_account_id == gl_account.account_id)
            .first()
        )

        if existing:
            skipped.append(gl_account)
            print(f"  [exists] {gl_account.account_code} - {gl_account.account_name}")
            continue

        # Derive bank info from account name
        bank_name, bank_code, account_type = get_bank_info(gl_account.account_name)

        # Create BankAccount record
        bank_account = BankAccount(
            organization_id=ORG_ID,
            gl_account_id=gl_account.account_id,
            bank_name=bank_name,
            bank_code=bank_code,
            account_name=gl_account.account_name,
            # Use GL account code as placeholder account number
            account_number=f"GL-{gl_account.account_code}",
            account_type=account_type,
            currency_code=gl_account.default_currency_code or "NGN",
            status=BankAccountStatus.active,
            notes=f"Auto-created from GL account {gl_account.account_code}",
        )

        db.add(bank_account)
        created.append(bank_account)
        print(f"  [created] {gl_account.account_code} - {gl_account.account_name}")
        print(f"            -> Bank: {bank_name} ({bank_code})")

    if created:
        db.commit()

    return created


def main():
    print("=" * 60)
    print(" LINK GL BANK ACCOUNTS TO BANKING MODULE")
    print("=" * 60)

    db = SessionLocal()
    try:
        created = create_bank_accounts(db)

        print("\n" + "=" * 60)
        print(" SUMMARY")
        print("=" * 60)
        print(f"\n  Bank accounts created: {len(created)}")

        if created:
            print("\n  You can now:")
            print("  - Use bank accounts in Paystack settings dropdowns")
            print("  - Import bank statements for reconciliation")
            print("  - Set up auto-categorization rules")
            print("\n  To update account numbers and bank details:")
            print("  - Go to Finance > Banking > Bank Accounts")
            print("  - Edit each account with actual bank details")

    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
