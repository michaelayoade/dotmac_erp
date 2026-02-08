"""
Bank Upload Service.

Generates bank upload files for bulk payments.
Supports multiple bank formats (Zenith, Access, GTBank, etc.).
Reusable across payroll, AP bills, and any bulk payment scenarios.
"""

from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from sqlalchemy.orm import Session

from app.services.finance.banking.bank_directory import BankDirectoryService

logger = logging.getLogger(__name__)

BankFormat = Literal["zenith", "access", "gtbank", "generic"]


@dataclass
class PaymentItem:
    """
    Generic payment item for bank upload generation.

    Reusable across payroll, AP bills, and other bulk payments.
    """

    reference: str  # Transaction reference
    beneficiary_name: str  # Recipient name
    amount: Decimal  # Payment amount
    account_number: str  # Beneficiary account number
    bank_name: str  # Beneficiary bank name (will be resolved to code)
    bank_code: str | None = None  # Beneficiary bank code (if known)
    beneficiary_code: str | None = None  # Internal code (employee ID, supplier code)
    narration: str | None = None  # Payment narration/memo


@dataclass
class BankUploadResult:
    """Result of bank upload file generation."""

    content: bytes
    filename: str
    content_type: str
    row_count: int
    total_amount: Decimal
    errors: list[str]


