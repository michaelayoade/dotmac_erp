"""
Expenses Importer.

Imports expense entries from CSV data into the expense system.
"""

from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.exp.expense_entry import ExpenseEntry, ExpenseStatus, PaymentMethod
from app.models.finance.gl.account import Account

from .base import BaseImporter, FieldMapping, ImportConfig


class ExpenseImporter(BaseImporter[ExpenseEntry]):
    """
    Importer for expense entries from CSV data.

    Expected CSV columns (flexible - maps common naming conventions):
    - Expense Number / Entry Number / Reference: Expense reference
    - Expense Date / Date: Date of expense
    - Description / Expense Description: Description
    - Expense Account / Account / Category: Account for expense
    - Payment Account / Paid Through / Bank: Payment account
    - Amount: Expense amount
    - Tax Amount / VAT / Tax: Tax amount
    - Currency Code / Currency: Currency (default: NGN)
    - Payment Method / Method: CASH, BANK_TRANSFER, etc.
    - Payee / Vendor / Supplier: Who was paid
    - Receipt Reference / Receipt / Reference #: Receipt reference
    - Status: DRAFT, POSTED, APPROVED, etc.
    - Project / Project Name: Associated project
    - Notes: Additional notes
    """

    entity_name = "Expense"
    model_class = ExpenseEntry

    def __init__(
        self,
        db: Session,
        config: ImportConfig,
        default_expense_account_id: UUID,
        default_payment_account_id: Optional[UUID] = None,
    ):
        super().__init__(db, config)
        self.default_expense_account_id = default_expense_account_id
        self.default_payment_account_id = default_payment_account_id
        self._account_cache: Dict[str, UUID] = {}
        self._expense_counter = 0

    def get_field_mappings(self) -> List[FieldMapping]:
        """Define flexible field mappings supporting various CSV formats."""
        return [
            # Reference
            FieldMapping("Expense Number", "expense_number", required=False),
            FieldMapping("Entry Number", "entry_number_alt", required=False),
            FieldMapping("Reference", "reference_alt", required=False),
            # Date
            FieldMapping("Expense Date", "expense_date", required=False,
                         transformer=self.parse_date),
            FieldMapping("Date", "date_alt", required=False,
                         transformer=self.parse_date),
            # Description
            FieldMapping("Description", "description", required=False),
            FieldMapping("Expense Description", "description_alt", required=False),
            # Accounts
            FieldMapping("Expense Account", "expense_account_name", required=False),
            FieldMapping("Account", "account_alt", required=False),
            FieldMapping("Category", "category_alt", required=False),
            FieldMapping("Expense Account Code", "expense_account_code", required=False),
            FieldMapping("Payment Account", "payment_account_name", required=False),
            FieldMapping("Paid Through", "paid_through_alt", required=False),
            FieldMapping("Bank", "bank_alt", required=False),
            FieldMapping("Payment Account Code", "payment_account_code", required=False),
            # Amounts
            FieldMapping("Amount", "amount", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Expense Amount", "expense_amount_alt", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Total", "total_alt", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Tax Amount", "tax_amount", required=False,
                         transformer=self.parse_decimal, default=Decimal("0")),
            FieldMapping("VAT", "vat_alt", required=False,
                         transformer=self.parse_decimal),
            FieldMapping("Tax", "tax_alt", required=False,
                         transformer=self.parse_decimal),
            # Currency
            FieldMapping("Currency Code", "currency_code", required=False, default="NGN"),
            FieldMapping("Currency", "currency_alt", required=False),
            # Payment
            FieldMapping("Payment Method", "payment_method_str", required=False),
            FieldMapping("Method", "method_alt", required=False),
            FieldMapping("Payee", "payee", required=False),
            FieldMapping("Vendor", "vendor_alt", required=False),
            FieldMapping("Supplier", "supplier_alt", required=False),
            FieldMapping("Receipt Reference", "receipt_reference", required=False),
            FieldMapping("Receipt", "receipt_alt", required=False),
            FieldMapping("Reference#", "reference_hash_alt", required=False),
            # Status
            FieldMapping("Status", "status_str", required=False, default="DRAFT"),
            # Project
            FieldMapping("Project", "project_name", required=False),
            FieldMapping("Project Name", "project_name_alt", required=False),
            # Notes
            FieldMapping("Notes", "notes", required=False),
        ]

    def get_unique_key(self, row: Dict[str, Any]) -> str:
        """Unique key is expense number."""
        return (row.get("Expense Number") or row.get("Entry Number") or
                row.get("Reference") or "").strip()

    def check_duplicate(self, row: Dict[str, Any]) -> Optional[ExpenseEntry]:
        """Check if expense already exists."""
        expense_number = self.get_unique_key(row)
        if not expense_number:
            return None

        existing = self.db.execute(
            select(ExpenseEntry).where(
                ExpenseEntry.organization_id == self.config.organization_id,
                ExpenseEntry.expense_number == expense_number,
            )
        ).scalar_one_or_none()

        return existing

    def validate_row(self, row: Dict[str, Any], row_num: int) -> bool:
        """Validate row data."""
        is_valid = super().validate_row(row, row_num)

        # Amount is required
        amount = (row.get("Amount") or row.get("Expense Amount") or
                  row.get("Total") or "").strip() if isinstance(
            row.get("Amount") or row.get("Expense Amount") or row.get("Total"), str
        ) else row.get("Amount") or row.get("Expense Amount") or row.get("Total")

        if not amount:
            self.result.add_error(row_num, "Amount is required", "Amount")
            is_valid = False

        return is_valid

    def create_entity(self, row: Dict[str, Any]) -> ExpenseEntry:
        """Create a new expense entry from transformed row data."""
        # Get expense number
        expense_number = (row.get("expense_number") or row.get("entry_number_alt") or
                          row.get("reference_alt") or "").strip()
        if not expense_number:
            self._expense_counter += 1
            expense_number = f"EXP{self._expense_counter:06d}"

        # Get date
        expense_date = row.get("expense_date") or row.get("date_alt") or date.today()

        # Get description
        description = (row.get("description") or row.get("description_alt") or
                       "Expense Entry")

        # Get expense account
        expense_account_id = self._get_account_id(
            row.get("expense_account_name") or row.get("account_alt") or row.get("category_alt"),
            row.get("expense_account_code"),
            self.default_expense_account_id
        )

        # Get payment account
        payment_account_id = None
        if row.get("payment_account_name") or row.get("paid_through_alt") or row.get("bank_alt"):
            payment_account_id = self._get_account_id(
                row.get("payment_account_name") or row.get("paid_through_alt") or row.get("bank_alt"),
                row.get("payment_account_code"),
                self.default_payment_account_id
            )
        elif self.default_payment_account_id:
            payment_account_id = self.default_payment_account_id

        # Get amounts
        amount = (row.get("amount") or row.get("expense_amount_alt") or
                  row.get("total_alt") or Decimal("0"))
        tax_amount = (row.get("tax_amount") or row.get("vat_alt") or
                      row.get("tax_alt") or Decimal("0"))

        # Get currency
        currency_code = (row.get("currency_code") or row.get("currency_alt") or "NGN")[:3]

        # Get payment method
        method_str = row.get("payment_method_str") or row.get("method_alt") or "CASH"
        payment_method = self._parse_payment_method(method_str)

        # Get payee
        payee = (row.get("payee") or row.get("vendor_alt") or row.get("supplier_alt"))

        # Get receipt reference
        receipt_reference = (row.get("receipt_reference") or row.get("receipt_alt") or
                             row.get("reference_hash_alt"))

        # Get status
        status_str = row.get("status_str", "DRAFT")
        status = self._parse_status(status_str)

        expense = ExpenseEntry(
            expense_id=uuid4(),
            organization_id=self.config.organization_id,
            expense_number=expense_number[:30],
            description=description[:500],
            notes=row.get("notes"),
            expense_date=expense_date,
            expense_account_id=expense_account_id,
            payment_account_id=payment_account_id,
            amount=amount,
            currency_code=currency_code,
            tax_amount=tax_amount,
            payment_method=payment_method,
            payee=payee[:200] if payee else None,
            receipt_reference=receipt_reference[:100] if receipt_reference else None,
            status=status,
            created_by=self.config.user_id,
        )

        return expense

    def _get_account_id(
        self,
        account_name: Optional[str],
        account_code: Optional[str],
        default_id: Optional[UUID]
    ) -> UUID:
        """Get account ID by name or code."""
        if account_code:
            cache_key = f"code:{account_code}"
            if cache_key in self._account_cache:
                return self._account_cache[cache_key]

            account = self.db.execute(
                select(Account).where(
                    Account.organization_id == self.config.organization_id,
                    Account.account_code == account_code,
                )
            ).scalar_one_or_none()

            if account:
                self._account_cache[cache_key] = account.account_id
                return account.account_id

        if account_name:
            cache_key = f"name:{account_name}"
            if cache_key in self._account_cache:
                return self._account_cache[cache_key]

            account = self.db.execute(
                select(Account).where(
                    Account.organization_id == self.config.organization_id,
                    Account.account_name == account_name,
                )
            ).scalar_one_or_none()

            if account:
                self._account_cache[cache_key] = account.account_id
                return account.account_id

        if default_id:
            return default_id

        raise ValueError(
            f"Account '{account_name or account_code}' not found and no default provided."
        )

    def _parse_payment_method(self, method_str: str) -> PaymentMethod:
        """Parse payment method string."""
        method_map = {
            "CASH": PaymentMethod.CASH,
            "PETTY_CASH": PaymentMethod.PETTY_CASH,
            "PETTY CASH": PaymentMethod.PETTY_CASH,
            "CORPORATE_CARD": PaymentMethod.CORPORATE_CARD,
            "CORPORATE CARD": PaymentMethod.CORPORATE_CARD,
            "CARD": PaymentMethod.CORPORATE_CARD,
            "PERSONAL_CARD": PaymentMethod.PERSONAL_CARD,
            "PERSONAL CARD": PaymentMethod.PERSONAL_CARD,
            "BANK_TRANSFER": PaymentMethod.BANK_TRANSFER,
            "BANK TRANSFER": PaymentMethod.BANK_TRANSFER,
            "TRANSFER": PaymentMethod.BANK_TRANSFER,
            "BANK": PaymentMethod.BANK_TRANSFER,
            "OTHER": PaymentMethod.OTHER,
        }
        return method_map.get(method_str.upper().replace("-", "_"), PaymentMethod.CASH)

    def _parse_status(self, status_str: str) -> ExpenseStatus:
        """Parse expense status string."""
        status_map = {
            "DRAFT": ExpenseStatus.DRAFT,
            "SUBMITTED": ExpenseStatus.SUBMITTED,
            "APPROVED": ExpenseStatus.APPROVED,
            "POSTED": ExpenseStatus.POSTED,
            "REJECTED": ExpenseStatus.REJECTED,
            "VOID": ExpenseStatus.VOID,
            "CANCELLED": ExpenseStatus.VOID,
        }
        return status_map.get(status_str.upper(), ExpenseStatus.DRAFT)
