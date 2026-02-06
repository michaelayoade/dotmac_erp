"""
Import/Export Services for Data Migration.

This module provides tools to import data from CSV exports (Zoho Books, QuickBooks,
Sage, or any other accounting system) into the IFRS-based accounting system.

Supported entity types:
- Chart of Accounts (Account Categories + Accounts)
- Contacts (Customers and Suppliers/Vendors)
- Inventory (Item Categories + Items)
- Fixed Assets (Asset Categories + Assets)
- Bank Accounts
- Invoices (Customer Invoices)
- Expenses
- Payments (Customer and Supplier)

Import order recommendation:
1. Chart of Accounts
2. Contacts (Customers, Suppliers)
3. Inventory Items
4. Fixed Assets
5. Bank Accounts
6. Invoices
7. Expenses
8. Payments
"""

from .accounts import AccountCategoryImporter, AccountImporter
from .assets import AssetCategoryImporter, AssetImporter
from .banking import BankAccountImporter
from .base import (
    # Column alias utilities
    COLUMN_ALIASES,
    VALID_ACCOUNT_TYPES,
    VALID_CURRENCY_CODES,
    BaseImporter,
    ColumnMapping,
    FieldMapping,
    ImportConfig,
    ImportError,
    ImportResult,
    ImportStatus,
    ImportWarning,
    # New preview and validation classes
    PreviewResult,
    ValidationRule,
    detect_csv_format,
    find_account_by_name_pattern,
    # Account lookup utilities
    find_account_by_subledger_type,
    resolve_column_alias,
)
from .contacts import (
    CustomerImporter,
    SupplierImporter,
    get_ap_control_account,
    get_ar_control_account,
)
from .expenses import ExpenseImporter
from .invoices import InvoiceImporter
from .items import ItemCategoryImporter, ItemImporter
from .opening_balance import (
    OpeningBalanceImporter,
    OpeningBalanceLine,
    OpeningBalancePreview,
    OpeningBalanceResult,
    get_opening_balance_template,
)
from .payments import CustomerPaymentImporter, SupplierPaymentImporter

__all__ = [
    # Base classes
    "BaseImporter",
    "ImportResult",
    "ImportError",
    "ImportWarning",
    "ImportConfig",
    "ImportStatus",
    "FieldMapping",
    # Preview and validation
    "PreviewResult",
    "ColumnMapping",
    "ValidationRule",
    # Column alias utilities
    "COLUMN_ALIASES",
    "VALID_CURRENCY_CODES",
    "VALID_ACCOUNT_TYPES",
    "detect_csv_format",
    "resolve_column_alias",
    # Account lookup utilities
    "find_account_by_subledger_type",
    "find_account_by_name_pattern",
    # Account importers
    "AccountImporter",
    "AccountCategoryImporter",
    # Contact importers
    "CustomerImporter",
    "SupplierImporter",
    "get_ar_control_account",
    "get_ap_control_account",
    # Inventory importers
    "ItemImporter",
    "ItemCategoryImporter",
    # Asset importers
    "AssetImporter",
    "AssetCategoryImporter",
    # Banking importers
    "BankAccountImporter",
    # Transaction importers
    "InvoiceImporter",
    "ExpenseImporter",
    "CustomerPaymentImporter",
    "SupplierPaymentImporter",
    # Opening Balance importer
    "OpeningBalanceImporter",
    "OpeningBalanceLine",
    "OpeningBalancePreview",
    "OpeningBalanceResult",
    "get_opening_balance_template",
]
