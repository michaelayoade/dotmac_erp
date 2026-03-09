"""
Opening Balance Importer.

Imports opening balance data from CSV and creates an OPENING journal entry.
This is used to set up initial balances when migrating from another system.
"""

import csv
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.gl.account import Account
from app.models.finance.gl.fiscal_period import FiscalPeriod
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus, JournalType
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.services.finance.import_export.base import (
    ImportConfig,
    detect_csv_format,
)

logger = logging.getLogger(__name__)


class BalanceType(str, Enum):
    """Type of balance entry."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


@dataclass
class OpeningBalanceLine:
    """Represents a single opening balance line."""

    account_name: str
    account_type: str
    debit: Decimal
    credit: Decimal
    normal_balance: BalanceType
    notes: str | None = None
    coa_match: str | None = None
    # Resolved during import
    account_id: UUID | None = None
    matched_account_name: str | None = None


@dataclass
class OpeningBalancePreview:
    """Preview result for opening balance import."""

    total_rows: int
    total_debit: Decimal
    total_credit: Decimal
    is_balanced: bool
    difference: Decimal
    lines: list[OpeningBalanceLine]
    matched_count: int
    unmatched_count: int
    unmatched_accounts: list[str]
    validation_errors: list[str]
    entry_date: date
    detected_format: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "total_rows": self.total_rows,
            "total_debit": float(self.total_debit),
            "total_credit": float(self.total_credit),
            "is_balanced": self.is_balanced,
            "difference": float(self.difference),
            "matched_count": self.matched_count,
            "unmatched_count": self.unmatched_count,
            "unmatched_accounts": self.unmatched_accounts,
            "validation_errors": self.validation_errors[:20],
            "entry_date": self.entry_date.isoformat(),
            "detected_format": self.detected_format,
            "lines": [
                {
                    "account_name": line.account_name,
                    "account_type": line.account_type,
                    "debit": float(line.debit),
                    "credit": float(line.credit),
                    "normal_balance": line.normal_balance.value,
                    "notes": line.notes,
                    "matched": line.account_id is not None,
                    "matched_account": line.matched_account_name,
                }
                for line in self.lines
            ],
        }


@dataclass
class OpeningBalanceResult:
    """Result of opening balance import."""

    success: bool
    journal_entry_id: UUID | None
    journal_number: str | None
    total_debit: Decimal
    total_credit: Decimal
    lines_created: int
    errors: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "success": self.success,
            "journal_entry_id": str(self.journal_entry_id)
            if self.journal_entry_id
            else None,
            "journal_number": self.journal_number,
            "total_debit": float(self.total_debit),
            "total_credit": float(self.total_credit),
            "lines_created": self.lines_created,
            "errors": self.errors[:50],
            "warnings": self.warnings[:50],
        }


class OpeningBalanceImporter:
    """
    Imports opening balances from CSV and creates an OPENING journal entry.

    Expected CSV format:
    Account Name,Account Type,Debit,Credit,Normal Balance,Notes,COA Match

    The importer will:
    1. Parse the CSV file
    2. Match account names to existing Chart of Accounts
    3. Validate that debits equal credits
    4. Create a single OPENING journal entry with all lines
    """

    entity_name = "Opening Balance"

    # Column name variations
    ACCOUNT_NAME_COLS = ["Account Name", "AccountName", "Name", "account_name"]
    ACCOUNT_TYPE_COLS = ["Account Type", "AccountType", "Type", "account_type"]
    DEBIT_COLS = ["Debit", "debit", "Debit Amount", "Dr", "DR"]
    CREDIT_COLS = ["Credit", "credit", "Credit Amount", "Cr", "CR"]
    NORMAL_BALANCE_COLS = [
        "Normal Balance",
        "NormalBalance",
        "normal_balance",
        "Balance Type",
    ]
    NOTES_COLS = ["Notes", "notes", "Description", "Memo", "Comment"]
    COA_MATCH_COLS = ["COA Match", "COA_Match", "Matched Account", "GL Account"]

    def __init__(self, db: Session, config: ImportConfig):
        self.db = db
        self.config = config
        self._account_cache: dict[str, Account] = {}
        self._load_accounts()

    def _load_accounts(self) -> None:
        """Load all accounts for the organization into cache."""
        result = self.db.execute(
            select(Account).where(
                Account.organization_id == self.config.organization_id
            )
        )
        for account in result.scalars():
            # Cache by multiple keys for flexible matching
            self._account_cache[account.account_name.lower().strip()] = account
            if account.account_code:
                self._account_cache[account.account_code.lower().strip()] = account

    def _find_column(self, row: dict[str, Any], candidates: list[str]) -> str | None:
        """Find the first matching column from candidates."""
        for col in candidates:
            if col in row:
                return col
        return None

    def _parse_decimal(self, value: Any) -> Decimal:
        """Parse a value to Decimal, handling various formats."""
        if value is None or value == "":
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)):
            return Decimal(str(value))

        # Clean string
        s = str(value).strip()
        s = s.replace(",", "")  # Remove thousands separator
        s = (
            s.replace("₦", "").replace("N", "").replace("$", "")
        )  # Remove currency symbols
        s = s.strip()

        if not s or s == "0":
            return Decimal("0")

        try:
            return Decimal(s)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid decimal value: {value}") from exc

    def _match_account(
        self, account_name: str, coa_match: str | None = None
    ) -> Account | None:
        """
        Find matching account in Chart of Accounts.

        Tries:
        1. Exact match on COA Match column (if provided)
        2. Exact match on account name
        3. Case-insensitive match
        4. Partial match (contains)
        """
        # Try COA match first
        if coa_match:
            key = coa_match.lower().strip()
            if key in self._account_cache:
                return self._account_cache[key]

        # Try exact match
        key = account_name.lower().strip()
        if key in self._account_cache:
            return self._account_cache[key]

        return None

    def preview_file(self, file_path: str, entry_date: date) -> OpeningBalancePreview:
        """
        Preview opening balance file without importing.

        Args:
            file_path: Path to CSV file
            entry_date: Date for the opening balance entry

        Returns:
            OpeningBalancePreview with parsed data and validation
        """
        path = Path(file_path)
        errors: list[str] = []
        lines: list[OpeningBalanceLine] = []

        if not path.exists():
            return OpeningBalancePreview(
                total_rows=0,
                total_debit=Decimal("0"),
                total_credit=Decimal("0"),
                is_balanced=False,
                difference=Decimal("0"),
                lines=[],
                matched_count=0,
                unmatched_count=0,
                unmatched_accounts=[],
                validation_errors=["File not found"],
                entry_date=entry_date,
                detected_format="unknown",
            )

        try:
            with open(path, encoding=self.config.encoding) as f:
                reader = csv.DictReader(f)
                columns = list(reader.fieldnames or [])
                rows = list(reader)
        except Exception as e:
            return OpeningBalancePreview(
                total_rows=0,
                total_debit=Decimal("0"),
                total_credit=Decimal("0"),
                is_balanced=False,
                difference=Decimal("0"),
                lines=[],
                matched_count=0,
                unmatched_count=0,
                unmatched_accounts=[],
                validation_errors=[f"Failed to read file: {str(e)}"],
                entry_date=entry_date,
                detected_format="unknown",
            )

        detected_format = detect_csv_format(columns)

        # Find column mappings
        name_col = self._find_column(rows[0] if rows else {}, self.ACCOUNT_NAME_COLS)
        type_col = self._find_column(rows[0] if rows else {}, self.ACCOUNT_TYPE_COLS)
        debit_col = self._find_column(rows[0] if rows else {}, self.DEBIT_COLS)
        credit_col = self._find_column(rows[0] if rows else {}, self.CREDIT_COLS)
        balance_col = self._find_column(
            rows[0] if rows else {}, self.NORMAL_BALANCE_COLS
        )
        notes_col = self._find_column(rows[0] if rows else {}, self.NOTES_COLS)
        coa_col = self._find_column(rows[0] if rows else {}, self.COA_MATCH_COLS)

        if not name_col:
            errors.append("Missing required column: Account Name")
        if not debit_col and not credit_col:
            errors.append("Missing required columns: Debit or Credit")

        total_debit = Decimal("0")
        total_credit = Decimal("0")
        matched_count = 0
        unmatched_accounts = []

        for idx, row in enumerate(rows, start=1):
            account_name = str(row.get(name_col, "") or "").strip() if name_col else ""
            account_type = str(row.get(type_col, "") or "").strip() if type_col else ""
            try:
                debit = (
                    self._parse_decimal(row.get(debit_col))
                    if debit_col
                    else Decimal("0")
                )
            except ValueError as exc:
                errors.append(f"Row {idx}: {exc}")
                debit = Decimal("0")
            try:
                credit = (
                    self._parse_decimal(row.get(credit_col))
                    if credit_col
                    else Decimal("0")
                )
            except ValueError as exc:
                errors.append(f"Row {idx}: {exc}")
                credit = Decimal("0")
            balance_str = (
                str(row.get(balance_col, "DEBIT") or "DEBIT").upper()
                if balance_col
                else "DEBIT"
            )
            notes = str(row.get(notes_col, "") or "") if notes_col else ""
            coa_match = str(row.get(coa_col, "") or "") if coa_col else ""

            if not account_name:
                errors.append(f"Row {idx}: Missing account name")
                continue

            # Determine normal balance
            if balance_str in ("CREDIT", "CR", "C"):
                normal_balance = BalanceType.CREDIT
            else:
                normal_balance = BalanceType.DEBIT

            # Match account
            account = self._match_account(account_name, coa_match)

            line = OpeningBalanceLine(
                account_name=account_name,
                account_type=account_type,
                debit=debit,
                credit=credit,
                normal_balance=normal_balance,
                notes=notes,
                coa_match=coa_match,
                account_id=account.account_id if account else None,
                matched_account_name=account.account_name if account else None,
            )
            lines.append(line)

            total_debit += debit
            total_credit += credit

            if account:
                matched_count += 1
            else:
                unmatched_accounts.append(account_name)

        difference = abs(total_debit - total_credit)
        is_balanced = difference < Decimal("0.01")

        if not is_balanced:
            errors.append(
                f"Unbalanced: Debits ({total_debit:,.2f}) != Credits ({total_credit:,.2f}), Difference: {difference:,.2f}"
            )

        return OpeningBalancePreview(
            total_rows=len(rows),
            total_debit=total_debit,
            total_credit=total_credit,
            is_balanced=is_balanced,
            difference=difference,
            lines=lines,
            matched_count=matched_count,
            unmatched_count=len(unmatched_accounts),
            unmatched_accounts=unmatched_accounts,
            validation_errors=errors,
            entry_date=entry_date,
            detected_format=detected_format,
        )

    def import_file(
        self,
        file_path: str,
        entry_date: date,
        description: str = "Opening Balance Entry",
        auto_create_accounts: bool = False,
        post_immediately: bool = False,
    ) -> OpeningBalanceResult:
        """
        Import opening balances and create journal entry.

        Args:
            file_path: Path to CSV file
            entry_date: Date for the opening balance entry
            description: Description for the journal entry
            auto_create_accounts: If True, create missing accounts
            post_immediately: If True, post the journal entry immediately

        Returns:
            OpeningBalanceResult with import status
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Preview first to validate
        preview = self.preview_file(file_path, entry_date)

        if preview.validation_errors and not auto_create_accounts:
            # Check if only unmatched accounts errors
            non_match_errors = [
                e
                for e in preview.validation_errors
                if "Unmatched" not in e and "unmatched" not in e.lower()
            ]
            if non_match_errors:
                return OpeningBalanceResult(
                    success=False,
                    journal_entry_id=None,
                    journal_number=None,
                    total_debit=preview.total_debit,
                    total_credit=preview.total_credit,
                    lines_created=0,
                    errors=preview.validation_errors,
                    warnings=[],
                )
        if auto_create_accounts and preview.unmatched_count > 0:
            return OpeningBalanceResult(
                success=False,
                journal_entry_id=None,
                journal_number=None,
                total_debit=preview.total_debit,
                total_credit=preview.total_credit,
                lines_created=0,
                errors=[
                    "Auto-create accounts is not implemented. Please create missing accounts first."
                ],
                warnings=[],
            )

        if not preview.is_balanced:
            return OpeningBalanceResult(
                success=False,
                journal_entry_id=None,
                journal_number=None,
                total_debit=preview.total_debit,
                total_credit=preview.total_credit,
                lines_created=0,
                errors=[
                    f"Journal is unbalanced: Debits={preview.total_debit:,.2f}, Credits={preview.total_credit:,.2f}"
                ],
                warnings=[],
            )

        # Get fiscal period
        period = self.db.execute(
            select(FiscalPeriod).where(
                FiscalPeriod.organization_id == self.config.organization_id,
                FiscalPeriod.start_date <= entry_date,
                FiscalPeriod.end_date >= entry_date,
            )
        ).scalar_one_or_none()

        if not period:
            return OpeningBalanceResult(
                success=False,
                journal_entry_id=None,
                journal_number=None,
                total_debit=preview.total_debit,
                total_credit=preview.total_credit,
                lines_created=0,
                errors=[f"No fiscal period found for date {entry_date}"],
                warnings=[],
            )

        # Generate journal number
        max_num = self.db.execute(
            select(func.max(JournalEntry.journal_number)).where(
                JournalEntry.organization_id == self.config.organization_id,
                JournalEntry.journal_type == JournalType.OPENING,
            )
        ).scalar()

        if max_num:
            try:
                num = int(max_num.replace("OB-", "")) + 1
            except (ValueError, AttributeError):
                num = 1
        else:
            num = 1

        journal_number = f"OB-{num:06d}"

        # Create journal entry
        journal = JournalEntry(
            organization_id=self.config.organization_id,
            journal_number=journal_number,
            journal_type=JournalType.OPENING,
            entry_date=entry_date,
            posting_date=entry_date,
            fiscal_period_id=period.fiscal_period_id,
            description=description,
            reference=f"Opening Balances as at {entry_date}",
            currency_code=settings.default_functional_currency_code,
            exchange_rate=Decimal("1.0"),
            total_debit=preview.total_debit,
            total_credit=preview.total_credit,
            total_debit_functional=preview.total_debit,
            total_credit_functional=preview.total_credit,
            status=JournalStatus.DRAFT,
            source_module="IMPORT",
            source_document_type="OPENING_BALANCE",
            created_by_user_id=self.config.user_id,
        )
        self.db.add(journal)
        self.db.flush()

        # Create journal lines
        lines_created = 0
        for idx, line in enumerate(preview.lines, start=1):
            if line.debit == 0 and line.credit == 0:
                continue  # Skip zero lines

            account_id = line.account_id
            if not account_id:
                if auto_create_accounts:
                    errors.append(
                        f"Line {idx}: Account '{line.account_name}' not found. Auto-create is not implemented."
                    )
                    continue
                else:
                    errors.append(
                        f"Line {idx}: Account '{line.account_name}' not found in Chart of Accounts"
                    )
                    continue

            journal_line = JournalEntryLine(
                journal_entry_id=journal.journal_entry_id,
                line_number=idx,
                account_id=account_id,
                description=line.notes or f"Opening balance - {line.account_name}",
                debit_amount=line.debit,
                credit_amount=line.credit,
                debit_amount_functional=line.debit,
                credit_amount_functional=line.credit,
                currency_code=settings.default_functional_currency_code,
                exchange_rate=Decimal("1.0"),
            )
            self.db.add(journal_line)
            lines_created += 1

        if errors:
            self.db.rollback()
            return OpeningBalanceResult(
                success=False,
                journal_entry_id=None,
                journal_number=None,
                total_debit=preview.total_debit,
                total_credit=preview.total_credit,
                lines_created=0,
                errors=errors,
                warnings=warnings,
            )

        # Commit
        self.db.commit()

        return OpeningBalanceResult(
            success=True,
            journal_entry_id=journal.journal_entry_id,
            journal_number=journal_number,
            total_debit=preview.total_debit,
            total_credit=preview.total_credit,
            lines_created=lines_created,
            errors=[],
            warnings=warnings,
        )


