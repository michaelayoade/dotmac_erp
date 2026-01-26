"""
Chart of Accounts Importer.

Imports accounts from Zoho Books CSV export into the IFRS-based chart of accounts.
"""

from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account, AccountType, NormalBalance
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

from .base import BaseImporter, FieldMapping, ImportConfig


# Mapping from Zoho account types to IFRS categories and normal balance
ZOHO_ACCOUNT_TYPE_MAPPING = {
    # Revenue accounts (Credit)
    "Income": (IFRSCategory.REVENUE, NormalBalance.CREDIT),
    "Other Income": (IFRSCategory.REVENUE, NormalBalance.CREDIT),
    # Expense accounts (Debit)
    "Expense": (IFRSCategory.EXPENSES, NormalBalance.DEBIT),
    "Other Expense": (IFRSCategory.EXPENSES, NormalBalance.DEBIT),
    "Cost Of Goods Sold": (IFRSCategory.EXPENSES, NormalBalance.DEBIT),
    # Asset accounts (Debit)
    "Cash": (IFRSCategory.ASSETS, NormalBalance.DEBIT),
    "Bank": (IFRSCategory.ASSETS, NormalBalance.DEBIT),
    "Accounts Receivable": (IFRSCategory.ASSETS, NormalBalance.DEBIT),
    "Other Current Asset": (IFRSCategory.ASSETS, NormalBalance.DEBIT),
    "Fixed Asset": (IFRSCategory.ASSETS, NormalBalance.DEBIT),
    "Stock": (IFRSCategory.ASSETS, NormalBalance.DEBIT),
    "Input Tax": (IFRSCategory.ASSETS, NormalBalance.DEBIT),
    "Payment Clearing": (IFRSCategory.ASSETS, NormalBalance.DEBIT),
    # Liability accounts (Credit)
    "Accounts Payable": (IFRSCategory.LIABILITIES, NormalBalance.CREDIT),
    "Other Current Liability": (IFRSCategory.LIABILITIES, NormalBalance.CREDIT),
    "Long Term Liability": (IFRSCategory.LIABILITIES, NormalBalance.CREDIT),
    "Other Liability": (IFRSCategory.LIABILITIES, NormalBalance.CREDIT),
    "Output Tax": (IFRSCategory.LIABILITIES, NormalBalance.CREDIT),
    # Equity accounts (Credit)
    "Equity": (IFRSCategory.EQUITY, NormalBalance.CREDIT),
}

# Subledger type mapping
ZOHO_SUBLEDGER_MAPPING = {
    "Accounts Receivable": "AR",
    "Accounts Payable": "AP",
    "Stock": "INVENTORY",
    "Fixed Asset": "ASSET",
    "Bank": "BANK",
    "Cash": "BANK",
}


