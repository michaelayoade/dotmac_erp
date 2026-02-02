#!/usr/bin/env python
"""
Import Zenith Bank Statements from Excel files.

This script handles both Zenith statement formats:
- "Account Statement - Soft Copy" (older format, 2022-Sep 2024)
- "BOP_CBA_003_Report" (newer format, Oct 2024-Dec 2025)

Usage:
    poetry run python scripts/import_zenith_statements.py [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import UUID

import openpyxl
from sqlalchemy import select

from app.db import SessionLocal
from app.models.finance.banking.bank_account import (
    BankAccount,
    BankAccountStatus,
    BankAccountType,
)
from app.models.finance.banking.bank_statement import (
    BankStatementStatus,
    StatementLineType,
)
from app.models.finance.gl.account import Account
from app.services.finance.banking.bank_statement import (
    BankStatementService,
    StatementLineInput,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Path to statement files
STATEMENT_DIR = Path("/root/.dotmac/zenith statement")

# Account mapping: account_number -> (name, currency, file_pairs)
# file_pairs = [(old_format_file, new_format_file), ...]
ACCOUNT_CONFIG = {
    "1011649523": {
        "name": "Zenith 523 (Main)",
        "account_name": "DOTMAC TECHNOLOGIES LTD",
        "currency": "NGN",
        "files": [
            "Account Statement - Soft Copy.xlsx",
            "BOP_CBA_003_Report.xlsx",
        ],
    },
    "1016946461": {
        "name": "Zenith 461 (Services)",
        "account_name": "DOTMAC TECHNOLOGIES LTD SERVICES",
        "currency": "NGN",
        "files": [
            "Account Statement - Soft Copy (2).xlsx",
            "BOP_CBA_003_Report (2).xlsx",
        ],
    },
    "1016946454": {
        "name": "Zenith 454 (Int Project)",
        "account_name": "DOTMAC TECHNOLOGIES INT PROJECT",
        "currency": "NGN",
        "files": [
            "Account Statement - Soft Copy (1).xlsx",
            "BOP_CBA_003_Report (1).xlsx",
        ],
    },
    "5070061296": {
        "name": "Zenith USD",
        "account_name": "DOTMAC TECHNOLOGIES",
        "currency": "USD",
        "files": [
            "Account Statement - Soft Copy (4).xlsx",
            "BOP_CBA_003_Report (5).xlsx",
        ],
    },
}


@dataclass
class ParsedStatement:
    """Parsed statement data from Excel file."""

    account_number: str
    account_name: str
    currency: str
    period_start: date
    period_end: date
    opening_balance: Decimal
    closing_balance: Decimal
    total_debit: Decimal
    total_credit: Decimal
    transactions: List[Dict]
    source_file: str


def parse_date(value) -> Optional[date]:
    """Parse date from various formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        # Try different date formats
        for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y"]:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def parse_period(period_str: str) -> Tuple[Optional[date], Optional[date]]:
    """Parse period string like '01/01/2022 TO 30/09/2024'."""
    if not period_str:
        return None, None

    # Clean up the string
    period_str = str(period_str).strip()

    # Try different separators
    for sep in [" TO ", " to ", "-"]:
        if sep in period_str:
            parts = period_str.split(sep)
            if len(parts) == 2:
                start = parse_date(parts[0].strip())
                end = parse_date(parts[1].strip())
                if start and end:
                    return start, end

    return None, None