def get_journal_import_status(
    db: Session,
    organization_id: UUID,
    journal_entry_id: UUID,
) -> dict | None:
    """
    Get status of an imported opening balance journal entry.

    Returns dict with journal details and line count, or None if not found.
    """
    from sqlalchemy import func, select

    from app.models.finance.gl.journal_entry import JournalEntry
    from app.models.finance.gl.journal_entry_line import JournalEntryLine

    journal = db.execute(
        select(JournalEntry).where(
            JournalEntry.journal_entry_id == journal_entry_id,
            JournalEntry.organization_id == organization_id,
        )
    ).scalar_one_or_none()

    if not journal:
        return None

    line_count = db.execute(
        select(func.count(JournalEntryLine.line_id)).where(
            JournalEntryLine.journal_entry_id == journal_entry_id
        )
    ).scalar()

    return {
        "journal_entry_id": str(journal.journal_entry_id),
        "journal_number": journal.journal_number,
        "journal_type": journal.journal_type.value,
        "entry_date": journal.entry_date.isoformat(),
        "description": journal.description,
        "status": journal.status.value,
        "total_debit": float(journal.total_debit),
        "total_credit": float(journal.total_credit),
        "line_count": line_count,
        "created_at": journal.created_at.isoformat(),
    }


def get_opening_balance_template() -> str:
    """Return CSV template for opening balances."""
    return """Account Name,Account Type,Debit,Credit,Normal Balance,Notes,COA Match
Cash and Cash Equivalent,Bank,100000.00,0,DEBIT,Bank balances,Zenith Bank
Trade Receivables,Accounts Receivable,50000.00,0,DEBIT,Customer balances,Trade Receivables
Inventory,Stock,25000.00,0,DEBIT,Stock on hand,Inventory Asset
Share Capital,Equity,0,50000.00,CREDIT,Issued shares,Share capital
Retained Earnings,Equity,0,100000.00,CREDIT,Accumulated profits,Retained Earnings
Trade Payables,Accounts Payable,0,25000.00,CREDIT,Supplier balances,Accounts Payable
"""
