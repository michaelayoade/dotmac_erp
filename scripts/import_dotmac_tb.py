#!/usr/bin/env python3
"""
Import Dotmac Trial Balance data for 2022-2024.
Creates Chart of Accounts, Fiscal Years, and Opening Balances.
"""

import os
import sys
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import SessionLocal
from app.models.finance.gl.account import Account, AccountType, NormalBalance
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.fiscal_year import FiscalYear
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.finance.gl.journal_entry_line import JournalEntryLine

# Organization ID - use existing org or override via env var
DOTMAC_ORG_ID = UUID(os.environ.get("ORG_ID", "00000000-0000-0000-0000-000000000001"))
# Default to local docs path; allow override for Docker runs.
TB_DIR = os.environ.get("TB_DIR", "/root/.dotmac/docs")

# Account mapping: TB name -> (code, category_code, normal_balance, subledger_type)
ACCOUNT_MAP = {
    # Fixed Assets (1xxx)
    "Office Equipment": ("1100", "FA", "DEBIT", None),
    "Motor Vehicle": ("1110", "FA", "DEBIT", None),
    "Furniture & Fittings": ("1120", "FA", "DEBIT", None),
    "Plant & Machinery": ("1130", "FA", "DEBIT", None),
    # Accumulated Depreciation (1xxx contra)
    "Office Equipment_ACCUM": ("1100-AD", "FA-AD", "CREDIT", None),
    "Motor Vehicle_ACCUM": ("1110-AD", "FA-AD", "CREDIT", None),
    "Furniture & Fittings_ACCUM": ("1120-AD", "FA-AD", "CREDIT", None),
    "Plant & Machinery_ACCUM": ("1130-AD", "FA-AD", "CREDIT", None),
    # Bank & Cash (1200-1299)
    "Zenith Bank": ("1200", "BANK", "DEBIT", "BANK"),
    "Heritage Bank": ("1201", "BANK", "DEBIT", "BANK"),
    "UBA": ("1202", "BANK", "DEBIT", "BANK"),
    "First Bank": ("1203", "BANK", "DEBIT", "BANK"),
    "Paystack": ("1210", "BANK", "DEBIT", "BANK"),
    "Paystack OPEX Account": ("1211", "BANK", "DEBIT", "BANK"),
    "Fultterwave": ("1212", "BANK", "DEBIT", "BANK"),
    "Flutterwave OPEX": ("1213", "BANK", "DEBIT", "BANK"),
    "Quick Teller": ("1214", "BANK", "DEBIT", "BANK"),
    "Cash at Hand": ("1220", "CASH", "DEBIT", None),
    # Inventory (1300-1399)
    "Materials": ("1300", "INV", "DEBIT", "INVENTORY"),
    "Goods in Transit": ("1310", "INV", "DEBIT", "INVENTORY"),
    # Receivables (1400-1499)
    "Trade Receivables": ("1400", "AR", "DEBIT", "AR"),
    "Staff Loan": ("1410", "AR", "DEBIT", None),
    "Withholding Taxes": ("1420", "AR", "DEBIT", None),
    "Prepayment": ("1430", "AR", "DEBIT", None),
    "Input VAT": ("1440", "AR", "DEBIT", None),
    # Payables (2000-2099)
    "Trade Payables": ("2000", "AP", "CREDIT", "AP"),
    "NCC Licence Fees": ("2010", "AP", "CREDIT", None),
    "Accurued Expenses": ("2020", "AP", "CREDIT", None),
    "Employee Reimbursables": ("2030", "AP", "CREDIT", None),
    # Tax Liabilities (2100-2199)
    "Income Tax": ("2100", "TAX-L", "CREDIT", None),
    "Education Tax": ("2101", "TAX-L", "CREDIT", None),
    "IT Levy": ("2102", "TAX-L", "CREDIT", None),
    "WHT": ("2110", "TAX-L", "CREDIT", None),
    "VAT Payables": ("2120", "TAX-L", "CREDIT", None),
    "Pension": ("2130", "TAX-L", "CREDIT", None),
    "Payee": ("2131", "TAX-L", "CREDIT", None),
    "NHF Payables": ("2132", "TAX-L", "CREDIT", None),
    "Tax Audit Liability": ("2140", "TAX-L", "CREDIT", None),
    # Long-term Liabilities (2500-2599)
    "Long Term Borrowings": ("2500", "LTL", "CREDIT", None),
    # Equity (3000-3099)
    "Issued and Fully Paid": ("3000", "EQ", "CREDIT", None),
    "Other Componrnt Equity": ("3010", "EQ", "CREDIT", None),
    "Retained Earnings": ("3100", "RE", "CREDIT", None),
    "Directors Current Account": ("3200", "EQ", "CREDIT", None),
    # Revenue (4000-4099)
    "Internet  Revenue": ("4000", "REV", "CREDIT", None),
    "Other Business Revenue": ("4010", "REV", "CREDIT", None),
    "VAT (Out Put)": ("4020", "REV", "CREDIT", None),
    "Stampduty Deducted At Source": ("4030", "REV", "CREDIT", None),
    "Value Added Tax Withheld": ("4031", "REV", "CREDIT", None),
    # Cost of Sales (5000-5099)
    "Purchases": ("5000", "COS", "DEBIT", None),
    "Customer Terminal Devices": ("5010", "COS", "DEBIT", None),
    "Installation and maintenance of fiber optic Network": (
        "5020",
        "COS",
        "DEBIT",
        None,
    ),
    "Purchase of bandwitdh and Interconnect": ("5030", "COS", "DEBIT", None),
    # Operating Expenses (6000-6999)
    "Staff Salaries & Wage": ("6000", "EXP", "DEBIT", None),
    "PAYE expenses": ("6001", "EXP", "DEBIT", None),
    "NHF Charges/Expenses": ("6002", "EXP", "DEBIT", None),
    "Staff Training": ("6003", "EXP", "DEBIT", None),
    "Statutory Expenses": ("6010", "EXP", "DEBIT", None),
    "Security Expenses": ("6011", "EXP", "DEBIT", None),
    "Subscription & Renewal": ("6012", "EXP", "DEBIT", None),
    "Medical Expenses": ("6013", "EXP", "DEBIT", None),
    "Printing & stationery": ("6020", "EXP", "DEBIT", None),
    "Rent or Lease Payment": ("6021", "EXP", "DEBIT", None),
    "Utilities": ("6022", "EXP", "DEBIT", None),
    "Telephone bills": ("6023", "EXP", "DEBIT", None),
    "Fuel & Lubricant": ("6024", "EXP", "DEBIT", None),
    "Consultancy": ("6030", "EXP", "DEBIT", None),
    "Exchange gain or Loss": ("6031", "EXP", "DEBIT", None),
    "IT & Internet Expenses": ("6032", "EXP", "DEBIT", None),
    "Janitorial Expenses": ("6033", "EXP", "DEBIT", None),
    "Reconciliation Discrepancies": ("6034", "EXP", "DEBIT", None),
    "Undeposited Funds": ("6035", "EXP", "DEBIT", None),
    "Unearned Revenue": ("6036", "EXP", "CREDIT", None),
    "Accomodation Expenses": ("6037", "EXP", "DEBIT", None),
    "Membership dues": ("6038", "EXP", "DEBIT", None),
    "Pension_EXP": ("6039", "EXP", "DEBIT", None),
    "ITF": ("6040", "EXP", "DEBIT", None),
    "NHF": ("6041", "EXP", "DEBIT", None),
    "Office Repairs & Maintenance": ("6050", "EXP", "DEBIT", None),
    "Entertament": ("6051", "EXP", "DEBIT", None),
    "Equipment rental": ("6052", "EXP", "DEBIT", None),
    "Motor Vehichle Repairs & Maintenance": ("6053", "EXP", "DEBIT", None),
    "Commission & Fees": ("6060", "EXP", "DEBIT", None),
    "Insurance Expenses": ("6061", "EXP", "DEBIT", None),
    "Bad Debt": ("6062", "EXP", "DEBIT", None),
    "Contract Tender Fees": ("6063", "EXP", "DEBIT", None),
    "Base Station Repairs and Maintenance": ("6064", "EXP", "DEBIT", None),
    "Legal & Professional Fee": ("6070", "EXP", "DEBIT", None),
    "NCC Operating Licence": ("6071", "EXP", "DEBIT", None),
    "Finance Cost": ("6080", "EXP", "DEBIT", None),
    "Transportation & Travelling Expenses": ("6081", "EXP", "DEBIT", None),
    "Shipping & Delivery Expenses": ("6082", "EXP", "DEBIT", None),
    "Advertising Expenses": ("6083", "EXP", "DEBIT", None),
    "Audit Fee": ("6090", "EXP", "DEBIT", None),
    "Depreciation": ("6091", "EXP", "DEBIT", None),
    "Tax Audit Expense": ("6092", "EXP", "DEBIT", None),
    " Equipment Rental": ("6093", "EXP", "DEBIT", None),
    "Discount": ("6094", "EXP", "DEBIT", None),
    "Discounts given - COS": ("6095", "EXP", "DEBIT", None),
    "Other Expenses": ("6099", "EXP", "DEBIT", None),
    "VAT Paid": ("6100", "EXP", "DEBIT", None),
}

