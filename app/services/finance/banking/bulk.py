"""
Bank Statement Bulk Action Service.

Provides bulk operations for bank statement batches.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.banking.bank_statement import (
    BankStatement,
    BankStatementStatus,
)
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