def parse_decimal(value) -> Optional[Decimal]:
    """Parse decimal from various formats."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, Decimal):
        return value
    if isinstance(value, str):
        # Clean up the string
        value = value.strip().replace(",", "").replace(" ", "")
        if not value or value == "-":
            return None
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


def extract_account_number(value: str) -> Optional[str]:
    """Extract account number from strings like 'CA         1011649523'."""
    if not value:
        return None
    match = re.search(r"(\d{10})", str(value))
    return match.group(1) if match else None


def parse_old_format_statement(filepath: Path) -> Optional[ParsedStatement]:
    """
    Parse older Zenith statement format (Account Statement - Soft Copy).

    Structure:
    - Row 5: Account name in col 0, "Account Number:" in col 6, account in col 8
    - Row 6: Currency in col 8
    - Row 7: Opening balance in col 8
    - Row 8: Total debit in col 8
    - Row 9: Total credit in col 8
    - Row 10: Closing balance in col 8
    - Row 11: Period in col 8
    - Row 13: Headers (DATE POSTED, VALUE DATE, DESCRIPTION, DEBIT, CREDIT, BALANCE)
    - Row 15+: Data (skip opening balance row)
    """
    logger.info(f"Parsing old format: {filepath.name}")

    try:
        wb = openpyxl.load_workbook(filepath, data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))

        # Extract header info
        account_name = str(rows[4][0]).strip() if rows[4][0] else ""
        account_number = extract_account_number(str(rows[4][8])) if rows[4][8] else None
        currency = str(rows[5][8]).strip() if rows[5][8] else "NGN"
        opening_balance = parse_decimal(rows[6][8])
        total_debit = parse_decimal(rows[7][8])
        total_credit = parse_decimal(rows[8][8])
        closing_balance = parse_decimal(rows[9][8])
        period_start, period_end = parse_period(str(rows[10][8]) if rows[10][8] else "")

        if not account_number:
            logger.warning(f"Could not extract account number from {filepath.name}")
            return None

        # Parse transactions (skip header rows and opening balance row)
        transactions = []
        for i, row in enumerate(rows[15:], start=16):
            date_posted = parse_date(row[0])
            if not date_posted:
                continue  # Skip non-transaction rows

            value_date = parse_date(row[2])
            description = str(row[3]).strip() if row[3] else ""
            debit = parse_decimal(row[6])
            credit = parse_decimal(row[7])
            balance = parse_decimal(row[9])

            # Skip opening balance line
            if "OPENING BALANCE" in description.upper():
                continue

            transactions.append({
                "line_number": len(transactions) + 1,
                "date_posted": date_posted,
                "value_date": value_date or date_posted,
                "description": description,
                "debit": abs(debit) if debit else None,
                "credit": credit,
                "balance": balance,
            })

        logger.info(f"  Parsed {len(transactions)} transactions from {filepath.name}")

        return ParsedStatement(
            account_number=account_number,
            account_name=account_name,
            currency=currency,
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening_balance or Decimal("0"),
            closing_balance=closing_balance or Decimal("0"),
            total_debit=abs(total_debit) if total_debit else Decimal("0"),
            total_credit=total_credit or Decimal("0"),
            transactions=transactions,
            source_file=filepath.name,
        )

    except Exception as e:
        logger.error(f"Error parsing {filepath.name}: {e}")
        return None


def parse_new_format_statement(filepath: Path) -> Optional[ParsedStatement]:
    """
    Parse newer Zenith statement format (BOP_CBA_003_Report).

    Structure:
    - Row 9: Account name in col 0, "Account Number:" in col 6, account in col 8
    - Row 10: Currency in col 8
    - Row 11: Opening balance in col 8
    - Row 12: Total debit in col 8
    - Row 13: Total credit in col 8
    - Row 14: Closing balance in col 8
    - Row 15: Period in col 8
    - Row 17: Headers
    - Row 20: Opening balance line (skip)
    - Row 21+: Data
    """
    logger.info(f"Parsing new format: {filepath.name}")

    try:
        wb = openpyxl.load_workbook(filepath, data_only=True)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))

        # Extract header info (0-indexed, so row 9 = index 8)
        account_name = str(rows[8][0]).strip() if rows[8][0] else ""
        account_number = extract_account_number(str(rows[8][8])) if rows[8][8] else None
        currency = str(rows[9][8]).strip() if rows[9][8] else "NGN"
        opening_balance = parse_decimal(rows[10][8])
        total_debit = parse_decimal(rows[11][8])
        total_credit = parse_decimal(rows[12][8])
        closing_balance = parse_decimal(rows[13][8])
        period_start, period_end = parse_period(str(rows[14][8]) if rows[14][8] else "")

        if not account_number:
            logger.warning(f"Could not extract account number from {filepath.name}")
            return None

        # Parse transactions (start from row 21, index 20)
        transactions = []
        for i, row in enumerate(rows[20:], start=21):
            # Parse date from string format like ' 16/10/2024'
            date_str = str(row[0]).strip() if row[0] else ""
            date_posted = parse_date(date_str)

            if not date_posted:
                continue  # Skip non-transaction rows

            value_date_str = str(row[2]).strip() if row[2] else ""
            value_date = parse_date(value_date_str)
            description = str(row[3]).strip() if row[3] else ""

            # In new format: debit in col 6, credit in col 7
            debit = parse_decimal(row[6])
            credit = parse_decimal(row[7])
            balance_str = str(row[9]).strip().replace(",", "") if row[9] else ""
            balance = parse_decimal(balance_str)

            # Skip opening balance line
            if "Opening Balance" in description:
                continue

            # Convert: debit > 0 means debit, credit > 0 means credit
            actual_debit = debit if debit and debit > 0 else None
            actual_credit = credit if credit and credit > 0 else None

            transactions.append({
                "line_number": len(transactions) + 1,
                "date_posted": date_posted,
                "value_date": value_date or date_posted,
                "description": description,
                "debit": actual_debit,
                "credit": actual_credit,
                "balance": balance,
            })

        logger.info(f"  Parsed {len(transactions)} transactions from {filepath.name}")

        return ParsedStatement(
            account_number=account_number,
            account_name=account_name,
            currency=currency,
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening_balance or Decimal("0"),
            closing_balance=closing_balance or Decimal("0"),
            total_debit=total_debit or Decimal("0"),
            total_credit=total_credit or Decimal("0"),
            transactions=transactions,
            source_file=filepath.name,
        )

    except Exception as e:
        logger.error(f"Error parsing {filepath.name}: {e}")
        import traceback
        traceback.print_exc()
        return None


def ensure_bank_account(
    db,
    org_id: UUID,
    account_number: str,
    config: Dict,
) -> BankAccount:
    """Ensure bank account exists, create if not."""

    # Check if account exists
    existing = db.execute(
        select(BankAccount).where(
            BankAccount.organization_id == org_id,
            BankAccount.account_number == account_number,
        )
    ).scalar_one_or_none()

    if existing:
        logger.info(f"Found existing bank account: {account_number}")
        return existing

    # Find GL account (Zenith Bank = 1200)
    gl_account = db.execute(
        select(Account).where(
            Account.organization_id == org_id,
            Account.account_code == "1200",
        )
    ).scalar_one_or_none()

    if not gl_account:
        raise ValueError("GL Account 1200 (Zenith Bank) not found")

    # Create new bank account
    account = BankAccount(
        organization_id=org_id,
        bank_name="Zenith Bank",
        bank_code="057",
        account_name=config["name"],
        account_number=account_number,
        account_type=BankAccountType.checking,
        currency_code=config["currency"],
        gl_account_id=gl_account.account_id,
        status=BankAccountStatus.active,
    )
    db.add(account)
    db.flush()

    logger.info(f"Created new bank account: {account_number} - {config['name']}")
    return account


def convert_to_statement_lines(
    transactions: List[Dict],
    start_line: int = 1,
) -> List[StatementLineInput]:
    """Convert parsed transactions to StatementLineInput objects."""
    lines = []

    for i, txn in enumerate(transactions):
        amount = txn["debit"] or txn["credit"] or Decimal("0")
        txn_type = (
            StatementLineType.debit if txn["debit"]
            else StatementLineType.credit
        )

        # Convert raw_data to JSON-serializable format
        raw_data = {
            "date_posted": txn["date_posted"].isoformat() if txn.get("date_posted") else None,
            "value_date": txn["value_date"].isoformat() if txn.get("value_date") else None,
            "description": txn.get("description"),
            "debit": str(txn["debit"]) if txn.get("debit") else None,
            "credit": str(txn["credit"]) if txn.get("credit") else None,
            "balance": str(txn["balance"]) if txn.get("balance") else None,
        }

        lines.append(StatementLineInput(
            line_number=start_line + i,
            transaction_date=txn["date_posted"],
            value_date=txn["value_date"],
            transaction_type=txn_type,
            amount=amount,
            description=txn["description"],
            running_balance=txn.get("balance"),
            raw_data=raw_data,
        ))

    return lines


def import_statements(dry_run: bool = False):
    """Main import function."""

    logger.info("=" * 60)
    logger.info("Zenith Bank Statement Import")
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN MODE - No changes will be made")

    with SessionLocal() as db:
        # Get organization ID from existing account
        existing = db.execute(
            select(BankAccount).where(
                BankAccount.bank_name.ilike("%zenith%")
            )
        ).scalars().first()

        if not existing:
            logger.error("No existing Zenith bank account found to determine org ID")
            return

        org_id = existing.organization_id
        logger.info(f"Organization ID: {org_id}")

        total_imported = 0
        total_skipped = 0

        for account_number, config in ACCOUNT_CONFIG.items():
            logger.info("")
            logger.info(f"Processing account: {account_number} ({config['name']})")
            logger.info("-" * 40)

            # Ensure bank account exists
            bank_account = ensure_bank_account(db, org_id, account_number, config)

            # Parse all statement files for this account
            all_transactions = []

            for filename in config["files"]:
                filepath = STATEMENT_DIR / filename
                if not filepath.exists():
                    logger.warning(f"  File not found: {filename}")
                    continue

                # Determine format and parse
                if filename.startswith("Account Statement"):
                    parsed = parse_old_format_statement(filepath)
                else:
                    parsed = parse_new_format_statement(filepath)

                if parsed:
                    all_transactions.extend(parsed.transactions)

            if not all_transactions:
                logger.warning(f"  No transactions parsed for {account_number}")
                continue

            # Sort by date and remove duplicates
            all_transactions.sort(key=lambda x: (x["date_posted"], x["description"]))

            # Deduplicate based on date + amount + description
            seen = set()
            unique_transactions = []
            for txn in all_transactions:
                key = (
                    txn["date_posted"],
                    str(txn["debit"] or txn["credit"]),
                    txn["description"][:50],
                )
                if key not in seen:
                    seen.add(key)
                    unique_transactions.append(txn)

            logger.info(f"  Total transactions: {len(all_transactions)}")
            logger.info(f"  Unique transactions: {len(unique_transactions)}")

            # Determine overall period
            dates = [t["date_posted"] for t in unique_transactions if t["date_posted"]]
            if not dates:
                continue

            period_start = min(dates)
            period_end = max(dates)

            # Calculate opening/closing from first/last transaction
            first_txn = unique_transactions[0]
            last_txn = unique_transactions[-1]

            # Calculate opening balance
            first_amount = first_txn["debit"] or first_txn["credit"] or Decimal("0")
            first_balance = first_txn.get("balance") or Decimal("0")
            if first_txn["debit"]:
                opening_balance = first_balance + first_amount
            else:
                opening_balance = first_balance - first_amount

            closing_balance = last_txn.get("balance") or Decimal("0")

            # Create statement number
            statement_number = f"ZENITH-{account_number}-{period_start.strftime('%Y%m%d')}-{period_end.strftime('%Y%m%d')}"

            logger.info(f"  Period: {period_start} to {period_end}")
            logger.info(f"  Opening Balance: {opening_balance:,.2f}")
            logger.info(f"  Closing Balance: {closing_balance:,.2f}")
            logger.info(f"  Statement Number: {statement_number}")

            if dry_run:
                logger.info("  [DRY RUN] Would import statement")
                continue

            # Convert to statement lines
            lines = convert_to_statement_lines(unique_transactions)

            # Import using BankStatementService
            service = BankStatementService()

            try:
                result = service.import_statement(
                    db=db,
                    organization_id=org_id,
                    bank_account_id=bank_account.bank_account_id,
                    statement_number=statement_number,
                    statement_date=period_end,
                    period_start=period_start,
                    period_end=period_end,
                    opening_balance=opening_balance,
                    closing_balance=closing_balance,
                    lines=lines,
                    import_source="zenith_excel",
                    import_filename=", ".join(config["files"]),
                    check_duplicates=True,
                    skip_duplicates=True,
                )

                logger.info(f"  Imported: {result.lines_imported} lines")
                logger.info(f"  Skipped: {result.lines_skipped} lines")
                logger.info(f"  Duplicates: {result.duplicates_found}")

                total_imported += result.lines_imported
                total_skipped += result.lines_skipped

                if result.errors:
                    for err in result.errors[:5]:
                        logger.warning(f"    Error: {err}")

                if result.warnings:
                    for warn in result.warnings[:5]:
                        logger.info(f"    Warning: {warn}")

            except Exception as e:
                logger.error(f"  Failed to import: {e}")
                import traceback
                traceback.print_exc()

        if not dry_run:
            db.commit()
            logger.info("")
            logger.info("=" * 60)
            logger.info(f"Import Complete: {total_imported} transactions imported, {total_skipped} skipped")
            logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import Zenith bank statements")
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't import")
    args = parser.parse_args()

    import_statements(dry_run=args.dry_run)