# Category definitions
CATEGORY_DEFS = {
    "FA": ("Fixed Assets", IFRSCategory.ASSETS, 1),
    "FA-AD": ("Accumulated Depreciation", IFRSCategory.ASSETS, 2),
    "BANK": ("Bank Accounts", IFRSCategory.ASSETS, 3),
    "CASH": ("Cash", IFRSCategory.ASSETS, 4),
    "INV": ("Inventory", IFRSCategory.ASSETS, 5),
    "AR": ("Trade & Other Receivables", IFRSCategory.ASSETS, 6),
    "AP": ("Trade & Other Payables", IFRSCategory.LIABILITIES, 10),
    "TAX-L": ("Tax Liabilities", IFRSCategory.LIABILITIES, 11),
    "LTL": ("Long-term Liabilities", IFRSCategory.LIABILITIES, 12),
    "EQ": ("Share Capital", IFRSCategory.EQUITY, 20),
    "RE": ("Retained Earnings", IFRSCategory.EQUITY, 21),
    "REV": ("Revenue", IFRSCategory.REVENUE, 30),
    "COS": ("Cost of Sales", IFRSCategory.EXPENSES, 40),
    "EXP": ("Operating Expenses", IFRSCategory.EXPENSES, 41),
}


def create_categories(db) -> dict:
    """Create account categories and return mapping."""
    print("\n--- Creating Account Categories ---")
    categories = {}

    for code, (name, ifrs_cat, order) in CATEGORY_DEFS.items():
        existing = (
            db.query(AccountCategory)
            .filter(AccountCategory.organization_id == DOTMAC_ORG_ID)
            .filter(AccountCategory.category_code == code)
            .first()
        )
        if existing:
            categories[code] = existing.category_id
            print(f"  [exists] {code}: {name}")
        else:
            cat = AccountCategory(
                category_id=uuid4(),
                organization_id=DOTMAC_ORG_ID,
                category_code=code,
                category_name=name,
                ifrs_category=ifrs_cat,
                hierarchy_level=1,
                display_order=order,
                is_active=True,
            )
            db.add(cat)
            db.flush()
            categories[code] = cat.category_id
            print(f"  [created] {code}: {name}")

    db.commit()
    return categories