class BankUploadService:
    """
    Service for generating bank upload files.

    Supports multiple bank formats and automatically resolves
    bank codes from bank names using the bank directory.
    """

    def __init__(self, db: Session):
        self.db = db
        self.bank_directory = BankDirectoryService(db)

    def generate_upload(
        self,
        items: list[PaymentItem],
        source_account_number: str,
        payment_date: date,
        bank_format: BankFormat = "zenith",
        batch_reference: str | None = None,
    ) -> BankUploadResult:
        """
        Generate bank upload file for bulk payments.

        Args:
            items: List of payment items
            source_account_number: Debit account number
            payment_date: Payment date
            bank_format: Target bank format
            batch_reference: Optional batch reference prefix

        Returns:
            BankUploadResult with file content and metadata
        """
        if bank_format == "zenith":
            return self._generate_zenith_format(
                items, source_account_number, payment_date, batch_reference
            )
        elif bank_format == "access":
            return self._generate_access_format(
                items, source_account_number, payment_date, batch_reference
            )
        elif bank_format == "gtbank":
            return self._generate_gtbank_format(
                items, source_account_number, payment_date, batch_reference
            )
        else:
            return self._generate_generic_format(
                items, source_account_number, payment_date, batch_reference
            )

    def _resolve_bank_code(self, item: PaymentItem) -> str:
        """
        Resolve bank code for a payment item.

        Uses provided bank_code if available, otherwise looks up from bank_name.
        Returns bank code as a string, zero-padded to 3 digits.
        """
        code = item.bank_code
        if not code:
            code = self.bank_directory.lookup_bank_code(item.bank_name)

        if not code:
            return ""

        # Ensure bank code is formatted as 3-digit string (e.g., "044", "057")
        code_str = str(code).strip()
        if code_str.isdigit():
            return code_str.zfill(3)
        return code_str

    def _format_account_number(self, account_number: str) -> str:
        """
        Format account number as 10-digit string with leading zeros preserved.

        Nigerian bank accounts are 10 digits (NUBAN format).
        """
        if not account_number:
            return ""

        # Remove any spaces or dashes
        cleaned = str(account_number).strip().replace(" ", "").replace("-", "")

        # If numeric, zero-pad to 10 digits
        if cleaned.isdigit():
            return cleaned.zfill(10)

        return cleaned

    def _generate_zenith_format(
        self,
        items: list[PaymentItem],
        source_account_number: str,
        payment_date: date,
        batch_reference: str | None = None,
    ) -> BankUploadResult:
        """
        Generate Zenith Bank upload format.

        Columns:
        - Transaction Ref
        - Beneficiary Name
        - Amount
        - Date (DD/MM/YYYY)
        - Beneficiary Code
        - Account Number
        - Sort Code (bank code)
        - Debit Account
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow(
            [
                "Transaction Ref",
                "Beneficiary Name",
                "Amount",
                "Date",
                "Beneficiary Code",
                "Account Number",
                "Sort Code",
                "Debit Account",
            ]
        )

        errors: list[str] = []
        total_amount = Decimal("0")
        row_count = 0
        date_str = payment_date.strftime("%d/%m/%Y")

        # Format source/debit account number
        formatted_source_account = self._format_account_number(source_account_number)

        for item in items:
            bank_code = self._resolve_bank_code(item)
            if not bank_code:
                errors.append(
                    f"Bank code not found for: {item.beneficiary_name} ({item.bank_name})"
                )

            account_number = self._format_account_number(item.account_number)

            writer.writerow(
                [
                    item.reference,
                    item.beneficiary_name,
                    str(item.amount),
                    date_str,
                    item.beneficiary_code or "",
                    account_number,
                    bank_code,
                    formatted_source_account,
                ]
            )
            total_amount += item.amount
            row_count += 1

        content = output.getvalue().encode("utf-8")
        filename = f"bank_upload_zenith_{payment_date.strftime('%Y%m%d')}.csv"

        return BankUploadResult(
            content=content,
            filename=filename,
            content_type="text/csv",
            row_count=row_count,
            total_amount=total_amount,
            errors=errors,
        )

    def _generate_access_format(
        self,
        items: list[PaymentItem],
        source_account_number: str,
        payment_date: date,
        batch_reference: str | None = None,
    ) -> BankUploadResult:
        """
        Generate Access Bank upload format.

        Similar to Zenith but with slightly different column order.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow(
            [
                "Serial No",
                "Beneficiary Account Number",
                "Beneficiary Bank Code",
                "Beneficiary Name",
                "Amount",
                "Narration",
            ]
        )

        errors: list[str] = []
        total_amount = Decimal("0")
        row_count = 0

        for idx, item in enumerate(items, start=1):
            bank_code = self._resolve_bank_code(item)
            if not bank_code:
                errors.append(
                    f"Bank code not found for: {item.beneficiary_name} ({item.bank_name})"
                )

            narration = item.narration or f"Payment to {item.beneficiary_name}"

            writer.writerow(
                [
                    idx,
                    self._format_account_number(item.account_number),
                    bank_code,
                    item.beneficiary_name,
                    str(item.amount),
                    narration,
                ]
            )
            total_amount += item.amount
            row_count += 1

        content = output.getvalue().encode("utf-8")
        filename = f"bank_upload_access_{payment_date.strftime('%Y%m%d')}.csv"

        return BankUploadResult(
            content=content,
            filename=filename,
            content_type="text/csv",
            row_count=row_count,
            total_amount=total_amount,
            errors=errors,
        )

    def _generate_gtbank_format(
        self,
        items: list[PaymentItem],
        source_account_number: str,
        payment_date: date,
        batch_reference: str | None = None,
    ) -> BankUploadResult:
        """
        Generate GTBank upload format.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow(
            [
                "Account Number",
                "Bank Code",
                "Amount",
                "Beneficiary Name",
                "Remarks",
            ]
        )

        errors: list[str] = []
        total_amount = Decimal("0")
        row_count = 0

        for item in items:
            bank_code = self._resolve_bank_code(item)
            if not bank_code:
                errors.append(
                    f"Bank code not found for: {item.beneficiary_name} ({item.bank_name})"
                )

            remarks = item.narration or item.reference

            writer.writerow(
                [
                    self._format_account_number(item.account_number),
                    bank_code,
                    str(item.amount),
                    item.beneficiary_name,
                    remarks,
                ]
            )
            total_amount += item.amount
            row_count += 1

        content = output.getvalue().encode("utf-8")
        filename = f"bank_upload_gtbank_{payment_date.strftime('%Y%m%d')}.csv"

        return BankUploadResult(
            content=content,
            filename=filename,
            content_type="text/csv",
            row_count=row_count,
            total_amount=total_amount,
            errors=errors,
        )

    def _generate_generic_format(
        self,
        items: list[PaymentItem],
        source_account_number: str,
        payment_date: date,
        batch_reference: str | None = None,
    ) -> BankUploadResult:
        """
        Generate generic bank upload format.

        A universal format that can be adapted for any bank.
        """
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow(
            [
                "Reference",
                "Beneficiary Name",
                "Account Number",
                "Bank Code",
                "Bank Name",
                "Amount",
                "Date",
                "Narration",
            ]
        )

        errors: list[str] = []
        total_amount = Decimal("0")
        row_count = 0
        date_str = payment_date.strftime("%Y-%m-%d")

        for item in items:
            bank_code = self._resolve_bank_code(item)
            if not bank_code:
                errors.append(
                    f"Bank code not found for: {item.beneficiary_name} ({item.bank_name})"
                )

            narration = item.narration or f"Payment - {item.reference}"

            writer.writerow(
                [
                    item.reference,
                    item.beneficiary_name,
                    self._format_account_number(item.account_number),
                    bank_code,
                    item.bank_name,
                    str(item.amount),
                    date_str,
                    narration,
                ]
            )
            total_amount += item.amount
            row_count += 1

        content = output.getvalue().encode("utf-8")
        filename = f"bank_upload_{payment_date.strftime('%Y%m%d')}.csv"

        return BankUploadResult(
            content=content,
            filename=filename,
            content_type="text/csv",
            row_count=row_count,
            total_amount=total_amount,
            errors=errors,
        )


def bank_upload_service(db: Session) -> BankUploadService:
    """Create a BankUploadService instance."""
    return BankUploadService(db)