class AccountCategoryImporter(BaseImporter[AccountCategory]):
    """
    Importer for account categories (derived from Zoho account types).

    This creates the category hierarchy based on unique Zoho account types.
    """

    entity_name = "Account Category"
    model_class = AccountCategory

    def __init__(self, db: Session, config: ImportConfig):
        super().__init__(db, config)
        self._category_cache: Dict[str, UUID] = {}

    def get_field_mappings(self) -> List[FieldMapping]:
        """Categories are derived from account types, not direct mappings."""
        return []

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        """Unique key is the Zoho account type."""
        return str(row.get("Account Type", "") or "").strip()

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[AccountCategory]:
        """Check if category already exists."""
        account_type = self.get_unique_key(row)
        if not account_type:
            return None

        # Generate category code from account type
        category_code = self._make_category_code(account_type)

        # Check cache first
        if category_code in self._category_cache:
            return self.db.get(AccountCategory, self._category_cache[category_code])

        # Check database
        existing = self.db.execute(
            select(AccountCategory).where(
                AccountCategory.organization_id == self.config.organization_id,
                AccountCategory.category_code == category_code,
            )
        ).scalar_one_or_none()

        if existing:
            self._category_cache[category_code] = existing.category_id

        return existing

    def create_entity(self, row: Dict[str, Any]) -> AccountCategory:
        """Create a new account category from Zoho account type."""
        account_type = str(row.get("Account Type", "") or "").strip()
        category_code = self._make_category_code(account_type)

        ifrs_category, _ = ZOHO_ACCOUNT_TYPE_MAPPING.get(
            account_type, (IFRSCategory.EXPENSES, NormalBalance.DEBIT)
        )

        category = AccountCategory(
            category_id=uuid4(),
            organization_id=self.config.organization_id,
            category_code=category_code,
            category_name=account_type,
            description=f"Imported from Zoho Books - {account_type}",
            ifrs_category=ifrs_category,
            hierarchy_level=1,
            display_order=self._get_display_order(ifrs_category),
            is_active=True,
        )

        self._category_cache[category_code] = category.category_id
        return category

    def _make_category_code(self, account_type: str) -> str:
        """Generate a category code from Zoho account type."""
        return account_type.upper().replace(" ", "_")[:20]

    def _get_display_order(self, ifrs_category: IFRSCategory) -> int:
        """Get display order based on IFRS category."""
        order_map = {
            IFRSCategory.ASSETS: 100,
            IFRSCategory.LIABILITIES: 200,
            IFRSCategory.EQUITY: 300,
            IFRSCategory.REVENUE: 400,
            IFRSCategory.EXPENSES: 500,
            IFRSCategory.OTHER_COMPREHENSIVE_INCOME: 600,
        }
        return order_map.get(ifrs_category, 999)

    def get_category_id(self, account_type: str) -> Optional[UUID]:
        """Get the category ID for a given Zoho account type."""
        category_code = self._make_category_code(account_type)
        return self._category_cache.get(category_code)

    def ensure_categories(self, rows: List[Dict[str, Any]]) -> None:
        """Ensure all required categories exist before importing accounts."""
        unique_types = set()
        for row in rows:
            account_type = str(row.get("Account Type", "") or "").strip()
            if account_type:
                unique_types.add(account_type)

        for account_type in unique_types:
            row = {"Account Type": account_type}
            if not self.check_duplicate(row):
                category = self.create_entity(row)
                self.db.add(category)
                self.db.flush()