def create_accounts(db, categories: dict) -> dict:
    """Create accounts and return mapping."""
    print("\n--- Creating Chart of Accounts ---")
    accounts = {}

    for tb_name, (code, cat_code, normal_bal, subledger) in ACCOUNT_MAP.items():
        # Clean the display name (remove _ACCUM suffix)
        display_name = tb_name.replace("_ACCUM", " - Accumulated Depreciation")
        display_name = display_name.replace("_EXP", " Expense")

        existing = (
            db.query(Account)
            .filter(Account.organization_id == DOTMAC_ORG_ID)
            .filter(Account.account_code == code)
            .first()
        )
        if existing:
            accounts[tb_name] = existing.account_id
            print(f"  [exists] {code}: {display_name}")
        else:
            acc = Account(
                account_id=uuid4(),
                organization_id=DOTMAC_ORG_ID,
                category_id=categories[cat_code],
                account_code=code,
                account_name=display_name,
                account_type=AccountType.POSTING,
                normal_balance=NormalBalance[normal_bal],
                is_active=True,
                is_posting_allowed=True,
                subledger_type=subledger,
                is_cash_equivalent=(cat_code in ("BANK", "CASH")),
            )
            db.add(acc)
            db.flush()
            accounts[tb_name] = acc.account_id
            print(f"  [created] {code}: {display_name}")

    db.commit()
    return accounts


