#!/usr/bin/env python3
"""
Seed baseline reference data for E2E tests.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Iterable
from uuid import UUID

from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal
from app.models.auth import UserCredential
from app.models.person import Person
from app.models.ifrs.core_org.organization import Organization
from app.models.ifrs.gl.account import Account, AccountType, NormalBalance
from app.models.ifrs.gl.account_category import AccountCategory, IFRSCategory
from app.models.ifrs.gl.fiscal_year import FiscalYear
from app.models.ifrs.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.ifrs.ar.customer import Customer, CustomerType, RiskCategory
from app.models.ifrs.ar.payment_terms import PaymentTerms
from app.models.ifrs.ap.supplier import Supplier, SupplierType
from app.models.ifrs.banking.bank_account import BankAccount, BankAccountStatus, BankAccountType
from app.models.ifrs.inv.item import Item, ItemType, CostingMethod
from app.models.ifrs.inv.item_category import ItemCategory
from app.models.ifrs.tax.tax_jurisdiction import TaxJurisdiction
from app.models.ifrs.tax.tax_period import TaxPeriod, TaxPeriodFrequency, TaxPeriodStatus
from app.services.ifrs.gl.fiscal_year import FiscalYearInput, fiscal_year_service
from app.services.ifrs.tax.seed import NigeriaSeedSummary, seed_nigeria_tax_data


DEFAULT_ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


@dataclass
class SeedSummary:
    organizations: int = 0
    categories: int = 0
    accounts: int = 0
    fiscal_years: int = 0
    fiscal_periods: int = 0
    payment_terms: int = 0
    customers: int = 0
    suppliers: int = 0
    bank_accounts: int = 0
    item_categories: int = 0
    items: int = 0
    tax_periods: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed baseline E2E data.")
    parser.add_argument("--org-id", help="Organization ID to seed")
    parser.add_argument("--org-code", help="Organization code to seed")
    return parser.parse_args()


def _unique_code(existing: Iterable[str], base: str) -> str:
    if base not in existing:
        return base
    index = 2
    while True:
        candidate = f"{base}-{index}"
        if candidate not in existing:
            return candidate
        index += 1


def resolve_orgs(db, args: argparse.Namespace) -> list[Organization]:
    if args.org_id and args.org_code:
        raise SystemExit("Use only one of --org-id or --org-code.")

    if args.org_id:
        try:
            org_id = UUID(args.org_id)
        except ValueError as exc:
            raise SystemExit(f"Invalid organization ID: {args.org_id}") from exc
        org = db.get(Organization, org_id)
        return [org] if org else []

    if args.org_code:
        return (
            db.query(Organization)
            .filter(Organization.organization_code == args.org_code)
            .all()
        )

    org_ids: set[UUID] = set()
    for env_key in ("E2E_TEST_USERNAME", "E2E_ADMIN_USERNAME"):
        username = os.environ.get(env_key)
        if not username:
            continue
        credential = (
            db.query(UserCredential)
            .filter(UserCredential.username == username)
            .first()
        )
        if credential:
            person = db.get(Person, credential.person_id)
            if person:
                org_ids.add(person.organization_id)

    orgs = [db.get(Organization, org_id) for org_id in org_ids if org_id]
    orgs = [org for org in orgs if org]
    if orgs:
        return orgs

    fallback = db.get(Organization, DEFAULT_ORG_ID)
    return [fallback] if fallback else []


def ensure_organization(db, org_id: UUID, summary: SeedSummary) -> Organization:
    org = db.get(Organization, org_id)
    if org:
        return org

    existing_codes = {
        row.organization_code
        for row in db.query(Organization.organization_code).all()
    }
    org_code = _unique_code(existing_codes, "E2E")

    org = Organization(
        organization_id=org_id,
        organization_code=org_code,
        legal_name="E2E Test Organization",
        functional_currency_code="USD",
        presentation_currency_code="USD",
        fiscal_year_end_month=12,
        fiscal_year_end_day=31,
        jurisdiction_country_code="NG",
        is_active=True,
    )
    db.add(org)
    db.commit()
    summary.organizations += 1
    return org


def ensure_categories(db, org_id: UUID, summary: SeedSummary) -> dict[IFRSCategory, AccountCategory]:
    defaults = [
        (IFRSCategory.ASSETS, "AST", "Assets"),
        (IFRSCategory.LIABILITIES, "LIA", "Liabilities"),
        (IFRSCategory.EQUITY, "EQT", "Equity"),
        (IFRSCategory.REVENUE, "REV", "Revenue"),
        (IFRSCategory.EXPENSES, "EXP", "Expenses"),
    ]
    categories: dict[IFRSCategory, AccountCategory] = {}
    for index, (ifrs_category, code, name) in enumerate(defaults, start=1):
        existing = (
            db.query(AccountCategory)
            .filter(
                AccountCategory.organization_id == org_id,
                AccountCategory.ifrs_category == ifrs_category,
                AccountCategory.is_active.is_(True),
            )
            .order_by(AccountCategory.category_code)
            .first()
        )
        if existing:
            categories[ifrs_category] = existing
            continue

        existing_codes = {
            row.category_code
            for row in db.query(AccountCategory.category_code)
            .filter(AccountCategory.organization_id == org_id)
            .all()
        }
        category_code = _unique_code(existing_codes, code)
        category = AccountCategory(
            organization_id=org_id,
            category_code=category_code,
            category_name=name,
            description=f"E2E {name} category",
            ifrs_category=ifrs_category,
            hierarchy_level=1,
            display_order=index,
            is_active=True,
        )
        db.add(category)
        categories[ifrs_category] = category
        summary.categories += 1

    db.flush()
    return categories


def ensure_account(
    db,
    org_id: UUID,
    category_id: UUID,
    account_code: str,
    account_name: str,
    normal_balance: NormalBalance,
    summary: SeedSummary,
    subledger_type: str | None = None,
    is_cash_equivalent: bool = False,
) -> Account:
    account = (
        db.query(Account)
        .filter(
            Account.organization_id == org_id,
            Account.account_code == account_code,
        )
        .first()
    )
    if account:
        return account

    account = Account(
        organization_id=org_id,
        category_id=category_id,
        account_code=account_code,
        account_name=account_name,
        description=f"E2E {account_name}",
        account_type=AccountType.POSTING,
        normal_balance=normal_balance,
        subledger_type=subledger_type,
        is_cash_equivalent=is_cash_equivalent,
        is_active=True,
        is_posting_allowed=True,
        is_budgetable=False,
        is_reconciliation_required=False,
    )
    db.add(account)
    summary.accounts += 1
    return account


def ensure_accounts(
    db,
    org_id: UUID,
    categories: dict[IFRSCategory, AccountCategory],
    summary: SeedSummary,
) -> dict[str, Account]:
    assets = categories[IFRSCategory.ASSETS]
    liabilities = categories[IFRSCategory.LIABILITIES]
    equity = categories[IFRSCategory.EQUITY]
    revenue = categories[IFRSCategory.REVENUE]
    expenses = categories[IFRSCategory.EXPENSES]

    accounts = {
        "cash": ensure_account(
            db,
            org_id,
            assets.category_id,
            "1000",
            "Cash and Cash Equivalents",
            NormalBalance.DEBIT,
            summary,
            subledger_type="BANK",
            is_cash_equivalent=True,
        ),
        "accounts_receivable": ensure_account(
            db,
            org_id,
            assets.category_id,
            "1100",
            "Accounts Receivable",
            NormalBalance.DEBIT,
            summary,
            subledger_type="AR",
        ),
        "inventory": ensure_account(
            db,
            org_id,
            assets.category_id,
            "1200",
            "Inventory",
            NormalBalance.DEBIT,
            summary,
        ),
        "accounts_payable": ensure_account(
            db,
            org_id,
            liabilities.category_id,
            "2000",
            "Accounts Payable",
            NormalBalance.CREDIT,
            summary,
            subledger_type="AP",
        ),
        "retained_earnings": ensure_account(
            db,
            org_id,
            equity.category_id,
            "3000",
            "Retained Earnings",
            NormalBalance.CREDIT,
            summary,
        ),
        "sales_revenue": ensure_account(
            db,
            org_id,
            revenue.category_id,
            "4000",
            "Sales Revenue",
            NormalBalance.CREDIT,
            summary,
        ),
        "operating_expense": ensure_account(
            db,
            org_id,
            expenses.category_id,
            "5000",
            "Operating Expenses",
            NormalBalance.DEBIT,
            summary,
        ),
        "cogs": ensure_account(
            db,
            org_id,
            expenses.category_id,
            "5100",
            "Cost of Goods Sold",
            NormalBalance.DEBIT,
            summary,
        ),
        "inventory_adjustment": ensure_account(
            db,
            org_id,
            expenses.category_id,
            "5200",
            "Inventory Adjustments",
            NormalBalance.DEBIT,
            summary,
        ),
    }

    db.flush()
    return accounts


def ensure_fiscal_year(db, org_id: UUID, retained_earnings_id: UUID, summary: SeedSummary) -> None:
    today = date.today()
    year_code = str(today.year)
    existing = (
        db.query(FiscalYear)
        .filter(
            FiscalYear.organization_id == org_id,
            FiscalYear.year_code == year_code,
        )
        .first()
    )
    if existing:
        return

    fiscal_year_service.create_year_with_periods(
        db,
        org_id,
        FiscalYearInput(
            year_code=year_code,
            year_name=f"FY {year_code}",
            start_date=date(today.year, 1, 1),
            end_date=date(today.year, 12, 31),
            retained_earnings_account_id=retained_earnings_id,
        ),
    )
    summary.fiscal_years += 1
    summary.fiscal_periods += 12

    period = (
        db.query(FiscalPeriod)
        .filter(
            FiscalPeriod.organization_id == org_id,
            FiscalPeriod.start_date <= today,
            FiscalPeriod.end_date >= today,
        )
        .first()
    )
    if period and period.status != PeriodStatus.OPEN:
        period.status = PeriodStatus.OPEN
        db.commit()


def ensure_payment_terms(db, org_id: UUID, summary: SeedSummary) -> PaymentTerms:
    terms = (
        db.query(PaymentTerms)
        .filter(
            PaymentTerms.organization_id == org_id,
            PaymentTerms.terms_code == "NET30",
        )
        .first()
    )
    if terms:
        return terms

    terms = PaymentTerms(
        organization_id=org_id,
        terms_code="NET30",
        terms_name="Net 30",
        due_days=30,
        discount_days=None,
        discount_percentage=None,
        is_active=True,
    )
    db.add(terms)
    db.commit()
    summary.payment_terms += 1
    return terms


def ensure_customer(
    db,
    org_id: UUID,
    ar_account_id: UUID,
    revenue_account_id: UUID,
    summary: SeedSummary,
) -> Customer:
    customer = (
        db.query(Customer)
        .filter(
            Customer.organization_id == org_id,
            Customer.customer_code == "E2E-CUST",
        )
        .first()
    )
    if customer:
        return customer

    customer = Customer(
        organization_id=org_id,
        customer_code="E2E-CUST",
        customer_type=CustomerType.COMPANY,
        legal_name="E2E Customer",
        trading_name="E2E Customer",
        credit_limit=Decimal("10000"),
        credit_terms_days=30,
        currency_code="USD",
        ar_control_account_id=ar_account_id,
        default_revenue_account_id=revenue_account_id,
        risk_category=RiskCategory.MEDIUM,
        is_active=True,
    )
    db.add(customer)
    db.commit()
    summary.customers += 1
    return customer


def ensure_supplier(
    db,
    org_id: UUID,
    ap_account_id: UUID,
    expense_account_id: UUID,
    summary: SeedSummary,
) -> Supplier:
    supplier = (
        db.query(Supplier)
        .filter(
            Supplier.organization_id == org_id,
            Supplier.supplier_code == "E2E-SUP",
        )
        .first()
    )
    if supplier:
        return supplier

    supplier = Supplier(
        organization_id=org_id,
        supplier_code="E2E-SUP",
        supplier_type=SupplierType.VENDOR,
        legal_name="E2E Supplier",
        trading_name="E2E Supplier",
        payment_terms_days=30,
        currency_code="USD",
        default_expense_account_id=expense_account_id,
        ap_control_account_id=ap_account_id,
        is_active=True,
    )
    db.add(supplier)
    db.commit()
    summary.suppliers += 1
    return supplier


def ensure_inventory_category(
    db,
    org_id: UUID,
    inventory_account_id: UUID,
    cogs_account_id: UUID,
    revenue_account_id: UUID,
    adjustment_account_id: UUID,
    summary: SeedSummary,
) -> ItemCategory:
    category = (
        db.query(ItemCategory)
        .filter(
            ItemCategory.organization_id == org_id,
            ItemCategory.category_code == "E2E-INV",
        )
        .first()
    )
    if category:
        return category

    category = ItemCategory(
        organization_id=org_id,
        category_code="E2E-INV",
        category_name="E2E Inventory",
        description="E2E inventory category",
        inventory_account_id=inventory_account_id,
        cogs_account_id=cogs_account_id,
        revenue_account_id=revenue_account_id,
        inventory_adjustment_account_id=adjustment_account_id,
        purchase_variance_account_id=None,
        is_active=True,
    )
    db.add(category)
    db.commit()
    summary.item_categories += 1
    return category


def ensure_inventory_item(
    db,
    org_id: UUID,
    category_id: UUID,
    revenue_account_id: UUID,
    inventory_account_id: UUID,
    cogs_account_id: UUID,
    summary: SeedSummary,
) -> Item:
    item = (
        db.query(Item)
        .filter(
            Item.organization_id == org_id,
            Item.item_code == "E2E-ITEM",
        )
        .first()
    )
    if item:
        return item

    item = Item(
        organization_id=org_id,
        item_code="E2E-ITEM",
        item_name="E2E Inventory Item",
        description="Seeded item for E2E tests",
        item_type=ItemType.INVENTORY,
        category_id=category_id,
        base_uom="EA",
        purchase_uom="EA",
        sales_uom="EA",
        costing_method=CostingMethod.WEIGHTED_AVERAGE,
        currency_code="USD",
        list_price=Decimal("100.00"),
        track_inventory=True,
        inventory_account_id=inventory_account_id,
        cogs_account_id=cogs_account_id,
        revenue_account_id=revenue_account_id,
        is_active=True,
        is_purchaseable=True,
        is_saleable=True,
    )
    db.add(item)
    db.commit()
    summary.items += 1
    return item


def ensure_bank_account(
    db,
    org_id: UUID,
    gl_account_id: UUID,
    summary: SeedSummary,
) -> BankAccount:
    account = (
        db.query(BankAccount)
        .filter(
            BankAccount.organization_id == org_id,
            BankAccount.account_number == "E2E-0001",
            BankAccount.bank_code == "E2E",
        )
        .first()
    )
    if account:
        return account

    account = BankAccount(
        organization_id=org_id,
        bank_name="E2E Bank",
        bank_code="E2E",
        branch_code="0001",
        branch_name="E2E Branch",
        account_number="E2E-0001",
        account_name="E2E Operating Account",
        account_type=BankAccountType.checking,
        currency_code="USD",
        gl_account_id=gl_account_id,
        status=BankAccountStatus.active,
        is_primary=True,
        allow_overdraft=False,
    )
    db.add(account)
    db.commit()
    summary.bank_accounts += 1
    return account


def ensure_tax_period(db, org_id: UUID, summary: SeedSummary) -> None:
    jurisdiction = (
        db.query(TaxJurisdiction)
        .filter(
            TaxJurisdiction.organization_id == org_id,
            TaxJurisdiction.jurisdiction_code == "NG-FED",
        )
        .first()
    )
    if not jurisdiction:
        return

    today = date.today()
    period_name = f"{today.year}-{today.month:02d}"
    existing = (
        db.query(TaxPeriod)
        .filter(
            TaxPeriod.organization_id == org_id,
            TaxPeriod.jurisdiction_id == jurisdiction.jurisdiction_id,
            TaxPeriod.period_name == period_name,
        )
        .first()
    )
    if existing:
        return

    start_date = date(today.year, today.month, 1)
    if today.month == 12:
        end_date = date(today.year, 12, 31)
    else:
        end_date = date(today.year, today.month + 1, 1) - timedelta(days=1)
    due_date = end_date + timedelta(days=30)

    period = TaxPeriod(
        organization_id=org_id,
        jurisdiction_id=jurisdiction.jurisdiction_id,
        period_name=period_name,
        frequency=TaxPeriodFrequency.MONTHLY,
        start_date=start_date,
        end_date=end_date,
        due_date=due_date,
        status=TaxPeriodStatus.OPEN,
    )
    db.add(period)
    db.commit()
    summary.tax_periods += 1


def seed_for_org(db, org: Organization) -> tuple[SeedSummary, NigeriaSeedSummary]:
    summary = SeedSummary()

    categories = ensure_categories(db, org.organization_id, summary)
    accounts = ensure_accounts(db, org.organization_id, categories, summary)

    ensure_fiscal_year(db, org.organization_id, accounts["retained_earnings"].account_id, summary)
    ensure_payment_terms(db, org.organization_id, summary)

    ensure_customer(
        db,
        org.organization_id,
        accounts["accounts_receivable"].account_id,
        accounts["sales_revenue"].account_id,
        summary,
    )
    ensure_supplier(
        db,
        org.organization_id,
        accounts["accounts_payable"].account_id,
        accounts["operating_expense"].account_id,
        summary,
    )

    category = ensure_inventory_category(
        db,
        org.organization_id,
        accounts["inventory"].account_id,
        accounts["cogs"].account_id,
        accounts["sales_revenue"].account_id,
        accounts["inventory_adjustment"].account_id,
        summary,
    )
    ensure_inventory_item(
        db,
        org.organization_id,
        category.category_id,
        accounts["sales_revenue"].account_id,
        accounts["inventory"].account_id,
        accounts["cogs"].account_id,
        summary,
    )

    ensure_bank_account(db, org.organization_id, accounts["cash"].account_id, summary)

    nigeria_summary = seed_nigeria_tax_data(db, org.organization_id)
    ensure_tax_period(db, org.organization_id, summary)

    return summary, nigeria_summary


def main() -> None:
    load_dotenv()
    args = parse_args()

    db = SessionLocal()
    try:
        orgs = resolve_orgs(db, args)
        if not orgs and not args.org_id and not args.org_code:
            orgs = [ensure_organization(db, DEFAULT_ORG_ID, SeedSummary())]

        if not orgs:
            raise SystemExit("No organizations matched for E2E seed data.")

        for org in orgs:
            summary, nigeria_summary = seed_for_org(db, org)
            print(
                f"Seeded E2E data for org {org.organization_code} ({org.organization_id}): "
                f"categories={summary.categories}, "
                f"accounts={summary.accounts}, "
                f"fiscal_years={summary.fiscal_years}, "
                f"fiscal_periods={summary.fiscal_periods}, "
                f"payment_terms={summary.payment_terms}, "
                f"customers={summary.customers}, "
                f"suppliers={summary.suppliers}, "
                f"bank_accounts={summary.bank_accounts}, "
                f"item_categories={summary.item_categories}, "
                f"items={summary.items}, "
                f"tax_periods={summary.tax_periods}, "
                f"nigeria_currency={nigeria_summary.currency_created}, "
                f"nigeria_categories={nigeria_summary.categories_created}, "
                f"nigeria_accounts={nigeria_summary.accounts_created}, "
                f"nigeria_jurisdictions={nigeria_summary.jurisdictions_created}, "
                f"nigeria_tax_codes={nigeria_summary.tax_codes_created}"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
