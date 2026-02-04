#!/usr/bin/env python3
"""
Bulk Import Script for Dotmac Books
Imports all prepared Zoho Books data (2022-2024) into IFRS-compliant models

Usage:
    python scripts/bulk_import.py --org-id <org_uuid> --user-id <user_uuid> [options]

Options:
    --dry-run       Validate without saving
    --entity TYPE   Import only specific entity type
    --skip-errors   Continue on errors
"""

import argparse
import csv
import sys
import os
from datetime import datetime, date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, Dict, Any, List
from uuid import UUID
import hashlib

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models.finance.gl.account import Account, AccountType, NormalBalance
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_year import FiscalYear
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.journal_entry import JournalEntry, JournalType, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.ar.customer import Customer, CustomerType, RiskCategory
from app.models.finance.ar.invoice import Invoice, InvoiceType, InvoiceStatus
from app.models.finance.ar.invoice_line import InvoiceLine
from app.models.finance.ar.customer_payment import CustomerPayment, PaymentMethod, PaymentStatus
from app.models.finance.ap.supplier import Supplier, SupplierType
from app.models.finance.ap.supplier_invoice import SupplierInvoice, SupplierInvoiceType, SupplierInvoiceStatus
from app.models.finance.ap.supplier_invoice_line import SupplierInvoiceLine
from app.models.finance.ap.supplier_payment import SupplierPayment, APPaymentMethod, APPaymentStatus
from app.models.inventory.item import Item, ItemType, CostingMethod
from app.models.inventory.item_category import ItemCategory


# Import paths
IMPORT_PATH = Path("/Users/michaelayoade/Downloads/Projects/dotmac_erp/books_backup/import_ready")