def create_fiscal_years(db) -> dict:
    """Create fiscal years 2022-2024 with periods."""
    print("\n--- Creating Fiscal Years ---")
    fiscal_years = {}

    for year in [2022, 2023, 2024]:
        year_code = f"FY{year}"
        existing = (
            db.query(FiscalYear)
            .filter(FiscalYear.organization_id == DOTMAC_ORG_ID)
            .filter(FiscalYear.year_code == year_code)
            .first()
        )
        if existing:
            fiscal_years[year] = existing.fiscal_year_id
            print(f"  [exists] {year_code}")
            continue

        fy = FiscalYear(
            fiscal_year_id=uuid4(),
            organization_id=DOTMAC_ORG_ID,
            year_code=year_code,
            year_name=f"Fiscal Year {year}",
            start_date=date(year, 1, 1),
            end_date=date(year, 12, 31),
            is_closed=(year < 2024),  # 2022, 2023 are closed
        )
        db.add(fy)
        db.flush()
        fiscal_years[year] = fy.fiscal_year_id
        print(f"  [created] {year_code}")

        # Create monthly periods
        months = [
            (1, 31),
            (2, 28 if year % 4 != 0 else 29),
            (3, 31),
            (4, 30),
            (5, 31),
            (6, 30),
            (7, 31),
            (8, 31),
            (9, 30),
            (10, 31),
            (11, 30),
            (12, 31),
        ]
        for month, days in months:
            period = FiscalPeriod(
                fiscal_period_id=uuid4(),
                organization_id=DOTMAC_ORG_ID,
                fiscal_year_id=fy.fiscal_year_id,
                period_number=month,
                period_name=date(year, month, 1).strftime("%B %Y"),
                start_date=date(year, month, 1),
                end_date=date(year, month, days),
                status=PeriodStatus.HARD_CLOSED if year < 2024 else PeriodStatus.OPEN,
            )
            db.add(period)
        print(f"    Created 12 periods for {year_code}")

    db.commit()
    return fiscal_years


def read_trial_balance(year: int) -> dict:
    """Read TB file and return account balances."""
    df = pd.read_excel(f"{TB_DIR}/{year} TB.xlsx")
    balances = {}

    in_accum_depreciation = False

    for _idx, row in df.iterrows():
        item = str(row["ITEMS"]).strip() if pd.notna(row["ITEMS"]) else ""
        if not item:
            continue

        # Track if we're in accumulated depreciation section
        if "accumulated depreciation" in item.lower():
            in_accum_depreciation = True
            continue
        elif item.lower() in ("current assets", "cash and cash equivalent:"):
            in_accum_depreciation = False

        # Skip section headers
        if item.endswith(":") or "ADJUSTMENT" in item.upper() or "Please note" in item:
            continue

        debit = (
            row["ADJUSTED FINAL TOTAL"] if pd.notna(row["ADJUSTED FINAL TOTAL"]) else 0
        )
        credit = row.iloc[8] if pd.notna(row.iloc[8]) else 0

        try:
            debit = float(debit) if debit else 0
            credit = float(credit) if credit else 0

            if debit == 0 and credit == 0:
                continue

            # Handle accumulated depreciation accounts (same names as assets)
            if in_accum_depreciation and item in (
                "Office Equipment",
                "Motor Vehicle",
                "Furniture & Fittings",
                "Plant & Machinery",
            ):
                item = f"{item}_ACCUM"

            balances[item] = {
                "debit": Decimal(str(debit)),
                "credit": Decimal(str(credit)),
            }
        except (ValueError, TypeError):
            pass

    return balances


