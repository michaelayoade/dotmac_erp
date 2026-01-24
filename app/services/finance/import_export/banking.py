"""
Bank Accounts Importer.

Imports bank accounts from CSV data into the banking system.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.banking.bank_account import BankAccount, BankAccountType, BankAccountStatus
from app.models.finance.gl.account import Account

from .base import BaseImporter, FieldMapping, ImportConfig


class BankAccountImporter(BaseImporter[BankAccount]):
    """
    Importer for bank accounts from CSV data.

    Expected CSV columns (flexible - maps common naming conventions):
    - Bank Name / Bank: Name of the bank
    - Account Name / Name: Account display name
    - Account Number / Account No: Bank account number
    - Account Type / Type: checking, savings, money_market, credit_line, loan, other
    - Bank Code / SWIFT / BIC: Bank identifier code
    - Branch Code: Branch identifier
    - Branch Name / Branch: Branch name
    - IBAN: International bank account number
    - Currency Code / Currency: Currency (default: NGN)
    - Opening Balance / Balance: Initial balance
    - Contact Name: Bank contact name
    - Contact Phone: Bank contact phone
    - Contact Email: Bank contact email
    - Notes / Description: Additional notes
    - Is Primary / Primary: Whether this is primary account
    - Allow Overdraft: Whether overdraft is allowed
    - Overdraft Limit: Overdraft limit amount
    - Status: active, inactive, closed, suspended
    """

    entity_name = "Bank Account"
    model_class = BankAccount

    def __init__(
        self,
        db: Session,
        config: ImportConfig,
        default_gl_account_id: Optional[UUID] = None,
    ):
        super().__init__(db, config)
        self.default_gl_account_id = default_gl_account_id
        self._gl_account_cache: Dict[str, UUID] = {}
        self._code_counter = 0

    def get_field_mappings(self) -> List[FieldMapping]:
        """Define flexible field mappings supporting various CSV formats."""
        return [
            # Bank details
            FieldMapping("Bank Name", "bank_name", required=False),
            FieldMapping("Bank", "bank_alt", required=False),
            FieldMapping("Bank Code", "bank_code", required=False),
            FieldMapping("SWIFT", "swift_code", required=False),
            FieldMapping("BIC", "bic_code", required=False),
            FieldMapping("Branch Code", "branch_code", required=False),
            FieldMapping("Branch Name", "branch_name", required=False),
            FieldMapping("Branch", "branch_alt", required=False),
            # Account details
            FieldMapping("Account Name", "account_name", required=False),
            FieldMapping("Name", "name_alt", required=False),
            FieldMapping("Account Number", "account_number", required=False),
            FieldMapping("Account No", "account_no_alt", required=False),
            FieldMapping("Account Type", "account_type_str", required=False),
            FieldMapping("Type", "type_alt", required=False),
            FieldMapping("IBAN", "iban", required=False),
            # Currency
            FieldMapping("Currency Code", "currency_code", required=False, default="NGN"),
            FieldMapping("Currency", "currency_alt", required=False),
            # Balance
            FieldMapping("Opening Balance", "opening_balance", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Balance", "balance_alt", required=False,
                         transformer=self.parse_decimal),
            # GL Account link
            FieldMapping("GL Account", "gl_account_code", required=False),
            FieldMapping("GL Account Code", "gl_account_code_alt", required=False),
            # Contact
            FieldMapping("Contact Name", "contact_name", required=False),
            FieldMapping("Contact Phone", "contact_phone", required=False),
            FieldMapping("Contact Email", "contact_email", required=False),
            # Notes
            FieldMapping("Notes", "notes", required=False),
            FieldMapping("Description", "description_alt", required=False),
            # Flags
            FieldMapping("Is Primary", "is_primary", required=False,
                         transformer=self.parse_boolean, default=False),
            FieldMapping("Primary", "primary_alt", required=False,
                         transformer=self.parse_boolean),
            FieldMapping("Allow Overdraft", "allow_overdraft", required=False,
                         transformer=self.parse_boolean, default=False),
            FieldMapping("Overdraft Limit", "overdraft_limit", required=False,
                         transformer=self.parse_decimal),
            # Status
            FieldMapping("Status", "status_str", required=False, default="active"),
        ]

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        """Unique key is account number + bank code."""
        account_number = (row.get("Account Number") or row.get("Account No") or "").strip()
        bank_code = (row.get("Bank Code") or row.get("SWIFT") or
                     row.get("BIC") or "").strip()
        return f"{account_number}:{bank_code}"

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[BankAccount]:
        """Check if bank account already exists."""
        account_number = (row.get("Account Number") or row.get("Account No") or "").strip()
        bank_code = (row.get("Bank Code") or row.get("SWIFT") or row.get("BIC") or "").strip()

        if not account_number:
            return None

        existing = self.db.execute(
            select(BankAccount).where(
                BankAccount.organization_id == self.config.organization_id,
                BankAccount.account_number == account_number,
                BankAccount.bank_code == bank_code if bank_code else True,
            )
        ).scalar_one_or_none()

        return existing

    def validate_row(self, row: Dict[str, Any], row_num: int) -> bool:
        """Validate row data."""
        is_valid = super().validate_row(row, row_num)

        # Account number is required
        account_number = (row.get("Account Number") or row.get("Account No") or "").strip()
        if not account_number:
            self.result.add_error(row_num, "Account number is required", "Account Number")
            is_valid = False

        # Bank name is required
        bank_name = (row.get("Bank Name") or row.get("Bank") or "").strip()
        if not bank_name:
            self.result.add_error(row_num, "Bank name is required", "Bank Name")
            is_valid = False

        return is_valid

    def create_entity(self, row: Dict[str, Any]) -> BankAccount:
        """Create a new bank account from transformed row data."""
        # Get bank details
        bank_name = (row.get("bank_name") or row.get("bank_alt") or "Unknown Bank").strip()
        bank_code = (row.get("bank_code") or row.get("swift_code") or
                     row.get("bic_code") or "").strip() or None
        branch_code = row.get("branch_code")
        branch_name = row.get("branch_name") or row.get("branch_alt")

        # Get account details
        account_number = (row.get("account_number") or row.get("account_no_alt") or "").strip()
        account_name = (row.get("account_name") or row.get("name_alt") or
                        f"{bank_name} - {account_number[-4:]}").strip()

        # Parse account type
        type_str = (row.get("account_type_str") or row.get("type_alt") or "checking")
        account_type = self._parse_account_type(type_str)

        # Get currency
        currency_code = (row.get("currency_code") or row.get("currency_alt") or "NGN")[:3]

        # Get or find GL account
        gl_account_id = self._get_gl_account_id(row, bank_name, currency_code)

        # Parse status
        status_str = row.get("status_str", "active")
        status = self._parse_status(status_str)

        # Get balance
        opening_balance = row.get("opening_balance") or row.get("balance_alt")

        # Get flags
        is_primary = row.get("is_primary") or row.get("primary_alt") or False
        allow_overdraft = row.get("allow_overdraft") or False

        bank_account = BankAccount(
            bank_account_id=uuid4(),
            organization_id=self.config.organization_id,
            bank_name=bank_name[:200],
            bank_code=bank_code[:20] if bank_code else None,
            branch_code=branch_code[:20] if branch_code else None,
            branch_name=branch_name[:200] if branch_name else None,
            account_number=account_number[:50],
            account_name=account_name[:200],
            account_type=account_type,
            iban=row.get("iban")[:50] if row.get("iban") else None,
            currency_code=currency_code,
            gl_account_id=gl_account_id,
            status=status,
            last_statement_balance=opening_balance,
            contact_name=row.get("contact_name")[:200] if row.get("contact_name") else None,
            contact_phone=row.get("contact_phone")[:50] if row.get("contact_phone") else None,
            contact_email=row.get("contact_email")[:200] if row.get("contact_email") else None,
            notes=row.get("notes") or row.get("description_alt"),
            is_primary=is_primary,
            allow_overdraft=allow_overdraft,
            overdraft_limit=row.get("overdraft_limit"),
            created_by=self.config.user_id,
            updated_by=self.config.user_id,
        )

        return bank_account

    def _parse_account_type(self, type_str: str) -> BankAccountType:
        """Parse account type string."""
        type_map = {
            "CHECKING": BankAccountType.checking,
            "CURRENT": BankAccountType.checking,
            "SAVINGS": BankAccountType.savings,
            "MONEY_MARKET": BankAccountType.money_market,
            "MONEY MARKET": BankAccountType.money_market,
            "CREDIT_LINE": BankAccountType.credit_line,
            "CREDIT LINE": BankAccountType.credit_line,
            "CREDIT": BankAccountType.credit_line,
            "LOAN": BankAccountType.loan,
            "OTHER": BankAccountType.other,
        }
        return type_map.get(type_str.upper().replace("-", "_"), BankAccountType.checking)

    def _parse_status(self, status_str: str) -> BankAccountStatus:
        """Parse account status string."""
        status_map = {
            "ACTIVE": BankAccountStatus.active,
            "INACTIVE": BankAccountStatus.inactive,
            "CLOSED": BankAccountStatus.closed,
            "SUSPENDED": BankAccountStatus.suspended,
        }
        return status_map.get(status_str.upper(), BankAccountStatus.active)

    def _get_gl_account_id(self, row: Dict[str, Any], bank_name: str, currency: str) -> UUID:
        """Get or create GL account for this bank account."""
        # Try to find by GL account code
        gl_code = row.get("gl_account_code") or row.get("gl_account_code_alt")
        if gl_code:
            cache_key = gl_code
            if cache_key in self._gl_account_cache:
                return self._gl_account_cache[cache_key]

            account = self.db.execute(
                select(Account).where(
                    Account.organization_id == self.config.organization_id,
                    Account.account_code == gl_code,
                )
            ).scalar_one_or_none()

            if account:
                self._gl_account_cache[cache_key] = account.account_id
                return account.account_id

        # Try to find existing bank account with similar name
        cache_key = f"bank:{bank_name}"
        if cache_key in self._gl_account_cache:
            return self._gl_account_cache[cache_key]

        account = self.db.execute(
            select(Account).where(
                Account.organization_id == self.config.organization_id,
                Account.subledger_type == "BANK",
                Account.is_active == True,
            )
        ).first()

        if account:
            self._gl_account_cache[cache_key] = account[0].account_id
            return account[0].account_id

        # Use default if provided
        if self.default_gl_account_id:
            return self.default_gl_account_id

        # Raise error if no GL account found
        raise ValueError(
            f"No GL account found for bank '{bank_name}'. "
            "Please provide a GL account code or set up bank accounts in Chart of Accounts first."
        )
