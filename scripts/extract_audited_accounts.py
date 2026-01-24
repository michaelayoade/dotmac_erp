"""
Extract audited financial statement data from OCR text files.
Creates structured CSV output for data quality improvement.
"""

import re
import csv
from pathlib import Path
from decimal import Decimal
from dataclasses import dataclass, asdict
from typing import Optional

OCR_DIR = Path("/root/projects/dotmac_erp/books_backup/ocr_text")
OUTPUT_DIR = Path("/root/projects/dotmac_erp/books_backup")


@dataclass
class AuditedAccount:
    year: int
    account_category: str
    account_name: str
    amount_current: Decimal
    amount_prior: Optional[Decimal]
    note_reference: Optional[str]
    ifrs_classification: str  # ASSETS, LIABILITIES, EQUITY, REVENUE, EXPENSES


def parse_amount(text: str) -> Optional[Decimal]:
    """Parse currency amount from text."""
    if not text:
        return None
    # Remove commas and parentheses (for negative)
    cleaned = text.replace(",", "").replace("(", "-").replace(")", "").strip()
    # Handle dash/em-dash as zero
    if cleaned in ["-", "—", ""]:
        return Decimal("0")
    try:
        return Decimal(cleaned)
    except:
        return None


def extract_2022_accounts() -> list[AuditedAccount]:
    """Extract 2022 audited accounts from OCR text."""
    accounts = []

    # Balance Sheet - Assets
    accounts.extend([
        AuditedAccount(2022, "Non-Current Assets", "Property, Plant and Equipment", Decimal("78201206"), Decimal("91855808"), "1", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Inventories", Decimal("28411728"), Decimal("42672377"), "2", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Trade Receivables", Decimal("58291409"), Decimal("45113657"), "3", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Staff Loan", Decimal("0"), Decimal("620000"), "3", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Withholding Tax Receivable", Decimal("65599797"), Decimal("37825259"), "3", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Zenith Bank Plc", Decimal("105724794"), Decimal("9703896"), "4", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Heritage Bank", Decimal("100000"), Decimal("100000"), "4", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "United Bank for Africa", Decimal("208039"), Decimal("0"), "4", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Cash at Hand", Decimal("1460"), Decimal("80616"), "4", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Paystack", Decimal("416000"), Decimal("0"), "4", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Paystack OPEX Account", Decimal("33772"), Decimal("131597"), "4", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Flutterwave", Decimal("1394008"), Decimal("0"), "4", "ASSETS"),
        AuditedAccount(2022, "Current Assets", "Quick Teller", Decimal("1057"), Decimal("0"), "4", "ASSETS"),
    ])

    # Balance Sheet - Equity
    accounts.extend([
        AuditedAccount(2022, "Equity", "Share Capital", Decimal("14650000"), Decimal("14650000"), "5", "EQUITY"),
        AuditedAccount(2022, "Equity", "Retained Earnings", Decimal("98986276"), Decimal("75686098"), "6", "EQUITY"),
    ])

    # Balance Sheet - Liabilities
    accounts.extend([
        AuditedAccount(2022, "Non-Current Liabilities", "Long Term Borrowings", Decimal("94554299"), Decimal("82203961"), "7", "LIABILITIES"),
        AuditedAccount(2022, "Current Liabilities", "Trade Payables", Decimal("107613478"), Decimal("14367458"), "8", "LIABILITIES"),
        AuditedAccount(2022, "Current Liabilities", "Withholding Tax Payable", Decimal("2211412"), Decimal("0"), "8", "LIABILITIES"),
        AuditedAccount(2022, "Current Liabilities", "Pension Payable", Decimal("0"), Decimal("11780"), "8", "LIABILITIES"),
        AuditedAccount(2022, "Current Liabilities", "PAYE Payable", Decimal("0"), Decimal("31194"), "8", "LIABILITIES"),
        AuditedAccount(2022, "Current Liabilities", "NHF Payable", Decimal("0"), Decimal("1548"), "8", "LIABILITIES"),
        AuditedAccount(2022, "Current Liabilities", "Accrued Expenses", Decimal("1656670"), Decimal("7705496"), "8", "LIABILITIES"),
        AuditedAccount(2022, "Current Liabilities", "Income Tax Payable", Decimal("17800988"), Decimal("29798003"), "9", "LIABILITIES"),
        AuditedAccount(2022, "Current Liabilities", "Education Tax Payable", Decimal("1483416"), Decimal("2719112"), "9", "LIABILITIES"),
        AuditedAccount(2022, "Current Liabilities", "IT Levy Payable", Decimal("593366"), Decimal("928562"), "9", "LIABILITIES"),
    ])

    # Income Statement - Revenue
    accounts.extend([
        AuditedAccount(2022, "Revenue", "Internet Revenue", Decimal("513205564"), Decimal("326320232"), "10", "REVENUE"),
        AuditedAccount(2022, "Revenue", "Other Business Revenue", Decimal("1051801233"), Decimal("1063069468"), "10", "REVENUE"),
    ])

    # Income Statement - Cost of Sales
    accounts.extend([
        AuditedAccount(2022, "Cost of Sales", "Opening Inventory", Decimal("42672377"), Decimal("15837000"), "11", "EXPENSES"),
        AuditedAccount(2022, "Cost of Sales", "Purchases", Decimal("944502819"), Decimal("840236346"), "11", "EXPENSES"),
        AuditedAccount(2022, "Cost of Sales", "Internet Cost of Sales", Decimal("275961963"), Decimal("354186350"), "11", "EXPENSES"),
        AuditedAccount(2022, "Cost of Sales", "Customer Terminal Devices", Decimal("0"), Decimal("91303607"), "12", "EXPENSES"),
        AuditedAccount(2022, "Cost of Sales", "Installation and Maintenance", Decimal("0"), Decimal("76420676"), "12", "EXPENSES"),
        AuditedAccount(2022, "Cost of Sales", "Bandwidth and Interconnect", Decimal("275961963"), Decimal("186462067"), "12", "EXPENSES"),
    ])

    # Income Statement - Administrative Expenses
    accounts.extend([
        AuditedAccount(2022, "Administrative Expenses", "Staff Training", Decimal("6111734"), Decimal("95323"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Subscriptions", Decimal("7947574"), Decimal("0"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Medical Expenses", Decimal("2996059"), Decimal("1219500"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Printing & Stationery", Decimal("6674429"), Decimal("1664380"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Rent or Lease Payment", Decimal("5635500"), Decimal("4320000"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Utilities", Decimal("25918146"), Decimal("845000"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Telephone Bills", Decimal("2110127"), Decimal("941600"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Fuel & Lubricant", Decimal("14046903"), Decimal("1200000"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Pension Expense", Decimal("4353678"), Decimal("2468865"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "ITF & NSITF Expenses", Decimal("802855"), Decimal("445272"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Office Repairs & Maintenance", Decimal("17832673"), Decimal("11588140"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Tools Hire", Decimal("2682720"), Decimal("0"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "General Repairs & Maintenance", Decimal("14674650"), Decimal("2950000"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Commission & Fees", Decimal("1482667"), Decimal("0"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Insurance Expenses", Decimal("943980"), Decimal("0"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Contract Tender Fees", Decimal("1397259"), Decimal("6082637"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Base Station Repairs", Decimal("2009900"), Decimal("12110000"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Legal & Professional Fee", Decimal("2100000"), Decimal("0"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "NCC Operating Licence", Decimal("3496454"), Decimal("0"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Audit Fee", Decimal("600000"), Decimal("500000"), "13", "EXPENSES"),
        AuditedAccount(2022, "Administrative Expenses", "Depreciation", Decimal("16158678"), Decimal("15908270"), "13", "EXPENSES"),
    ])

    # Income Statement - Distribution Cost
    accounts.extend([
        AuditedAccount(2022, "Distribution Cost", "Transportation & Travelling", Decimal("25698354"), Decimal("3565480"), "14", "EXPENSES"),
        AuditedAccount(2022, "Distribution Cost", "Advertising Expenses", Decimal("11725860"), Decimal("11806083"), "14", "EXPENSES"),
        AuditedAccount(2022, "Distribution Cost", "Shipping & Delivery", Decimal("42157049"), Decimal("0"), "14", "EXPENSES"),
    ])

    # Income Statement - Personnel Cost
    accounts.extend([
        AuditedAccount(2022, "Personnel Cost", "Staff Salaries & Wages", Decimal("58344315"), Decimal("41867832"), "15", "EXPENSES"),
        AuditedAccount(2022, "Personnel Cost", "Staff Bonus", Decimal("6289251"), Decimal("0"), "15", "EXPENSES"),
    ])

    # Income Statement - Finance Cost
    accounts.extend([
        AuditedAccount(2022, "Finance Cost", "Interest Expense", Decimal("2912603"), Decimal("2699000"), None, "EXPENSES"),
    ])

    # Income Statement - Tax
    accounts.extend([
        AuditedAccount(2022, "Tax Expense", "Income Tax", Decimal("17800988"), Decimal("29798003"), "9", "EXPENSES"),
        AuditedAccount(2022, "Tax Expense", "Education Tax", Decimal("1483416"), Decimal("2719112"), "9", "EXPENSES"),
        AuditedAccount(2022, "Tax Expense", "IT Levy", Decimal("593366"), Decimal("928562"), "9", "EXPENSES"),
    ])

    return accounts


def extract_2023_accounts() -> list[AuditedAccount]:
    """Extract 2023 audited accounts from OCR text."""
    accounts = []

    # Balance Sheet - Assets (from gap report and OCR)
    accounts.extend([
        AuditedAccount(2023, "Non-Current Assets", "Property, Plant and Equipment", Decimal("83337841"), Decimal("78201206"), "1", "ASSETS"),
        AuditedAccount(2023, "Current Assets", "Inventories", Decimal("27676430"), Decimal("28411728"), "2", "ASSETS"),
        AuditedAccount(2023, "Current Assets", "Trade & Other Receivables", Decimal("99359079"), Decimal("123891205"), "3", "ASSETS"),
        AuditedAccount(2023, "Current Assets", "Cash and Cash Equivalent", Decimal("33408087"), Decimal("107879130"), "4", "ASSETS"),
    ])

    # Balance Sheet - Equity
    accounts.extend([
        AuditedAccount(2023, "Equity", "Share Capital", Decimal("14650000"), Decimal("14650000"), "5", "EQUITY"),
        AuditedAccount(2023, "Equity", "Retained Earnings", Decimal("6734261"), Decimal("98986276"), "6", "EQUITY"),
    ])

    # Balance Sheet - Liabilities
    accounts.extend([
        AuditedAccount(2023, "Non-Current Liabilities", "Long Term Borrowings", Decimal("194554302"), Decimal("94554299"), "7", "LIABILITIES"),
        AuditedAccount(2023, "Current Liabilities", "Trade & Other Payables", Decimal("27842874"), Decimal("111481560"), "8", "LIABILITIES"),
    ])

    # Income Statement
    accounts.extend([
        AuditedAccount(2023, "Revenue", "Total Revenue", Decimal("1348328386"), Decimal("1565006797"), "10", "REVENUE"),
        AuditedAccount(2023, "Cost of Sales", "Total Cost of Sales", Decimal("1189990275"), Decimal("1234725432"), "11", "EXPENSES"),
        AuditedAccount(2023, "Administrative Expenses", "Total Admin Expenses", Decimal("250590123"), Decimal("287103418"), "13", "EXPENSES"),
    ])

    return accounts


def extract_2024_accounts() -> list[AuditedAccount]:
    """Extract 2024 audited accounts from OCR text."""
    accounts = []

    # Balance Sheet - Assets
    accounts.extend([
        AuditedAccount(2024, "Non-Current Assets", "Property, Plant and Equipment", Decimal("63679445"), Decimal("83337841"), "1", "ASSETS"),
        AuditedAccount(2024, "Current Assets", "Inventories", Decimal("37438551"), Decimal("27676430"), "2", "ASSETS"),
        AuditedAccount(2024, "Current Assets", "Trade & Other Receivables", Decimal("88899523"), Decimal("99359079"), "3", "ASSETS"),
        AuditedAccount(2024, "Current Assets", "Cash and Cash Equivalent", Decimal("24321685"), Decimal("33408087"), "4", "ASSETS"),
    ])

    # Balance Sheet - Equity
    accounts.extend([
        AuditedAccount(2024, "Equity", "Share Capital", Decimal("14650000"), Decimal("14650000"), "5", "EQUITY"),
        AuditedAccount(2024, "Equity", "Retained Earnings", Decimal("-47008765"), Decimal("6734261"), "6", "EQUITY"),
    ])

    # Balance Sheet - Liabilities
    accounts.extend([
        AuditedAccount(2024, "Non-Current Liabilities", "Long Term Borrowings", Decimal("194554302"), Decimal("194554302"), "7", "LIABILITIES"),
        AuditedAccount(2024, "Current Liabilities", "Trade & Other Payables", Decimal("51634671"), Decimal("27842874"), "8", "LIABILITIES"),
        AuditedAccount(2024, "Current Liabilities", "Current Tax Payable", Decimal("508996"), Decimal("0"), "9", "LIABILITIES"),
    ])

    # Income Statement
    accounts.extend([
        AuditedAccount(2024, "Revenue", "Total Revenue", Decimal("720321657"), Decimal("1348328386"), "10", "REVENUE"),
        AuditedAccount(2024, "Cost of Sales", "Total Cost of Sales", Decimal("505563409"), Decimal("1189990275"), "11", "EXPENSES"),
        AuditedAccount(2024, "Administrative Expenses", "Total Admin Expenses", Decimal("267992278"), Decimal("250590123"), "13", "EXPENSES"),
        AuditedAccount(2024, "Tax Expense", "Income Tax Expense", Decimal("508996"), Decimal("0"), "9", "EXPENSES"),
    ])

    return accounts


def generate_summary_by_ifrs() -> dict:
    """Generate summary totals by IFRS classification and year."""
    all_accounts = (
        extract_2022_accounts() +
        extract_2023_accounts() +
        extract_2024_accounts()
    )

    summary = {}
    for acc in all_accounts:
        key = (acc.year, acc.ifrs_classification)
        if key not in summary:
            summary[key] = Decimal("0")
        summary[key] += acc.amount_current

    return summary


def main():
    # Extract all accounts
    accounts_2022 = extract_2022_accounts()
    accounts_2023 = extract_2023_accounts()
    accounts_2024 = extract_2024_accounts()

    all_accounts = accounts_2022 + accounts_2023 + accounts_2024

    # Write detailed accounts CSV
    output_path = OUTPUT_DIR / "audited_accounts_detailed.csv"
    with open(output_path, "w", newline="") as f:
        fieldnames = [
            "year", "ifrs_classification", "account_category",
            "account_name", "amount_current", "amount_prior", "note_reference"
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for acc in all_accounts:
            writer.writerow({
                "year": acc.year,
                "ifrs_classification": acc.ifrs_classification,
                "account_category": acc.account_category,
                "account_name": acc.account_name,
                "amount_current": float(acc.amount_current),
                "amount_prior": float(acc.amount_prior) if acc.amount_prior else "",
                "note_reference": acc.note_reference or "",
            })

    print(f"✅ Extracted {len(all_accounts)} audited accounts to {output_path}")

    # Generate summary by year and IFRS classification
    print("\n📊 Audited Financial Summary by Year:")
    print("-" * 70)

    summary = {}
    for acc in all_accounts:
        key = (acc.year, acc.ifrs_classification)
        if key not in summary:
            summary[key] = Decimal("0")
        summary[key] += acc.amount_current

    for year in [2022, 2023, 2024]:
        print(f"\n{year}:")
        for classification in ["ASSETS", "LIABILITIES", "EQUITY", "REVENUE", "EXPENSES"]:
            key = (year, classification)
            total = summary.get(key, Decimal("0"))
            print(f"  {classification:<15} ₦{float(total):>20,.2f}")

    # Write summary CSV
    summary_path = OUTPUT_DIR / "audited_ifrs_summary.csv"
    with open(summary_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["year", "ifrs_classification", "total_amount"])
        for (year, classification), total in sorted(summary.items()):
            writer.writerow([year, classification, float(total)])

    print(f"\n✅ Summary written to {summary_path}")

    # Generate Balance Sheet totals
    print("\n📋 Balance Sheet Verification (Assets = Liabilities + Equity):")
    print("-" * 70)

    for year in [2022, 2023, 2024]:
        assets = summary.get((year, "ASSETS"), Decimal("0"))
        liabilities = summary.get((year, "LIABILITIES"), Decimal("0"))
        equity = summary.get((year, "EQUITY"), Decimal("0"))
        check = assets - liabilities - equity

        print(f"\n{year}:")
        print(f"  Assets:               ₦{float(assets):>20,.2f}")
        print(f"  Liabilities:          ₦{float(liabilities):>20,.2f}")
        print(f"  Equity:               ₦{float(equity):>20,.2f}")
        print(f"  Difference:           ₦{float(check):>20,.2f} {'✅' if abs(check) < 1 else '⚠️'}")


if __name__ == "__main__":
    main()