def create_opening_balance_journal(
    db, year: int, accounts: dict, fiscal_years: dict, balances: dict
):
    """Create opening balance journal entry for a year."""
    print(f"\n--- Creating Opening Balance Journal for {year} ---")

    # Get fiscal year and its first period
    fy_id = fiscal_years[year]
    period = (
        db.query(FiscalPeriod)
        .filter(FiscalPeriod.fiscal_year_id == fy_id)
        .filter(FiscalPeriod.period_number == 1)
        .first()
    )

    if not period:
        print(f"  [error] No period found for {year}")
        return

    # Check if journal already exists
    existing = (
        db.query(JournalEntry)
        .filter(JournalEntry.organization_id == DOTMAC_ORG_ID)
        .filter(JournalEntry.journal_number == f"OB-{year}")
        .first()
    )
    if existing:
        print(f"  [exists] Opening balance journal OB-{year}")
        return

    # Create journal entry
    je = JournalEntry(
        journal_entry_id=uuid4(),
        organization_id=DOTMAC_ORG_ID,
        fiscal_period_id=period.fiscal_period_id,
        journal_number=f"OB-{year}",
        journal_type=JournalType.OPENING,
        entry_date=date(year, 1, 1),
        posting_date=date(year, 1, 1),
        reference=f"OB-{year}",
        description=f"Opening Balance {year} - Imported from Trial Balance",
        currency_code="NGN",
        exchange_rate=Decimal("1.0"),
        status=JournalStatus.POSTED,
        source_module="IMPORT",
        created_by_user_id=UUID("00000000-0000-0000-0000-000000000001"),  # System user
    )
    db.add(je)
    db.flush()

    total_debit = Decimal("0")
    total_credit = Decimal("0")
    line_num = 1
    lines_created = 0

    for tb_name, bal in balances.items():
        if tb_name not in accounts:
            print(f"    [skip] Unknown account: {tb_name}")
            continue

        account_id = accounts[tb_name]
        debit = bal["debit"]
        credit = bal["credit"]

        if debit > 0:
            line = JournalEntryLine(
                line_id=uuid4(),
                journal_entry_id=je.journal_entry_id,
                line_number=line_num,
                account_id=account_id,
                description=f"Opening balance {year}",
                debit_amount=debit,
                credit_amount=Decimal("0"),
                debit_amount_functional=debit,
                credit_amount_functional=Decimal("0"),
                currency_code="NGN",
                exchange_rate=Decimal("1.0"),
            )
            db.add(line)
            total_debit += debit
            line_num += 1
            lines_created += 1

        if credit > 0:
            line = JournalEntryLine(
                line_id=uuid4(),
                journal_entry_id=je.journal_entry_id,
                line_number=line_num,
                account_id=account_id,
                description=f"Opening balance {year}",
                debit_amount=Decimal("0"),
                credit_amount=credit,
                debit_amount_functional=Decimal("0"),
                credit_amount_functional=credit,
                currency_code="NGN",
                exchange_rate=Decimal("1.0"),
            )
            db.add(line)
            total_credit += credit
            line_num += 1
            lines_created += 1

    # Update journal totals
    je.total_debit = total_debit
    je.total_credit = total_credit
    je.total_debit_functional = total_debit
    je.total_credit_functional = total_credit
    db.commit()

    difference = total_debit - total_credit
    print(f"  [created] OB-{year}: {lines_created} lines")
    print(f"    Total Debit:  {total_debit:>20,.2f}")
    print(f"    Total Credit: {total_credit:>20,.2f}")
    if abs(difference) > Decimal("0.01"):
        print(f"    Difference:   {difference:>20,.2f} (will be adjusted)")


def main():
    print("=" * 60)
    print(" DOTMAC TRIAL BALANCE IMPORT")
    print("=" * 60)

    db = SessionLocal()
    try:
        # Create categories
        categories = create_categories(db)

        # Create accounts
        accounts = create_accounts(db, categories)

        # Create fiscal years
        fiscal_years = create_fiscal_years(db)

        # Import opening balances for each year
        for year in [2022, 2023, 2024]:
            balances = read_trial_balance(year)
            print(f"\n  Read {len(balances)} accounts from {year} TB")
            create_opening_balance_journal(db, year, accounts, fiscal_years, balances)

        print("\n" + "=" * 60)
        print(" IMPORT COMPLETE")
        print("=" * 60)
        print(f"\n  Categories: {len(categories)}")
        print(f"  Accounts:   {len(accounts)}")
        print(f"  Fiscal Years: {len(fiscal_years)}")

    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