class BulkImporter:
    """Handles bulk import of Zoho Books data into IFRS-compliant models"""

    def __init__(self, db: Session, org_id: UUID, user_id: UUID, dry_run: bool = False):
        self.db = db
        self.org_id = org_id
        self.user_id = user_id
        self.dry_run = dry_run
        self.stats = {
            'imported': 0,
            'skipped': 0,
            'errors': 0,
        }
        self.errors: List[str] = []

        # Cache for accounts
        self._account_cache: Dict[str, UUID] = {}
        self._account_code_cache: Dict[str, UUID] = {}
        self._ar_account_id: Optional[UUID] = None
        self._ap_account_id: Optional[UUID] = None
        self._inventory_account_id: Optional[UUID] = None
        self._cogs_account_id: Optional[UUID] = None
        self._revenue_account_id: Optional[UUID] = None
        self._expense_account_id: Optional[UUID] = None
        self._default_item_category_id: Optional[UUID] = None
        self._bank_account_id: Optional[UUID] = None

        # Cache for entities
        self._customer_cache: Dict[str, UUID] = {}
        self._supplier_cache: Dict[str, UUID] = {}
        self._fiscal_period_cache: Dict[str, UUID] = {}

    def log(self, msg: str):
        print(f"  {msg}")

    def _generate_code(self, prefix: str, name: str, index: int) -> str:
        """Generate a unique code from name"""
        return f"{prefix}{index:05d}"

    def _setup_default_accounts(self):
        """Cache default account IDs for lookups"""
        # AR Control Account
        ar_account = self.db.query(Account).filter(
            Account.organization_id == self.org_id,
            Account.account_name.ilike('%accounts receivable%')
        ).first()
        if ar_account:
            self._ar_account_id = ar_account.account_id
        else:
            ar_account = self.db.query(Account).filter(
                Account.organization_id == self.org_id,
                Account.account_name.ilike('%trade receivable%')
            ).first()
            if ar_account:
                self._ar_account_id = ar_account.account_id

        # AP Control Account
        ap_account = self.db.query(Account).filter(
            Account.organization_id == self.org_id,
            Account.account_name.ilike('%accounts payable%')
        ).first()
        if ap_account:
            self._ap_account_id = ap_account.account_id
        else:
            ap_account = self.db.query(Account).filter(
                Account.organization_id == self.org_id,
                Account.account_name.ilike('%trade payable%')
            ).first()
            if ap_account:
                self._ap_account_id = ap_account.account_id

        # Inventory Account
        inv_account = self.db.query(Account).filter(
            Account.organization_id == self.org_id,
            Account.account_name.ilike('%inventory%')
        ).first()
        if not inv_account:
            inv_account = self.db.query(Account).filter(
                Account.organization_id == self.org_id,
                Account.account_name.ilike('%stock%')
            ).first()
        if inv_account:
            self._inventory_account_id = inv_account.account_id

        # COGS Account
        cogs_account = self.db.query(Account).filter(
            Account.organization_id == self.org_id,
            Account.account_name.ilike('%cost of goods%')
        ).first()
        if not cogs_account:
            cogs_account = self.db.query(Account).filter(
                Account.organization_id == self.org_id,
                Account.account_name.ilike('%cost of sales%')
            ).first()
        if cogs_account:
            self._cogs_account_id = cogs_account.account_id

        # Revenue Account
        rev_account = self.db.query(Account).filter(
            Account.organization_id == self.org_id,
            Account.account_name.ilike('%sales%')
        ).first()
        if not rev_account:
            rev_account = self.db.query(Account).filter(
                Account.organization_id == self.org_id,
                Account.account_name.ilike('%revenue%')
            ).first()
        if rev_account:
            self._revenue_account_id = rev_account.account_id

        # Expense Account
        exp_account = self.db.query(Account).filter(
            Account.organization_id == self.org_id,
            Account.account_name.ilike('%expense%')
        ).first()
        if exp_account:
            self._expense_account_id = exp_account.account_id

        # Bank Account
        bank_account = self.db.query(Account).filter(
            Account.organization_id == self.org_id,
            Account.account_name.ilike('%bank%')
        ).first()
        if bank_account:
            self._bank_account_id = bank_account.account_id

        # Build account name cache
        accounts = self.db.query(Account).filter(
            Account.organization_id == self.org_id
        ).all()
        for acc in accounts:
            self._account_cache[acc.account_name.lower()] = acc.account_id
            if acc.account_code:
                self._account_code_cache[acc.account_code] = acc.account_id

        self.log(f"AR Account: {self._ar_account_id}")
        self.log(f"AP Account: {self._ap_account_id}")
        self.log(f"Inventory Account: {self._inventory_account_id}")
        self.log(f"COGS Account: {self._cogs_account_id}")
        self.log(f"Revenue Account: {self._revenue_account_id}")

    def _build_customer_cache(self):
        """Build customer name to ID cache"""
        customers = self.db.query(Customer).filter(
            Customer.organization_id == self.org_id
        ).all()
        for c in customers:
            self._customer_cache[c.legal_name.lower()] = c.customer_id

    def _build_supplier_cache(self):
        """Build supplier name to ID cache"""
        suppliers = self.db.query(Supplier).filter(
            Supplier.organization_id == self.org_id
        ).all()
        for s in suppliers:
            self._supplier_cache[s.legal_name.lower()] = s.supplier_id

    def _get_customer_id(self, name: str) -> Optional[UUID]:
        """Look up customer by name"""
        if not self._customer_cache:
            self._build_customer_cache()
        return self._customer_cache.get(name.lower().strip())

    def _get_supplier_id(self, name: str) -> Optional[UUID]:
        """Look up supplier by name"""
        if not self._supplier_cache:
            self._build_supplier_cache()
        return self._supplier_cache.get(name.lower().strip())

    def _get_account_id(self, name: str = None, code: str = None) -> Optional[UUID]:
        """Look up account by name or code"""
        if code and code in self._account_code_cache:
            return self._account_code_cache[code]
        if name and name.lower() in self._account_cache:
            return self._account_cache[name.lower()]
        return None

    def _ensure_fiscal_periods(self):
        """Create fiscal years and periods for 2022-2024"""
        if self.dry_run:
            return

        for year in [2022, 2023, 2024]:
            # Check if year exists
            fy = self.db.query(FiscalYear).filter(
                FiscalYear.organization_id == self.org_id,
                FiscalYear.year_code == str(year)
            ).first()

            if not fy:
                fy = FiscalYear(
                    organization_id=self.org_id,
                    year_code=str(year),
                    year_name=f"Fiscal Year {year}",
                    start_date=date(year, 1, 1),
                    end_date=date(year, 12, 31),
                )
                self.db.add(fy)
                self.db.flush()

            # Create periods for each month
            for month in range(1, 13):
                period_key = f"{year}-{month:02d}"
                if period_key in self._fiscal_period_cache:
                    continue

                fp = self.db.query(FiscalPeriod).filter(
                    FiscalPeriod.fiscal_year_id == fy.fiscal_year_id,
                    FiscalPeriod.period_number == month
                ).first()

                if not fp:
                    # Calculate period dates
                    start = date(year, month, 1)
                    if month == 12:
                        end = date(year, 12, 31)
                    else:
                        end = date(year, month + 1, 1) - timedelta(days=1)

                    fp = FiscalPeriod(
                        organization_id=self.org_id,
                        fiscal_year_id=fy.fiscal_year_id,
                        period_number=month,
                        period_name=start.strftime("%B %Y"),
                        start_date=start,
                        end_date=end,
                        status=PeriodStatus.OPEN,
                    )
                    self.db.add(fp)
                    self.db.flush()

                self._fiscal_period_cache[period_key] = fp.fiscal_period_id

        self.db.commit()

    def _get_fiscal_period_id(self, trans_date: date) -> Optional[UUID]:
        """Get fiscal period ID for a date"""
        period_key = f"{trans_date.year}-{trans_date.month:02d}"
        if period_key in self._fiscal_period_cache:
            return self._fiscal_period_cache[period_key]

        fp = self.db.query(FiscalPeriod).filter(
            FiscalPeriod.organization_id == self.org_id,
            FiscalPeriod.start_date <= trans_date,
            FiscalPeriod.end_date >= trans_date
        ).first()

        if fp:
            self._fiscal_period_cache[period_key] = fp.fiscal_period_id
            return fp.fiscal_period_id
        return None

    def _ensure_default_item_category(self) -> Optional[UUID]:
        """Create or get default item category"""
        if self._default_item_category_id:
            return self._default_item_category_id

        category = self.db.query(ItemCategory).filter(
            ItemCategory.organization_id == self.org_id,
            ItemCategory.category_code == 'DEFAULT'
        ).first()

        if category:
            self._default_item_category_id = category.category_id
            return self._default_item_category_id

        if not all([self._inventory_account_id, self._cogs_account_id,
                    self._revenue_account_id, self._expense_account_id]):
            return None

        if not self.dry_run:
            category = ItemCategory(
                organization_id=self.org_id,
                category_code='DEFAULT',
                category_name='Default Category',
                description='Default item category for imported items',
                inventory_account_id=self._inventory_account_id,
                cogs_account_id=self._cogs_account_id,
                revenue_account_id=self._revenue_account_id,
                inventory_adjustment_account_id=self._expense_account_id,
                is_active=True,
            )
            self.db.add(category)
            self.db.flush()
            self._default_item_category_id = category.category_id

        return self._default_item_category_id

    def import_chart_of_accounts(self) -> Dict[str, Any]:
        """Import Chart of Accounts"""
        print("\n" + "="*60)
        print("IMPORTING CHART OF ACCOUNTS")
        print("="*60)

        filepath = IMPORT_PATH / "chart_of_accounts.csv"
        if not filepath.exists():
            self.log("File not found!")
            return {'imported': 0, 'errors': ['File not found']}

        stats = {'imported': 0, 'skipped': 0, 'errors': []}
        categories_created = {}  # name -> category_id
        category_index = 1
        account_index = 1

        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    account_name = row.get('Account Name', '').strip()
                    zoho_account_type = row.get('Account Type', '').strip()
                    account_code = row.get('Account Code', '').strip()

                    if not account_name:
                        continue

                    existing = self.db.query(Account).filter(
                        Account.organization_id == self.org_id,
                        Account.account_name == account_name
                    ).first()

                    if existing:
                        stats['skipped'] += 1
                        continue

                    # Map to IFRS category
                    ifrs_category = self._map_to_ifrs_category(zoho_account_type)
                    normal_balance = self._get_normal_balance(ifrs_category)

                    category_name = zoho_account_type or 'Other'
                    if category_name not in categories_created:
                        existing_cat = self.db.query(AccountCategory).filter(
                            AccountCategory.organization_id == self.org_id,
                            AccountCategory.category_name == category_name
                        ).first()
                        if existing_cat:
                            categories_created[category_name] = existing_cat.category_id
                        elif not self.dry_run:
                            # Generate category code
                            cat_code = f"CAT{category_index:03d}"
                            category_index += 1

                            cat = AccountCategory(
                                organization_id=self.org_id,
                                category_code=cat_code,
                                category_name=category_name,
                                ifrs_category=ifrs_category,
                            )
                            self.db.add(cat)
                            self.db.flush()
                            categories_created[category_name] = cat.category_id

                    if not self.dry_run:
                        category_id = categories_created.get(category_name)
                        if not category_id:
                            category = self.db.query(AccountCategory).filter(
                                AccountCategory.organization_id == self.org_id,
                                AccountCategory.category_name == category_name
                            ).first()
                            category_id = category.category_id if category else None

                        # Generate account code if not provided
                        if not account_code:
                            account_code = f"ACC{account_index:05d}"
                        account_index += 1

                        account = Account(
                            organization_id=self.org_id,
                            category_id=category_id,
                            account_name=account_name,
                            account_code=account_code,
                            account_type=AccountType.POSTING,
                            normal_balance=normal_balance,
                            is_active=True,
                        )
                        self.db.add(account)

                    stats['imported'] += 1

                except Exception as e:
                    stats['errors'].append(f"Row {account_name}: {str(e)}")

        if not self.dry_run:
            self.db.commit()
            self._setup_default_accounts()

        self.log(f"Imported: {stats['imported']}, Skipped: {stats['skipped']}, Errors: {len(stats['errors'])}")
        return stats

    def import_customers(self) -> Dict[str, Any]:
        """Import Customers/Contacts into IFRS Customer model"""
        print("\n" + "="*60)
        print("IMPORTING CUSTOMERS")
        print("="*60)

        filepath = IMPORT_PATH / "customers.csv"
        if not filepath.exists():
            self.log("File not found!")
            return {'imported': 0, 'errors': ['File not found']}

        if not self._ar_account_id:
            self._setup_default_accounts()

        if not self._ar_account_id and not self.dry_run:
            self.log("ERROR: No AR control account found. Import accounts first.")
            return {'imported': 0, 'errors': ['No AR control account']}
        elif not self._ar_account_id:
            self.log("(Dry-run: AR account would be available after accounts import)")

        stats = {'imported': 0, 'skipped': 0, 'errors': []}
        customer_index = 1

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                try:
                    display_name = row.get('Display Name', '').strip() or row.get('Contact Name', '').strip()
                    if not display_name:
                        continue

                    existing = self.db.query(Customer).filter(
                        Customer.organization_id == self.org_id,
                        Customer.legal_name == display_name
                    ).first()

                    if existing:
                        stats['skipped'] += 1
                        continue

                    email = row.get('EmailID', '').strip() or row.get('Email', '').strip()
                    phone = row.get('Phone', '').strip() or row.get('MobilePhone', '').strip()
                    primary_contact = {}
                    if email:
                        primary_contact['email'] = email
                    if phone:
                        primary_contact['phone'] = phone
                    if row.get('First Name', '').strip():
                        primary_contact['first_name'] = row.get('First Name', '').strip()
                    if row.get('Last Name', '').strip():
                        primary_contact['last_name'] = row.get('Last Name', '').strip()

                    billing_address = {}
                    if row.get('Billing Address', '').strip():
                        billing_address['line1'] = row.get('Billing Address', '').strip()
                    if row.get('Billing City', '').strip():
                        billing_address['city'] = row.get('Billing City', '').strip()
                    if row.get('Billing State', '').strip():
                        billing_address['state'] = row.get('Billing State', '').strip()
                    if row.get('Billing Country', '').strip():
                        billing_address['country'] = row.get('Billing Country', '').strip()

                    company_name = row.get('Company Name', '').strip()
                    customer_type = CustomerType.COMPANY if company_name else CustomerType.INDIVIDUAL

                    currency = row.get('Currency Code', 'NGN').strip() or 'NGN'
                    if len(currency) > 3:
                        currency = 'NGN'

                    if not self.dry_run:
                        customer_code = self._generate_code('CUS', display_name, customer_index)

                        customer = Customer(
                            organization_id=self.org_id,
                            customer_code=customer_code,
                            customer_type=customer_type,
                            legal_name=display_name[:255],
                            trading_name=company_name[:255] if company_name else None,
                            currency_code=currency,
                            ar_control_account_id=self._ar_account_id,
                            risk_category=RiskCategory.MEDIUM,
                            credit_terms_days=30,
                            primary_contact=primary_contact or None,
                            billing_address=billing_address or None,
                            is_active=row.get('Status', 'Active').strip().lower() == 'active',
                        )
                        batch.append(customer)
                        customer_index += 1

                        if len(batch) >= 100:
                            self.db.add_all(batch)
                            self.db.flush()
                            batch = []

                    stats['imported'] += 1

                except Exception as e:
                    stats['errors'].append(f"Row {display_name}: {str(e)}")

            if batch and not self.dry_run:
                self.db.add_all(batch)

        if not self.dry_run:
            self.db.commit()
            self._build_customer_cache()

        self.log(f"Imported: {stats['imported']}, Skipped: {stats['skipped']}, Errors: {len(stats['errors'])}")
        return stats

    def import_vendors(self) -> Dict[str, Any]:
        """Import Vendors/Suppliers into IFRS Supplier model"""
        print("\n" + "="*60)
        print("IMPORTING VENDORS")
        print("="*60)

        filepath = IMPORT_PATH / "vendors.csv"
        if not filepath.exists():
            self.log("File not found!")
            return {'imported': 0, 'errors': ['File not found']}

        if not self._ap_account_id:
            self._setup_default_accounts()

        if not self._ap_account_id and not self.dry_run:
            self.log("ERROR: No AP control account found. Import accounts first.")
            return {'imported': 0, 'errors': ['No AP control account']}
        elif not self._ap_account_id:
            self.log("(Dry-run: AP account would be available after accounts import)")

        stats = {'imported': 0, 'skipped': 0, 'errors': []}
        supplier_index = 1

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                try:
                    vendor_name = row.get('Display Name', '').strip() or row.get('Contact Name', '').strip()
                    if not vendor_name:
                        continue

                    existing = self.db.query(Supplier).filter(
                        Supplier.organization_id == self.org_id,
                        Supplier.legal_name == vendor_name
                    ).first()

                    if existing:
                        stats['skipped'] += 1
                        continue

                    email = row.get('EmailID', '').strip() or row.get('Email', '').strip()
                    phone = row.get('Phone', '').strip() or row.get('MobilePhone', '').strip()
                    primary_contact = {}
                    if email:
                        primary_contact['email'] = email
                    if phone:
                        primary_contact['phone'] = phone

                    billing_address = {}
                    if row.get('Billing Address', '').strip():
                        billing_address['line1'] = row.get('Billing Address', '').strip()
                    if row.get('Billing City', '').strip():
                        billing_address['city'] = row.get('Billing City', '').strip()

                    currency = row.get('Currency Code', 'NGN').strip() or 'NGN'
                    if len(currency) > 3:
                        currency = 'NGN'

                    if not self.dry_run:
                        supplier_code = self._generate_code('VEN', vendor_name, supplier_index)

                        supplier = Supplier(
                            organization_id=self.org_id,
                            supplier_code=supplier_code,
                            supplier_type=SupplierType.VENDOR,
                            legal_name=vendor_name[:255],
                            trading_name=row.get('Company Name', '').strip()[:255] or None,
                            currency_code=currency,
                            ap_control_account_id=self._ap_account_id,
                            payment_terms_days=30,
                            primary_contact=primary_contact or None,
                            billing_address=billing_address or None,
                            is_active=row.get('Status', 'Active').strip().lower() == 'active',
                        )
                        batch.append(supplier)
                        supplier_index += 1

                        if len(batch) >= 100:
                            self.db.add_all(batch)
                            self.db.flush()
                            batch = []

                    stats['imported'] += 1

                except Exception as e:
                    stats['errors'].append(f"Row {vendor_name}: {str(e)}")

            if batch and not self.dry_run:
                self.db.add_all(batch)

        if not self.dry_run:
            self.db.commit()
            self._build_supplier_cache()

        self.log(f"Imported: {stats['imported']}, Skipped: {stats['skipped']}, Errors: {len(stats['errors'])}")
        return stats

    def import_items(self) -> Dict[str, Any]:
        """Import Inventory Items into IFRS Item model"""
        print("\n" + "="*60)
        print("IMPORTING ITEMS")
        print("="*60)

        filepath = IMPORT_PATH / "items.csv"
        if not filepath.exists():
            self.log("File not found!")
            return {'imported': 0, 'errors': ['File not found']}

        if not self._inventory_account_id:
            self._setup_default_accounts()

        category_id = self._ensure_default_item_category()
        if not category_id and not self.dry_run:
            self.log("ERROR: Cannot create default item category. Missing required accounts.")
            return {'imported': 0, 'errors': ['Cannot create item category']}

        stats = {'imported': 0, 'skipped': 0, 'errors': []}
        item_index = 1

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            batch = []
            for row in reader:
                try:
                    item_name = row.get('Item Name', '').strip() or row.get('Name', '').strip()
                    if not item_name:
                        continue

                    existing = self.db.query(Item).filter(
                        Item.organization_id == self.org_id,
                        Item.item_name == item_name
                    ).first()

                    if existing:
                        stats['skipped'] += 1
                        continue

                    product_type = row.get('Product Type', '').strip().upper()
                    if 'SERVICE' in product_type:
                        item_type = ItemType.SERVICE
                    elif 'NON' in product_type or 'GOODS' in product_type:
                        item_type = ItemType.NON_INVENTORY
                    else:
                        item_type = ItemType.INVENTORY

                    uom = row.get('Unit', '').strip() or row.get('UOM', '').strip() or 'EACH'

                    if not self.dry_run:
                        item_code = row.get('SKU', '').strip() or row.get('Item Code', '').strip()
                        if not item_code:
                            item_code = self._generate_code('ITM', item_name, item_index)

                        item = Item(
                            organization_id=self.org_id,
                            item_code=item_code[:50],
                            item_name=item_name[:200],
                            description=row.get('Description', '').strip() or None,
                            item_type=item_type,
                            category_id=category_id,
                            base_uom=uom[:20],
                            currency_code='NGN',
                            costing_method=CostingMethod.WEIGHTED_AVERAGE,
                            list_price=self._parse_decimal(row.get('Rate', 0)),
                            is_active=row.get('Status', 'Active').strip().lower() == 'active',
                            track_inventory=item_type == ItemType.INVENTORY,
                        )
                        batch.append(item)
                        item_index += 1

                        if len(batch) >= 100:
                            self.db.add_all(batch)
                            self.db.flush()
                            batch = []

                    stats['imported'] += 1

                except Exception as e:
                    stats['errors'].append(f"Row {item_name}: {str(e)}")

            if batch and not self.dry_run:
                self.db.add_all(batch)

        if not self.dry_run:
            self.db.commit()

        self.log(f"Imported: {stats['imported']}, Skipped: {stats['skipped']}, Errors: {len(stats['errors'])}")
        return stats

    def import_invoices(self) -> Dict[str, Any]:
        """Import Sales Invoices"""
        print("\n" + "="*60)
        print("IMPORTING INVOICES")
        print("="*60)

        filepath = IMPORT_PATH / "all_invoices.csv"
        if not filepath.exists():
            self.log("File not found!")
            return {'imported': 0, 'errors': ['File not found']}

        if not self._ar_account_id:
            self._setup_default_accounts()

        if not self._ar_account_id and not self.dry_run:
            self.log("ERROR: No AR control account found.")
            return {'imported': 0, 'errors': ['No AR control account']}
        elif not self._ar_account_id:
            self.log("(Dry-run: AR account would be available after accounts import)")

        if not self._customer_cache:
            self._build_customer_cache()

        stats = {'imported': 0, 'skipped': 0, 'errors': [], 'no_customer': 0}
        seen_invoices = set()

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    invoice_number = row.get('Invoice Number', '').strip()
                    if not invoice_number or invoice_number in seen_invoices:
                        continue

                    seen_invoices.add(invoice_number)

                    # Check if exists
                    existing = self.db.query(Invoice).filter(
                        Invoice.organization_id == self.org_id,
                        Invoice.invoice_number == invoice_number
                    ).first()

                    if existing:
                        stats['skipped'] += 1
                        continue

                    # Get customer
                    customer_name = row.get('Customer Name', '').strip()
                    customer_id = self._get_customer_id(customer_name)
                    if not customer_id:
                        stats['no_customer'] += 1
                        continue

                    invoice_date = self._parse_date(row.get('Invoice Date', ''))
                    due_date = self._parse_date(row.get('Due Date', ''))
                    if not invoice_date:
                        invoice_date = date.today()
                    if not due_date:
                        due_date = invoice_date + timedelta(days=30)

                    # Parse amounts
                    subtotal = self._parse_decimal(row.get('SubTotal', 0))
                    total = self._parse_decimal(row.get('Total', 0))
                    balance = self._parse_decimal(row.get('Balance', 0))
                    amount_paid = total - balance

                    # Map status
                    zoho_status = row.get('Invoice Status', '').strip().lower()
                    status = self._map_invoice_status(zoho_status, balance, total)

                    currency = row.get('Currency Code', 'NGN').strip() or 'NGN'
                    if len(currency) > 3:
                        currency = 'NGN'

                    if not self.dry_run:
                        invoice = Invoice(
                            organization_id=self.org_id,
                            customer_id=customer_id,
                            invoice_number=invoice_number[:30],
                            invoice_type=InvoiceType.STANDARD,
                            invoice_date=invoice_date,
                            due_date=due_date,
                            currency_code=currency,
                            subtotal=subtotal,
                            tax_amount=Decimal('0'),
                            total_amount=total,
                            amount_paid=amount_paid,
                            functional_currency_amount=total,
                            status=status,
                            ar_control_account_id=self._ar_account_id,
                            created_by_user_id=self.user_id,
                            posting_status='POSTED',
                        )
                        self.db.add(invoice)

                        if stats['imported'] % 1000 == 0:
                            self.db.flush()

                    stats['imported'] += 1

                except Exception as e:
                    stats['errors'].append(f"Invoice {invoice_number}: {str(e)}")

        if not self.dry_run:
            self.db.commit()

        self.log(f"Imported: {stats['imported']}, Skipped: {stats['skipped']}, No Customer: {stats['no_customer']}, Errors: {len(stats['errors'])}")
        return stats

    def import_bills(self) -> Dict[str, Any]:
        """Import Bills (Supplier Invoices)"""
        print("\n" + "="*60)
        print("IMPORTING BILLS")
        print("="*60)

        filepath = IMPORT_PATH / "bills.csv"
        if not filepath.exists():
            self.log("File not found!")
            return {'imported': 0, 'errors': ['File not found']}

        if not self._ap_account_id:
            self._setup_default_accounts()

        if not self._ap_account_id and not self.dry_run:
            self.log("ERROR: No AP control account found.")
            return {'imported': 0, 'errors': ['No AP control account']}
        elif not self._ap_account_id:
            self.log("(Dry-run: AP account would be available after accounts import)")

        if not self._supplier_cache:
            self._build_supplier_cache()

        stats = {'imported': 0, 'skipped': 0, 'errors': [], 'no_supplier': 0}
        seen_bills = set()

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    bill_number = row.get('Bill Number', '').strip()
                    if not bill_number or bill_number in seen_bills:
                        continue

                    seen_bills.add(bill_number)

                    existing = self.db.query(SupplierInvoice).filter(
                        SupplierInvoice.organization_id == self.org_id,
                        SupplierInvoice.invoice_number == bill_number
                    ).first()

                    if existing:
                        stats['skipped'] += 1
                        continue

                    vendor_name = row.get('Vendor Name', '').strip()
                    supplier_id = self._get_supplier_id(vendor_name)
                    if not supplier_id:
                        stats['no_supplier'] += 1
                        continue

                    bill_date = self._parse_date(row.get('Bill Date', ''))
                    due_date = self._parse_date(row.get('Due Date', ''))
                    if not bill_date:
                        bill_date = date.today()
                    if not due_date:
                        due_date = bill_date + timedelta(days=30)

                    subtotal = self._parse_decimal(row.get('SubTotal', 0))
                    total = self._parse_decimal(row.get('Total', 0))
                    balance = self._parse_decimal(row.get('Balance', 0))
                    amount_paid = total - balance

                    zoho_status = row.get('Bill Status', '').strip().lower()
                    status = self._map_bill_status(zoho_status, balance, total)

                    currency = row.get('Currency Code', 'NGN').strip() or 'NGN'
                    if len(currency) > 3:
                        currency = 'NGN'

                    if not self.dry_run:
                        bill = SupplierInvoice(
                            organization_id=self.org_id,
                            supplier_id=supplier_id,
                            invoice_number=bill_number[:30],
                            invoice_type=SupplierInvoiceType.STANDARD,
                            invoice_date=bill_date,
                            received_date=bill_date,
                            due_date=due_date,
                            currency_code=currency,
                            subtotal=subtotal,
                            tax_amount=Decimal('0'),
                            total_amount=total,
                            amount_paid=amount_paid,
                            functional_currency_amount=total,
                            status=status,
                            ap_control_account_id=self._ap_account_id,
                            created_by_user_id=self.user_id,
                            posting_status='POSTED',
                        )
                        self.db.add(bill)

                        if stats['imported'] % 1000 == 0:
                            self.db.flush()

                    stats['imported'] += 1

                except Exception as e:
                    stats['errors'].append(f"Bill {bill_number}: {str(e)}")

        if not self.dry_run:
            self.db.commit()

        self.log(f"Imported: {stats['imported']}, Skipped: {stats['skipped']}, No Supplier: {stats['no_supplier']}, Errors: {len(stats['errors'])}")
        return stats

    def import_customer_payments(self) -> Dict[str, Any]:
        """Import Customer Payments"""
        print("\n" + "="*60)
        print("IMPORTING CUSTOMER PAYMENTS")
        print("="*60)

        filepath = IMPORT_PATH / "all_customer_payments.csv"
        if not filepath.exists():
            self.log("File not found!")
            return {'imported': 0, 'errors': ['File not found']}

        if not self._customer_cache:
            self._build_customer_cache()

        stats = {'imported': 0, 'skipped': 0, 'errors': [], 'no_customer': 0}
        seen_payments = set()

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    payment_number = row.get('Payment Number', '').strip()
                    if not payment_number or payment_number in seen_payments:
                        continue

                    seen_payments.add(payment_number)

                    existing = self.db.query(CustomerPayment).filter(
                        CustomerPayment.organization_id == self.org_id,
                        CustomerPayment.payment_number == payment_number
                    ).first()

                    if existing:
                        stats['skipped'] += 1
                        continue

                    customer_name = row.get('Customer Name', '').strip()
                    customer_id = self._get_customer_id(customer_name)
                    if not customer_id:
                        stats['no_customer'] += 1
                        continue

                    payment_date = self._parse_date(row.get('Date', ''))
                    if not payment_date:
                        payment_date = date.today()

                    amount = self._parse_decimal(row.get('Amount', 0))
                    if amount <= 0:
                        continue

                    # Map payment method
                    mode = row.get('Mode', '').strip().lower()
                    payment_method = self._map_payment_method(mode)

                    currency = row.get('Currency Code', 'NGN').strip() or 'NGN'
                    if len(currency) > 3:
                        currency = 'NGN'

                    if not self.dry_run:
                        payment = CustomerPayment(
                            organization_id=self.org_id,
                            customer_id=customer_id,
                            payment_number=payment_number[:30],
                            payment_date=payment_date,
                            payment_method=payment_method,
                            currency_code=currency,
                            amount=amount,
                            functional_currency_amount=amount,
                            reference=row.get('Reference Number', '').strip()[:100] or None,
                            description=row.get('Description', '').strip() or None,
                            status=PaymentStatus.CLEARED,
                            created_by_user_id=self.user_id,
                        )
                        self.db.add(payment)

                        if stats['imported'] % 1000 == 0:
                            self.db.flush()

                    stats['imported'] += 1

                except Exception as e:
                    stats['errors'].append(f"Payment {payment_number}: {str(e)}")

        if not self.dry_run:
            self.db.commit()

        self.log(f"Imported: {stats['imported']}, Skipped: {stats['skipped']}, No Customer: {stats['no_customer']}, Errors: {len(stats['errors'])}")
        return stats

    def import_vendor_payments(self) -> Dict[str, Any]:
        """Import Vendor Payments"""
        print("\n" + "="*60)
        print("IMPORTING VENDOR PAYMENTS")
        print("="*60)

        filepath = IMPORT_PATH / "vendor_payments.csv"
        if not filepath.exists():
            self.log("File not found!")
            return {'imported': 0, 'errors': ['File not found']}

        if not self._supplier_cache:
            self._build_supplier_cache()

        if not self._bank_account_id:
            self._setup_default_accounts()

        if not self._bank_account_id and not self.dry_run:
            self.log("ERROR: No bank account found.")
            return {'imported': 0, 'errors': ['No bank account']}
        elif not self._bank_account_id:
            self.log("(Dry-run: Bank account would be available after accounts import)")

        stats = {'imported': 0, 'skipped': 0, 'errors': [], 'no_supplier': 0}
        seen_payments = set()

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    payment_number = row.get('Payment Number', '').strip()
                    if not payment_number or payment_number in seen_payments:
                        continue

                    seen_payments.add(payment_number)

                    existing = self.db.query(SupplierPayment).filter(
                        SupplierPayment.organization_id == self.org_id,
                        SupplierPayment.payment_number == payment_number
                    ).first()

                    if existing:
                        stats['skipped'] += 1
                        continue

                    vendor_name = row.get('Vendor Name', '').strip()
                    supplier_id = self._get_supplier_id(vendor_name)
                    if not supplier_id:
                        stats['no_supplier'] += 1
                        continue

                    payment_date = self._parse_date(row.get('Date', ''))
                    if not payment_date:
                        payment_date = date.today()

                    amount = self._parse_decimal(row.get('Amount', 0))
                    if amount <= 0:
                        continue

                    mode = row.get('Mode', '').strip().lower()
                    payment_method = self._map_ap_payment_method(mode)

                    currency = row.get('Currency Code', 'NGN').strip() or 'NGN'
                    if len(currency) > 3:
                        currency = 'NGN'

                    if not self.dry_run:
                        payment = SupplierPayment(
                            organization_id=self.org_id,
                            supplier_id=supplier_id,
                            payment_number=payment_number[:30],
                            payment_date=payment_date,
                            payment_method=payment_method,
                            currency_code=currency,
                            amount=amount,
                            functional_currency_amount=amount,
                            bank_account_id=self._bank_account_id,
                            reference=row.get('Reference Number', '').strip()[:100] or None,
                            status=APPaymentStatus.CLEARED,
                            created_by_user_id=self.user_id,
                        )
                        self.db.add(payment)

                        if stats['imported'] % 1000 == 0:
                            self.db.flush()

                    stats['imported'] += 1

                except Exception as e:
                    stats['errors'].append(f"Payment {payment_number}: {str(e)}")

        if not self.dry_run:
            self.db.commit()

        self.log(f"Imported: {stats['imported']}, Skipped: {stats['skipped']}, No Supplier: {stats['no_supplier']}, Errors: {len(stats['errors'])}")
        return stats

    def import_journals(self) -> Dict[str, Any]:
        """Import Journal Entries"""
        print("\n" + "="*60)
        print("IMPORTING JOURNALS")
        print("="*60)

        filepath = IMPORT_PATH / "journals.csv"
        if not filepath.exists():
            self.log("File not found!")
            return {'imported': 0, 'errors': ['File not found']}

        # Ensure fiscal periods exist
        self._ensure_fiscal_periods()

        stats = {'imported': 0, 'skipped': 0, 'errors': [], 'no_period': 0}
        journal_data = {}  # Group by journal number

        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            reader = csv.DictReader(f)
            for row in reader:
                journal_number = row.get('Journal Number', '').strip()
                if not journal_number:
                    continue

                if journal_number not in journal_data:
                    journal_data[journal_number] = {
                        'header': row,
                        'lines': []
                    }
                journal_data[journal_number]['lines'].append(row)

        for journal_number, data in journal_data.items():
            try:
                existing = self.db.query(JournalEntry).filter(
                    JournalEntry.organization_id == self.org_id,
                    JournalEntry.journal_number == journal_number
                ).first()

                if existing:
                    stats['skipped'] += 1
                    continue

                header = data['header']
                journal_date = self._parse_date(header.get('Journal Date', ''))
                if not journal_date:
                    journal_date = date.today()

                fiscal_period_id = self._get_fiscal_period_id(journal_date)
                if not fiscal_period_id:
                    stats['no_period'] += 1
                    continue

                # Calculate totals
                total_debit = Decimal('0')
                total_credit = Decimal('0')
                for line in data['lines']:
                    total_debit += self._parse_decimal(line.get('Debit', 0))
                    total_credit += self._parse_decimal(line.get('Credit', 0))

                # Map journal type
                journal_type_str = header.get('Journal Type', '').strip().lower()
                journal_type = self._map_journal_type(journal_type_str)

                currency = header.get('Currency', 'NGN').strip() or 'NGN'
                if len(currency) > 3:
                    currency = 'NGN'

                if not self.dry_run:
                    journal = JournalEntry(
                        organization_id=self.org_id,
                        journal_number=journal_number[:30],
                        journal_type=journal_type,
                        entry_date=journal_date,
                        posting_date=journal_date,
                        fiscal_period_id=fiscal_period_id,
                        description=header.get('Notes', '').strip()[:500] or f"Imported Journal {journal_number}",
                        reference=header.get('Reference Number', '').strip()[:100] or None,
                        currency_code=currency,
                        total_debit=total_debit,
                        total_credit=total_credit,
                        total_debit_functional=total_debit,
                        total_credit_functional=total_credit,
                        status=JournalStatus.POSTED,
                        created_by_user_id=self.user_id,
                    )
                    self.db.add(journal)
                    self.db.flush()

                    # Add lines
                    line_number = 1
                    for line in data['lines']:
                        account_name = line.get('Account', '').strip()
                        account_code = line.get('Account Code', '').strip()
                        account_id = self._get_account_id(account_name, account_code)

                        if not account_id:
                            continue

                        debit = self._parse_decimal(line.get('Debit', 0))
                        credit = self._parse_decimal(line.get('Credit', 0))

                        je_line = JournalEntryLine(
                            journal_entry_id=journal.journal_entry_id,
                            line_number=line_number,
                            account_id=account_id,
                            description=line.get('Description', '').strip() or None,
                            debit_amount=debit,
                            credit_amount=credit,
                            debit_amount_functional=debit,
                            credit_amount_functional=credit,
                        )
                        self.db.add(je_line)
                        line_number += 1

                stats['imported'] += 1

            except Exception as e:
                stats['errors'].append(f"Journal {journal_number}: {str(e)}")

        if not self.dry_run:
            self.db.commit()

        self.log(f"Imported: {stats['imported']}, Skipped: {stats['skipped']}, No Period: {stats['no_period']}, Errors: {len(stats['errors'])}")
        return stats

    # Helper methods for type mapping
    def _map_to_ifrs_category(self, zoho_type: str) -> IFRSCategory:
        """Map Zoho account type to IFRS category"""
        mapping = {
            'Bank': IFRSCategory.ASSETS,
            'Cash': IFRSCategory.ASSETS,
            'Other Current Asset': IFRSCategory.ASSETS,
            'Fixed Asset': IFRSCategory.ASSETS,
            'Stock': IFRSCategory.ASSETS,
            'Accounts Receivable': IFRSCategory.ASSETS,
            'Other Current Liability': IFRSCategory.LIABILITIES,
            'Long Term Liability': IFRSCategory.LIABILITIES,
            'Accounts Payable': IFRSCategory.LIABILITIES,
            'Credit Card': IFRSCategory.LIABILITIES,
            'Equity': IFRSCategory.EQUITY,
            'Income': IFRSCategory.REVENUE,
            'Other Income': IFRSCategory.REVENUE,
            'Expense': IFRSCategory.EXPENSES,
            'Cost of Goods Sold': IFRSCategory.EXPENSES,
            'Other Expense': IFRSCategory.EXPENSES,
        }
        return mapping.get(zoho_type, IFRSCategory.ASSETS)

    def _get_normal_balance(self, ifrs_category: IFRSCategory) -> NormalBalance:
        """Get normal balance based on IFRS category"""
        # Assets and Expenses have normal DEBIT balance
        # Liabilities, Equity, Revenue have normal CREDIT balance
        if ifrs_category in [IFRSCategory.ASSETS, IFRSCategory.EXPENSES]:
            return NormalBalance.DEBIT
        return NormalBalance.CREDIT

    def _map_invoice_status(self, zoho_status: str, balance: Decimal, total: Decimal) -> InvoiceStatus:
        if 'void' in zoho_status:
            return InvoiceStatus.VOID
        if 'draft' in zoho_status:
            return InvoiceStatus.DRAFT
        if balance <= 0 or 'paid' in zoho_status:
            return InvoiceStatus.PAID
        if balance < total:
            return InvoiceStatus.PARTIALLY_PAID
        if 'overdue' in zoho_status:
            return InvoiceStatus.OVERDUE
        return InvoiceStatus.POSTED

    def _map_bill_status(self, zoho_status: str, balance: Decimal, total: Decimal) -> SupplierInvoiceStatus:
        if 'void' in zoho_status:
            return SupplierInvoiceStatus.VOID
        if 'draft' in zoho_status:
            return SupplierInvoiceStatus.DRAFT
        if balance <= 0 or 'paid' in zoho_status:
            return SupplierInvoiceStatus.PAID
        if balance < total:
            return SupplierInvoiceStatus.PARTIALLY_PAID
        return SupplierInvoiceStatus.POSTED

    def _map_payment_method(self, mode: str) -> PaymentMethod:
        if 'bank' in mode or 'transfer' in mode:
            return PaymentMethod.BANK_TRANSFER
        if 'cash' in mode:
            return PaymentMethod.CASH
        if 'check' in mode or 'cheque' in mode:
            return PaymentMethod.CHECK
        if 'card' in mode:
            return PaymentMethod.CARD
        return PaymentMethod.BANK_TRANSFER

    def _map_ap_payment_method(self, mode: str) -> APPaymentMethod:
        if 'check' in mode or 'cheque' in mode:
            return APPaymentMethod.CHECK
        if 'wire' in mode:
            return APPaymentMethod.WIRE
        if 'card' in mode:
            return APPaymentMethod.CARD
        return APPaymentMethod.BANK_TRANSFER

    def _map_journal_type(self, journal_type: str) -> JournalType:
        if 'adjust' in journal_type:
            return JournalType.ADJUSTMENT
        if 'close' in journal_type or 'closing' in journal_type:
            return JournalType.CLOSING
        if 'open' in journal_type:
            return JournalType.OPENING
        if 'revers' in journal_type:
            return JournalType.REVERSAL
        return JournalType.STANDARD

    def _parse_decimal(self, value) -> Decimal:
        if not value:
            return Decimal('0')
        try:
            cleaned = str(value).replace(',', '').strip()
            return Decimal(cleaned)
        except:
            return Decimal('0')

    def _parse_date(self, value: str) -> Optional[date]:
        if not value:
            return None
        for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d']:
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except:
                continue
        return None

    def run_all(self):
        """Run all imports in order"""
        print("\n" + "="*60)
        print("BULK IMPORT - ZOHO BOOKS DATA (2022-2024)")
        print("="*60)
        print(f"Organization ID: {self.org_id}")
        print(f"User ID: {self.user_id}")
        print(f"Dry Run: {self.dry_run}")
        print(f"Import Path: {IMPORT_PATH}")

        results = {}

        # Setup fiscal periods first
        self._ensure_fiscal_periods()

        # Master data first
        results['accounts'] = self.import_chart_of_accounts()
        self._setup_default_accounts()

        results['customers'] = self.import_customers()
        results['vendors'] = self.import_vendors()
        results['items'] = self.import_items()

        # Transactions
        results['invoices'] = self.import_invoices()
        results['bills'] = self.import_bills()
        results['customer_payments'] = self.import_customer_payments()
        results['vendor_payments'] = self.import_vendor_payments()
        results['journals'] = self.import_journals()

        # Summary
        print("\n" + "="*60)
        print("IMPORT SUMMARY")
        print("="*60)

        total_imported = 0
        total_skipped = 0
        total_errors = 0

        for entity, stats in results.items():
            imported = stats.get('imported', 0)
            skipped = stats.get('skipped', 0)
            errors = len(stats.get('errors', []))
            total_imported += imported
            total_skipped += skipped
            total_errors += errors
            extra = ""
            if stats.get('no_customer'):
                extra += f", No Customer: {stats['no_customer']}"
            if stats.get('no_supplier'):
                extra += f", No Supplier: {stats['no_supplier']}"
            if stats.get('no_period'):
                extra += f", No Period: {stats['no_period']}"
            print(f"  {entity:<20} Imported: {imported:>6}, Skipped: {skipped:>6}, Errors: {errors:>4}{extra}")

        print("-"*60)
        print(f"  {'TOTAL':<20} Imported: {total_imported:>6}, Skipped: {total_skipped:>6}, Errors: {total_errors:>4}")

        if self.dry_run:
            print("\n*** DRY RUN - No data was saved ***")

        return results


def main():
    parser = argparse.ArgumentParser(description='Bulk Import Zoho Books Data')
    parser.add_argument('--org-id', required=True, help='Organization UUID')
    parser.add_argument('--user-id', required=True, help='User UUID')
    parser.add_argument('--dry-run', action='store_true', help='Validate without saving')
    parser.add_argument('--entity', help='Import only specific entity type')

    args = parser.parse_args()

    try:
        org_id = UUID(args.org_id)
        user_id = UUID(args.user_id)
    except ValueError as e:
        print(f"Error: Invalid UUID - {e}")
        sys.exit(1)

    db = SessionLocal()
    try:
        importer = BulkImporter(db, org_id, user_id, dry_run=args.dry_run)

        if args.entity:
            method_name = f"import_{args.entity}"
            if hasattr(importer, method_name):
                # Setup first
                importer._setup_default_accounts()
                importer._ensure_fiscal_periods()
                getattr(importer, method_name)()
            else:
                print(f"Unknown entity type: {args.entity}")
                print("Available: accounts, customers, vendors, items, invoices, bills, customer_payments, vendor_payments, journals")
                sys.exit(1)
        else:
            importer.run_all()

    finally:
        db.close()


if __name__ == "__main__":
    main()
