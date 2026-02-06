#!/usr/bin/env python
"""
Import UBA Bank Statements from Excel files.

Handles password-protected UBA statement files.

Usage:
    poetry run python scripts/import_uba_statements.py [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from uuid import UUID

import msoffcrypto
import openpyxl
from sqlalchemy import select

from app.db import SessionLocal
from app.models.finance.banking.bank_account import (
    BankAccount,
    BankAccountStatus,
    BankAccountType,
)
from app.models.finance.banking.bank_statement import (
    StatementLineType,
)
from app.models.finance.gl.account import Account
from app.services.finance.banking.bank_statement import (
    BankStatementService,
    StatementLineInput,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Path to statement files
STATEMENT_DIR = Path("/root/.dotmac/zenith statement/uba/Uba statement 2022-2025")

# Account configuration
ACCOUNT_CONFIG = {
    "1018904696": {
        "name": "UBA 96 (Main)",
        "currency": "NGN",
        "password": "89046",
        "gl_account_code": "1202",  # UBA GL account
        "files": [
            "101xxxxx96.xlsx",
            "101xxxxx96 (1).xlsx",
        ],
    },
    "3004154294": {
        "name": "UBA USD",
        "currency": "USD",
        "password": "41542",
        "gl_account_code": "1202",  # UBA GL account
        "files": [
            "300xxxxx94.xlsx",
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
        for fmt in ["%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"]:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    return None


def parse_period(period_str: str) -> Tuple[Optional[date], Optional[date]]:
    """Parse period string like '01-Jan-2022 - 31-Dec-2023'."""
    if not period_str:
        return None, None

    period_str = str(period_str).strip()

    # Try different separators
    for sep in [" - ", " TO ", " to "]:
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
        value = value.strip().replace(",", "").replace(" ", "")
        if not value or value == "-":
            return None
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


def extract_account_number(value: str) -> Optional[str]:
    """Extract account number from strings like '1018904696 . '."""
    if not value:
        return None
    match = re.search(r"(\d{10})", str(value))
    return match.group(1) if match else None


def open_workbook(filepath: Path, password: Optional[str] = None):
    """Open Excel workbook, handling password protection."""
    try:
        # Try without password first
        return openpyxl.load_workbook(filepath, data_only=True)
    except Exception:
        if password:
            with open(filepath, "rb") as file:
                decrypted = io.BytesIO()
                office_file = msoffcrypto.OfficeFile(file)
                office_file.load_key(password=password)
                office_file.decrypt(decrypted)
                decrypted.seek(0)
                return openpyxl.load_workbook(decrypted, data_only=True)
        raise


def parse_uba_statement(
    filepath: Path, password: Optional[str] = None
) -> Optional[ParsedStatement]:
    """
    Parse UBA statement format.

    Structure:
    - Row 4: Account Number
    - Row 5: Account Name
    - Row 8: Opening Balance
    - Row 9: Total Credit
    - Row 10: Total Debit
    - Row 11: Closing Balance
    - Row 12: Currency
    - Row 13: Period
    - Row 17: Headers (Tran Date, Value Date, Narration, Chq. No, Debit, Credit, Balance)
    - Row 18+: Data
    """
    logger.info(f"Parsing UBA statement: {filepath.name}")

    try:
        wb = open_workbook(filepath, password)
        ws = wb.active

        rows = list(ws.iter_rows(values_only=True))

        # Extract header info (0-indexed)
        account_number = extract_account_number(str(rows[3][1])) if rows[3][1] else None
        account_name = str(rows[4][1]).strip() if rows[4][1] else ""
        opening_balance = parse_decimal(rows[7][1])
        total_credit = parse_decimal(rows[8][1])
        total_debit = parse_decimal(rows[9][1])
        closing_balance = parse_decimal(rows[10][1])
        currency = str(rows[11][1]).strip() if rows[11][1] else "NGN"
        period_start, period_end = parse_period(str(rows[12][1]) if rows[12][1] else "")

        if not account_number:
            logger.warning(f"Could not extract account number from {filepath.name}")
            return None

        # Parse transactions (start from row 18, index 17)
        transactions = []
        for i, row in enumerate(rows[17:], start=18):
            tran_date = parse_date(row[0])
            if not tran_date:
                continue

            value_date = parse_date(row[1])
            narration = str(row[2]).strip() if row[2] else ""
            chq_no = str(row[3]).strip() if row[3] else None
            debit = parse_decimal(row[4])
            credit = parse_decimal(row[5])
            balance = parse_decimal(row[6])

            # Skip opening balance line
            if "Opening Balance" in narration:
                continue

            # Skip lines with both debit and credit as 0
            if (not debit or debit == 0) and (not credit or credit == 0):
                continue

            transactions.append(
                {
                    "line_number": len(transactions) + 1,
                    "date_posted": tran_date,
                    "value_date": value_date or tran_date,
                    "description": narration,
                    "reference": chq_no,
                    "debit": debit if debit and debit > 0 else None,
                    "credit": credit if credit and credit > 0 else None,
                    "balance": balance,
                }
            )

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

    # Find GL account
    gl_account = db.execute(
        select(Account).where(
            Account.organization_id == org_id,
            Account.account_code == config["gl_account_code"],
        )
    ).scalar_one_or_none()

    if not gl_account:
        raise ValueError(f"GL Account {config['gl_account_code']} not found")

    # Create new bank account
    account = BankAccount(
        organization_id=org_id,
        bank_name="United Bank for Africa",
        bank_code="033",
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
        txn_type = StatementLineType.debit if txn["debit"] else StatementLineType.credit

        # Convert raw_data to JSON-serializable format
        raw_data = {
            "date_posted": txn["date_posted"].isoformat()
            if txn.get("date_posted")
            else None,
            "value_date": txn["value_date"].isoformat()
            if txn.get("value_date")
            else None,
            "description": txn.get("description"),
            "reference": txn.get("reference"),
            "debit": str(txn["debit"]) if txn.get("debit") else None,
            "credit": str(txn["credit"]) if txn.get("credit") else None,
            "balance": str(txn["balance"]) if txn.get("balance") else None,
        }

        lines.append(
            StatementLineInput(
                line_number=start_line + i,
                transaction_date=txn["date_posted"],
                value_date=txn["value_date"],
                transaction_type=txn_type,
                amount=amount,
                description=txn["description"],
                reference=txn.get("reference"),
                running_balance=txn.get("balance"),
                raw_data=raw_data,
            )
        )

    return lines


def import_statements(dry_run: bool = False):
    """Main import function."""

    logger.info("=" * 60)
    logger.info("UBA Bank Statement Import")
    logger.info("=" * 60)

    if dry_run:
        logger.info("DRY RUN MODE - No changes will be made")

    with SessionLocal() as db:
        # Get organization ID from existing bank account
        existing = db.execute(select(BankAccount).limit(1)).scalars().first()

        if not existing:
            logger.error("No existing bank account found to determine org ID")
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
            if not dry_run:
                bank_account = ensure_bank_account(db, org_id, account_number, config)
            else:
                bank_account = db.execute(
                    select(BankAccount).where(
                        BankAccount.organization_id == org_id,
                        BankAccount.account_number == account_number,
                    )
                ).scalar_one_or_none()

            # Parse all statement files for this account
            all_transactions = []
            first_opening = None
            last_closing = None
            earliest_date = None
            latest_date = None

            for filename in config["files"]:
                filepath = STATEMENT_DIR / filename
                if not filepath.exists():
                    logger.warning(f"  File not found: {filename}")
                    continue

                parsed = parse_uba_statement(filepath, config["password"])

                if parsed:
                    all_transactions.extend(parsed.transactions)

                    # Track opening/closing from first/last file
                    if first_opening is None:
                        first_opening = parsed.opening_balance
                        earliest_date = parsed.period_start
                    last_closing = parsed.closing_balance
                    latest_date = parsed.period_end

            if not all_transactions:
                logger.warning(f"  No transactions parsed for {account_number}")
                continue

            # Sort by date and remove duplicates
            all_transactions.sort(
                key=lambda x: (x["date_posted"], x["description"][:30])
            )

            # Deduplicate based on date + amount + description prefix
            seen = set()
            unique_transactions = []
            for txn in all_transactions:
                key = (
                    txn["date_posted"],
                    str(txn["debit"] or txn["credit"]),
                    txn["description"][:40],
                )
                if key not in seen:
                    seen.add(key)
                    unique_transactions.append(txn)

            logger.info(f"  Total transactions: {len(all_transactions)}")
            logger.info(f"  Unique transactions: {len(unique_transactions)}")

            # Determine period from transactions if not from headers
            dates = [t["date_posted"] for t in unique_transactions if t["date_posted"]]
            if dates:
                period_start = earliest_date or min(dates)
                period_end = latest_date or max(dates)
            else:
                continue

            opening_balance = first_opening or Decimal("0")
            closing_balance = last_closing or Decimal("0")

            # Create statement number
            statement_number = f"UBA-{account_number}-{period_start.strftime('%Y%m%d')}-{period_end.strftime('%Y%m%d')}"

            logger.info(f"  Period: {period_start} to {period_end}")
            logger.info(f"  Opening Balance: {opening_balance:,.2f}")
            logger.info(f"  Closing Balance: {closing_balance:,.2f}")
            logger.info(f"  Statement Number: {statement_number}")

            if dry_run:
                logger.info("  [DRY RUN] Would import statement")
                continue

            if not bank_account:
                logger.error("  Bank account not found")
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
                    import_source="uba_excel",
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
            logger.info(
                f"Import Complete: {total_imported} transactions imported, {total_skipped} skipped"
            )
            logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import UBA bank statements")
    parser.add_argument(
        "--dry-run", action="store_true", help="Parse only, don't import"
    )
    args = parser.parse_args()

    import_statements(dry_run=args.dry_run)