class AccountImporter(BaseImporter[Account]):
    """
    Importer for chart of accounts from Zoho Books CSV export.

    CSV Format (Zoho Books):
    - Account ID: Zoho internal ID (ignored, we generate our own)
    - Account Name: Name of the account
    - Account Code: Account code (may be empty)
    - Description: Account description
    - Account Type: Zoho account type (mapped to IFRS category)
    - Account Status: Active/Inactive
    - Currency: Currency code (e.g., NGN)
    - Parent Account: Parent account name (for hierarchy)
    """

    entity_name = "Account"
    model_class = Account

    def __init__(self, db: Session, config: ImportConfig):
        super().__init__(db, config)
        self._category_importer = AccountCategoryImporter(db, config)
        self._account_code_counter = 0
        self._parent_cache: Dict[str, UUID] = {}

    def get_field_mappings(self) -> List[FieldMapping]:
        """Define field mappings from Zoho CSV to Account model."""
        return [
            FieldMapping("Account Name", "account_name", required=True),
            FieldMapping("Account Code", "account_code", required=False),
            FieldMapping("Description", "description", required=False),
            FieldMapping("Account Type", "zoho_account_type", required=True),
            FieldMapping("Account Status", "is_active", required=False,
                         transformer=lambda v: v != "Inactive", default=True),
            FieldMapping("Currency", "default_currency_code", required=False),
            FieldMapping("Parent Account", "parent_account_name", required=False),
        ]

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        """Unique key is account code or account name."""
        code = str(row.get("Account Code", "") or "").strip()
        if code:
            return code
        return str(row.get("Account Name", "") or "").strip()

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[Account]:
        """Check if account already exists by code or name."""
        code = str(row.get("Account Code", "") or "").strip()
        name = str(row.get("Account Name", "") or "").strip()

        # Check by code first
        if code:
            existing = self.db.execute(
                select(Account).where(
                    Account.organization_id == self.config.organization_id,
                    Account.account_code == code,
                )
            ).scalar_one_or_none()
            if existing:
                return existing

        # Check by name
        if name:
            existing = self.db.execute(
                select(Account).where(
                    Account.organization_id == self.config.organization_id,
                    Account.account_name == name,
                )
            ).scalar_one_or_none()
            if existing:
                return existing

        return None

    def create_entity(self, row: Dict[str, Any]) -> Account:
        """Create a new account from transformed row data."""
        zoho_type = row.get("zoho_account_type", "Expense")
        ifrs_category, normal_balance = ZOHO_ACCOUNT_TYPE_MAPPING.get(
            zoho_type, (IFRSCategory.EXPENSES, NormalBalance.DEBIT)
        )

        # Get or create category
        category_id = self._category_importer.get_category_id(zoho_type)
        if not category_id:
            # Category should have been created by ensure_categories
            # If not, create it now
            cat_row = {"Account Type": zoho_type}
            category = self._category_importer.create_entity(cat_row)
            self.db.add(category)
            self.db.flush()
            category_id = category.category_id

        # Generate account code if not provided
        account_code = row.get("account_code")
        if not account_code:
            self._account_code_counter += 1
            prefix = self._get_code_prefix(ifrs_category)
            account_code = f"{prefix}{self._account_code_counter:04d}"

        # Determine subledger type
        subledger_type = ZOHO_SUBLEDGER_MAPPING.get(zoho_type)

        # Determine special flags
        is_cash_equivalent = zoho_type in ("Cash", "Bank")
        is_reconciliation_required = zoho_type == "Bank"

        account = Account(
            account_id=uuid4(),
            organization_id=self.config.organization_id,
            category_id=category_id,
            account_code=account_code[:20],  # Ensure max length
            account_name=row.get("account_name", "")[:200],
            description=row.get("description"),
            account_type=AccountType.POSTING,
            normal_balance=normal_balance,
            is_active=row.get("is_active", True),
            is_posting_allowed=True,
            is_budgetable=True,
            is_reconciliation_required=is_reconciliation_required,
            is_multi_currency=False,
            default_currency_code=row.get("default_currency_code"),
            subledger_type=subledger_type,
            is_cash_equivalent=is_cash_equivalent,
            is_financial_instrument=False,
            created_by_user_id=self.config.user_id,
        )

        # Cache for parent account lookup
        self._parent_cache[row.get("account_name", "")] = account.account_id

        return account

    def _get_code_prefix(self, ifrs_category: IFRSCategory) -> str:
        """Get account code prefix based on IFRS category."""
        prefix_map = {
            IFRSCategory.ASSETS: "1",
            IFRSCategory.LIABILITIES: "2",
            IFRSCategory.EQUITY: "3",
            IFRSCategory.REVENUE: "4",
            IFRSCategory.EXPENSES: "5",
            IFRSCategory.OTHER_COMPREHENSIVE_INCOME: "6",
        }
        return prefix_map.get(ifrs_category, "9")

    def import_file(self, file_path):
        """Override to ensure categories are created first."""
        import csv
        from pathlib import Path

        file_path = Path(file_path)
        if not file_path.exists():
            self.result.add_error(0, f"File not found: {file_path}", None)
            return self.result

        # Read all rows first
        with open(file_path, "r", encoding=self.config.encoding) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # Ensure categories exist
        self._category_importer.ensure_categories(rows)
        self.db.flush()

        # Now import accounts
        return super().import_rows(rows)
