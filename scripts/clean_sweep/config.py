"""
Shared configuration for the 2025 Clean Sweep scripts.

Contains:
- Organization and user constants
- MySQL connection helper (ERPNext MariaDB)
- Account mapping: ERPNext account name → DotMac account code
- Common utility functions
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000001")

# Date range covering all 2025 fiscal data (Jan 2025 – Jan 2026 inclusive)
DATE_START = date(2025, 1, 1)
DATE_END = date(2026, 2, 1)  # exclusive upper bound

CURRENCY_CODE = "NGN"

# GL batch sizes
GL_BATCH_SIZE = 500  # vouchers per commit in Phase 3
DOC_BATCH_SIZE = 1000  # source docs per commit in Phase 4

# ---------------------------------------------------------------------------
# MySQL connection (ERPNext MariaDB)
# ---------------------------------------------------------------------------


def mysql_connect() -> Any:
    """Open a read-only connection to the ERPNext MariaDB database."""
    import pymysql

    return pymysql.connect(
        host=os.getenv("ERPNEXT_SQL_HOST", "erpnext_temp_maria"),
        port=int(os.getenv("ERPNEXT_SQL_PORT", "3306")),
        user=os.getenv("ERPNEXT_SQL_USER", "root"),
        password=os.getenv("ERPNEXT_SQL_PASSWORD", "root"),
        database=os.getenv("ERPNEXT_SQL_DATABASE", "erpnext_temp"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


# ---------------------------------------------------------------------------
# Account mapping: ERPNext account name → DotMac account code
#
# The DotMac chart of accounts uses numbered codes (1100, 4000, 6030, etc.).
# ERPNext uses descriptive names with " - DT" suffix.
# This dict maps every ERPNext GL account to the correct numbered code.
#
# Accounts that don't exist yet in DotMac will be created by Phase 2.
# ---------------------------------------------------------------------------

ACCOUNT_MAPPING: dict[str, str] = {
    # ── Assets (1xxx) ──────────────────────────────────────────────
    "Accounts Receivable - DT": "1400",
    "Advance Tax - DT": "1420",
    "Application of Funds (Assets) - DT": "1400",  # Control account → receivables
    "Asset Received but not billed - DT": "1430",  # → Prepayment
    "Cash CBD - DT": "1220",  # → Cash at Hand
    "Cash expenses - DT": "1220",  # → Cash at Hand
    "Cash Garki - DT": "1221",  # NEW: Cash Garki
    "Cash Lagos - DT": "1222",  # NEW: Cash Lagos
    "Customer Advance Account - DT": "1430",  # → Prepayment
    "Employee Advance - DT": "1410",  # → Staff Loan
    "Finished Goods - DT": "1300",  # → Materials
    "First Bank Nigeria - DT": "1203",  # → First Bank
    "Flutterwave - DT": "1212",  # → Flutterwave
    "Flutterwave Opex - DT": "1213",  # → Flutterwave OPEX
    "Goods In Transit - DT": "1310",  # → Goods in Transit
    "Heritage Bank - DT": "1201",  # → Heritage Bank
    "Input VAT - DT": "1440",  # → Input VAT
    "Inter Branch Account - DT": "1450",  # NEW: Inter-Branch Account
    "Internal Use - DT": "1300",  # → Materials
    "Inventory Asset - DT": "1300",  # → Materials
    "Inventory - DT": "1300",  # → Materials
    "Paystack - DT": "1211",  # → Paystack OPEX Account
    "Paystack OPEX - DT": "1211",  # → Paystack OPEX Account
    "Petty Cash - DT": "1220",  # → Cash at Hand
    "Prepaid Expenses - DT": "1430",  # → Prepayment
    "Quick Teller - DT": "1214",  # → Quick Teller
    "Staff Loan - DT": "1410",  # → Staff Loan
    "Staff Loan - DT - DT": "1410",  # → Staff Loan (duplicate)
    "TAJ Bank - DT": "1208",  # → TAJ Bank
    "Tax Deducted at Source - DT": "1420",  # → Withholding Taxes
    "TDS Receivable - DT": "1420",  # → Withholding Taxes
    "Temporary Opening Account - DT": "1460",  # NEW: Temporary Opening
    "Total Card Garki 1 - DT": "1215",  # NEW: Total Card Garki 1
    "Total Card Garki 2 - DT": "1216",  # NEW: Total Card Garki 2
    "Total Card Gudu - DT": "1217",  # NEW: Total Card Gudu
    "Total Card Gwarinpa - DT": "1218",  # NEW: Total Card Gwarinpa
    "Total Card Jabi - DT": "1219",  # NEW: Total Card Jabi
    "UBA Bank - DT": "1202",  # → UBA
    "Undeposited Funds - DT": "6035",  # → Undeposited Funds
    "Work In Progress - DT": "1300",  # → Materials
    "Zenith 454 Bank - DT": "1206",  # → Zenith 454 Bank
    "Zenith 461 Bank - DT": "1205",  # → Zenith 461 Bank
    "Zenith 523 Bank - DT": "1204",  # → Zenith 523 Bank
    "Zenith USD - DT": "1207",  # → Zenith USD Bank
    # Fixed assets
    "Fixed Asset Account - DT": "1100",  # → Office Equipment
    "Furniture and Fixtures - DT": "1120",  # → Furniture & Fittings
    "Office Equipment - DT": "1100",  # → Office Equipment
    "Plant and  Machinery - DT": "1130",  # → Plant & Machinery
    "Software Purchases - DT": "1100",  # → Office Equipment
    "Vehicle- DT - DT": "1110",  # → Motor Vehicle
    "Vehicle-DT - DT": "1110",  # → Motor Vehicle
    "Vehicles - DT": "1110",  # → Motor Vehicle
    # Accumulated depreciation
    "Accumulated depreciation on Vehicles - DT": "1110-AD",
    "Accumulated depreciation on property, plant and equipment - DT": "1130-AD",
    "Accumulated Depreciation - Furniture and fittings - DT": "1120-AD",
    "Accumulated Depreciation - Office Equipment - DT": "1100-AD",
    "Furniture and Fixtures:Depreciation - DT": "1120-AD",
    "Office Equipment:Depreciation - DT": "1100-AD",
    "Vehicles:Depreciation - DT": "1110-AD",
    # ── Liabilities (2xxx) ─────────────────────────────────────────
    "Accrued Expenses - DT": "2020",  # → Accrued Expenses
    "Accrued non-current liabilities - DT": "2020",  # → Accrued Expenses
    "Current Tax Payable - DT": "2100",  # → Income Tax
    "Current Tax Payable:IT Tax - DT": "2102",  # → IT Levy
    "Deferred Income - DT": "6036",  # → Unearned Revenue
    "Dividends payable - DT": "2050",  # NEW: Dividends Payable
    "Duties and Taxes - DT": "2110",  # → WHT
    "Employee Reimbursements - DT": "2030",  # → Employee Reimbursables
    "Expense Payable - DT": "2000",  # → Trade Payables
    "Leave Allowance Payables - DT": "2040",  # → Salaries Payable
    "Long-term debt - DT": "2500",  # → Long Term Borrowings
    "NHF Payables - DT": "2132",  # → NHF Payables
    "PAYE Payables - DT": "2131",  # → Payee
    "Payroll Clearing - DT": "2040",  # → Salaries Payable
    "Payroll liabilities - DT": "2040",  # → Salaries Payable
    "Pension Payables - DT": "2130",  # → Pension
    "Prepaid Income - DT": "6036",  # → Unearned Revenue
    "Short term loan - DT": "2500",  # → Long Term Borrowings
    "Source of Funds (Liabilities) - DT": "2000",  # Control → Trade Payables
    "TDS Payable - DT": "2110",  # → WHT
    "Trade and Other Payables - DT": "2000",  # → Trade Payables
    "Trade and Other Payables (USD) - DT": "2000",  # → Trade Payables
    "Withholding Tax - DT": "1420",  # → Withholding Taxes (ASSET — WHT deducted by customers)
    "Withholding Tax Liabilities - DT": "2110",  # → WHT (LIABILITY — WHT deducted from suppliers)
    # ── Equity (3xxx) ──────────────────────────────────────────────
    "Directors Account - DT": "3200",  # → Directors Current Account
    "Dividend disbursed - DT": "3100",  # → Retained Earnings
    "Drawings - DT": "3200",  # → Directors Current Account
    "Equity - DT": "3000",  # → Issued and Fully Paid
    "Opening Balance Adjustments - DT": "3100",  # → Retained Earnings
    "Opening Balance Offset - DT": "3100",  # → Retained Earnings
    "opening balance write off - DT": "3100",  # → Retained Earnings
    "Other comprehensive income - DT": "3010",  # → Other Component Equity
    "Owner's Equity - DT": "3000",  # → Issued and Fully Paid
    "Retained Earnings - DT": "3100",  # → Retained Earnings
    "Share capital - DT": "3000",  # → Issued and Fully Paid
    "Tag Adjustments - DT": "3100",  # → Retained Earnings
    # ── Revenue (4xxx) ─────────────────────────────────────────────
    "cash sales - DT": "4010",  # → Other Business Revenue
    "Dividend income - DT": "4010",  # → Other Business Revenue
    "General Income - DT": "4010",  # → Other Business Revenue
    "Income - DT": "4000",  # → Internet Revenue
    "Interest Income - DT": "4010",  # → Other Business Revenue
    "Internet Sales - DT": "4000",  # → Internet Revenue
    "Late Fee Income - DT": "4010",  # → Other Business Revenue
    "Other operating income (expenses) - DT": "4010",  # → Other Business Revenue
    "Output VAT - DT": "4020",  # → VAT (Out Put)
    "Sales - DT": "4000",  # → Internet Revenue
    "Sales to Customers (Cash) - DT": "4000",  # → Internet Revenue
    "Sales wholesale - DT": "4000",  # → Internet Revenue
    "Sales Without Invoice - DT": "4000",  # → Internet Revenue
    "Stamp duty deducted from sales - DT": "4030",  # → Stampduty Deducted At Source
    "Stampduty Deducted At Source - DT": "4030",  # → Stampduty Deducted At Source
    "VAT deducted at source - DT": "4031",  # → Value Added Tax Withheld
    "VAT - DT": "2120",  # → VAT Payables (liability!)
    "VAT Payable - DT": "2120",  # → VAT Payables
    "VAT (Tax Exempt) - DT": "2120",  # → VAT Payables
    # ── COGS (5xxx) ────────────────────────────────────────────────
    "Bandwidth and Interconnect - DT": "5030",  # → Purchase of Bandwidth
    "Cost of Goods Sold - DT": "5000",  # → Purchases
    "Cost of Labour - DT": "5010",  # → Customer Terminal Devices
    "Cost of sales - DT": "5000",  # → Purchases
    "Direct Cost - DT": "5000",  # → Purchases
    "Direct labour - COS - DT": "5011",  # NEW: Direct Labour COS
    "Direct Labour Project - DT": "5012",  # NEW: Direct Labour Project
    "Discounts given - COS - DT": "6095",  # → Discounts given - COS
    "Foreign Exchange Loss- COS - DT": "6031",  # → Exchange gain or Loss
    "Freight and delivery - COS - DT": "6082",  # → Shipping & Delivery
    "Internet Cost of sales - DT": "5030",  # → Purchase of Bandwidth
    "Materials - COS - DT": "5013",  # NEW: Materials COS
    "Materials - COS - DT - DT": "5013",  # NEW: Materials COS (dup)
    "Purchases - DT": "5000",  # → Purchases
    "Purchases - DT - DT": "5000",  # → Purchases (duplicate)
    "Purchase Discounts - DT": "6094",  # → Discount
    "Subcontractors - COS - DT": "5014",  # NEW: Subcontractors COS
    "Supplies - DT": "5000",  # → Purchases
    # ── Expenses (6xxx) ────────────────────────────────────────────
    "Administrative and General Expenses - DT": "6099",  # → Other Expenses
    "Administrative Expense - DT": "6099",  # → Other Expenses
    "Advertising Expenses and Marketing - DT": "6083",  # → Advertising Expenses
    "Allowance for bad debt - DT": "6062",  # → Bad Debt
    "Amortisation expense - DT": "6091",  # → Depreciation
    "Bad Debt - DT": "6062",  # → Bad Debt
    "Bank charges - DT": "6080",  # → Finance Cost
    "Bank Fees and Charges - DT": "6080",  # → Finance Cost
    "Base Station Repairs and Maintenance - DT": "6064",  # → Base Station Repairs
    "Car Repairs and Maintenance - DT": "6053",  # → Motor Vehicle Repairs
    "Commissions and fees - DT": "6060",  # → Commission & Fees
    "Consultant Expense - DT": "6030",  # → Consultancy
    "Credit Card Charges - DT": "6080",  # → Finance Cost
    "Depreciation Expense - DT": "6091",  # → Depreciation
    "Discount - DT": "6094",  # → Discount
    "Equipment Rental - DT": "6093",  # → Equipment Rental
    "Exchange Gain or Loss - DT": "6031",  # → Exchange gain or Loss
    "Expenses - DT": "6099",  # → Other Expenses
    "fees and Licenses - DT": "6010",  # → Statutory Expenses
    "Fuel/Mileage Expenses - DT": "6024",  # → Fuel & Lubricant
    "fueling - DT": "6024",  # → Fuel & Lubricant
    "Government Tender Fees - DT": "6063",  # → Contract Tender Fees
    "Income tax expense - DT": "6092",  # → Tax Audit Expense
    "Insurance - Disability - DT": "6061",  # → Insurance Expenses
    "Insurance - General - DT": "6061",  # → Insurance Expenses
    "Insurance - Liability - DT": "6061",  # → Insurance Expenses
    "Interest expense - DT": "6080",  # → Finance Cost
    "IT and Internet Expenses - DT": "6032",  # → IT & Internet Expenses
    "Janitorial Expense - DT": "6033",  # → Janitorial Expenses
    "Legal and professional fees - DT": "6070",  # → Legal & Professional Fee
    "Lodging - DT": "6037",  # → Accommodation Expenses
    "Loss on discontinued operations, net of tax - DT": "6099",
    "Loss on disposal of assets - DT": "6099",  # → Other Expenses
    "Management compensation - DT": "6000",  # → Staff Salaries
    "Marketing 2 - DT": "6083",  # → Advertising Expenses
    "Meals and Entertainment - DT": "6051",  # → Entertainment
    "Medical Expenses - DT": "6013",  # → Medical Expenses
    "Nhf Charges/Expenses - DT": "6002",  # → NHF Charges/Expenses
    "Office Repairs and Maintenance - DT": "6050",  # → Office Repairs
    "Offline Sales Weekly Stipend - DT": "6004",  # → Contract Labour
    "Other Charges - DT": "6099",  # → Other Expenses
    "Other Expenses - DT": "6099",  # → Other Expenses
    "Other Statutory Expenses (NSITF/ITF) - DT": "6040",  # → ITF
    "Parking - DT": "6081",  # → Transportation
    "Paye Expense - DT": "6001",  # → PAYE expenses
    "Pension Charges/Expenses - DT": "6039",  # → Pension Expense
    "Postage - DT": "6082",  # → Shipping & Delivery
    "Reconciliation Discrepancies - DT": "6034",  # → Reconciliation Discrepancies
    "Rent or lease payments - DT": "6021",  # → Rent or Lease Payment
    "Repair of computers - DT": "6032",  # → IT & Internet Expenses
    "Repairs and Maintenance - DT": "6050",  # → Office Repairs
    "Round off - DT": "6099",  # → Other Expenses
    "Salaries and Employee Wages - DT": "6000",  # → Staff Salaries
    "Salaries - DT": "6000",  # → Staff Salaries
    "Salary Account - DT": "6000",  # → Staff Salaries
    "Security and guards - DT": "6011",  # → Security Expenses
    "Shipping and delivery expense - DT": "6082",  # → Shipping & Delivery
    "Shipping Charge - DT": "6082",  # → Shipping & Delivery
    "Site Logistics - DT": "6004",  # → Contract Labour
    "Staff training - DT": "6003",  # → Staff Training
    "Staff Welfare - DT": "6004",  # → Contract Labour
    "Stamp duty Paid - DT": "6010",  # → Statutory Expenses
    "Stationery and printing - DT": "6020",  # → Printing & stationery
    "Stationery and printing - DT - DT": "6020",  # → Printing & stationery
    "Statutory Payments - DT": "6010",  # → Statutory Expenses
    "Subscriptions and Renewals - DT": "6012",  # → Subscription & Renewal
    "Telephone Expense - DT": "6023",  # → Telephone bills
    "Training - DT": "6003",  # → Staff Training
    "Transportation Expense - DT": "6081",  # → Transportation
    "Travel Expense - DT": "6081",  # → Transportation
    "Uncategorized - DT": "6099",  # → Other Expenses
    "Unearned Revenue - DT": "6036",  # → Unearned Revenue
    "Uniforms and Office Wears - DT": "6004",  # → Contract Labour
    "Unrealised loss on securities, net of tax - DT": "6031",  # → Exchange gain/Loss
    "Utilities - DT": "6022",  # → Utilities
    "Withholding tax Expense - DT": "2110",  # → WHT (liability)
    "Inventory Shrinkage - DT": "5000",  # → Purchases
    "VAT Paid - DT": "6100",  # → VAT Paid
    "VAT (Purchase) - DT": "1440",  # → Input VAT
    "Payroll Rounding Expense - DT": "6101",  # → Payroll Rounding Expense
}

# New accounts that need to be created in Phase 2.
# {code: (name, normal_balance, parent_code_for_category)}
NEW_ACCOUNTS: dict[str, tuple[str, str, str]] = {
    "1221": ("Cash Garki", "DEBIT", "1220"),
    "1222": ("Cash Lagos", "DEBIT", "1220"),
    "1215": ("Total Card Garki 1", "DEBIT", "1211"),
    "1216": ("Total Card Garki 2", "DEBIT", "1211"),
    "1217": ("Total Card Gudu", "DEBIT", "1211"),
    "1218": ("Total Card Gwarinpa", "DEBIT", "1211"),
    "1219": ("Total Card Jabi", "DEBIT", "1211"),
    "1450": ("Inter-Branch Account", "DEBIT", "1400"),
    "1460": ("Temporary Opening Account", "DEBIT", "1400"),
    "2050": ("Dividends Payable", "CREDIT", "2000"),
    "5011": ("Direct Labour COS", "DEBIT", "5010"),
    "5012": ("Direct Labour Project", "DEBIT", "5010"),
    "5013": ("Materials COS", "DEBIT", "5000"),
    "5014": ("Subcontractors COS", "DEBIT", "5000"),
}

# Voucher type → (source_module, source_document_type) mapping for journals
VOUCHER_TYPE_MAP: dict[str, tuple[str, str]] = {
    "Sales Invoice": ("ar", "INVOICE"),
    "Payment Entry": ("ar", "CUSTOMER_PAYMENT"),  # default; refined by party_type
    "Journal Entry": ("gl", "JOURNAL"),
    "Purchase Invoice": ("ap", "SUPPLIER_INVOICE"),
    "Expense Claim": ("expense", "EXPENSE_CLAIM"),
}

# Payment Entry subtypes based on payment_type + party_type
PE_SUBTYPE_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("Receive", "Customer"): ("ar", "CUSTOMER_PAYMENT"),
    ("Pay", "Supplier"): ("ap", "SUPPLIER_PAYMENT"),
    ("Pay", "Employee"): ("expense", "EXPENSE_REIMBURSEMENT"),
    ("Internal Transfer", ""): ("banking", "INTERBANK_TRANSFER"),
}

# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def norm_text(value: Any) -> str | None:
    """Normalize text: strip whitespace, return None for empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def to_decimal(value: Any) -> Decimal:
    """Convert any value to Decimal; default 0 for None/empty."""
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def to_date(value: Any) -> date | None:
    """Convert any value to a date object."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except (ValueError, TypeError):
        return None


def setup_logging(name: str) -> logging.Logger:
    """Configure logging for a phase script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger(name)
