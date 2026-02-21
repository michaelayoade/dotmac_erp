"""
Banking Bulk Action Services.

Provides bulk operations for bank statements, accounts, and payees.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementStatus,
)
from app.models.finance.banking.payee import Payee
from app.services.bulk_actions import BulkActionService

logger = logging.getLogger(__name__)


class StatementBulkService(BulkActionService[BankStatement]):
    """
    Bulk operations for bank statements.

    Supported actions:
    - delete: Remove statement batches (if not reconciled/closed)
    - export: Export to CSV
    """

    model = BankStatement
    id_field = "statement_id"
    org_field = "organization_id"
    search_fields = ["statement_number"]

    export_fields = [
        ("statement_number", "Statement #"),
        ("statement_date", "Date"),
        ("period_start", "Period Start"),
        ("period_end", "Period End"),
        ("opening_balance", "Opening Balance"),
        ("closing_balance", "Closing Balance"),
        ("total_credits", "Total Credits"),
        ("total_debits", "Total Debits"),
        ("total_lines", "Lines"),
        ("matched_lines", "Matched"),
        ("unmatched_lines", "Unmatched"),
        ("currency_code", "Currency"),
        ("status", "Status"),
        ("import_filename", "Import File"),
    ]

    def can_delete(self, entity: BankStatement) -> tuple[bool, str]:
        """A statement cannot be deleted if reconciled or closed."""
        if entity.status in (
            BankStatementStatus.reconciled,
            BankStatementStatus.closed,
        ):
            return (
                False,
                f"Cannot delete '{entity.statement_number}': status is {entity.status.value}",
            )
        return (True, "")

    def _get_export_value(self, entity: BankStatement, field_name: str) -> str:
        """Handle special field formatting for statement export."""
        if field_name == "status":
            return entity.status.value if entity.status else ""
        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get statement export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"bank_statements_export_{timestamp}.csv"


def get_statement_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> StatementBulkService:
    """Factory function to create a StatementBulkService instance."""
    return StatementBulkService(db, organization_id, user_id)


class AccountBulkService(BulkActionService["BankAccount"]):
    """Bulk operations for bank accounts (export only)."""

    model = BankAccount
    id_field = "bank_account_id"
    org_field = "organization_id"
    search_fields = ["bank_name", "account_name", "account_number"]

    export_fields = [
        ("bank_name", "Bank"),
        ("account_name", "Account Name"),
        ("account_number", "Account Number"),
        ("account_type", "Type"),
        ("currency_code", "Currency"),
        ("status", "Status"),
        ("last_statement_balance", "Last Statement Balance"),
        ("last_statement_date", "Last Statement Date"),
        ("last_reconciled_date", "Last Reconciled Date"),
        ("iban", "IBAN"),
        ("bank_code", "Bank Code"),
        ("branch_name", "Branch"),
    ]

    def can_delete(self, entity: BankAccount) -> tuple[bool, str]:
        """Accounts cannot be bulk deleted."""
        return (False, "Bank accounts cannot be bulk deleted")

    def _get_export_value(self, entity: BankAccount, field_name: str) -> str:
        """Handle special field formatting for account export."""
        if field_name == "status":
            return entity.status.value if entity.status else ""
        if field_name == "account_type":
            return entity.account_type.value if entity.account_type else ""
        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get account export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"bank_accounts_export_{timestamp}.csv"


def get_account_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> AccountBulkService:
    """Factory function to create an AccountBulkService instance."""
    return AccountBulkService(db, organization_id, user_id)


class PayeeBulkService(BulkActionService["Payee"]):
    """Bulk operations for payees (export only)."""

    model = Payee
    id_field = "payee_id"
    org_field = "organization_id"
    search_fields = ["payee_name"]

    export_fields = [
        ("payee_name", "Payee Name"),
        ("payee_type", "Type"),
        ("name_patterns", "Name Patterns"),
        ("match_count", "Match Count"),
        ("is_active", "Active"),
        ("notes", "Notes"),
    ]

    def can_delete(self, entity: Payee) -> tuple[bool, str]:
        """Payees cannot be bulk deleted."""
        return (False, "Payees cannot be bulk deleted")

    def _get_export_value(self, entity: Payee, field_name: str) -> str:
        """Handle special field formatting for payee export."""
        if field_name == "payee_type":
            return entity.payee_type.value if entity.payee_type else ""
        if field_name == "is_active":
            return "Yes" if entity.is_active else "No"
        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get payee export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"payees_export_{timestamp}.csv"


def get_payee_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> PayeeBulkService:
    """Factory function to create a PayeeBulkService instance."""
    return PayeeBulkService(db, organization_id, user_id)
