"""
GL Account Bulk Action Service.

Provides bulk operations for chart of accounts.
Note: Accounts cannot be deleted if they have journal entries.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account
from app.models.finance.gl.journal_line import JournalLine
from app.services.bulk_actions import BulkActionService


class AccountBulkService(BulkActionService[Account]):
    """
    Bulk operations for GL accounts.

    Supported actions:
    - activate: Set is_active=True
    - deactivate: Set is_active=False
    - export: Export to CSV

    Note: Delete is restricted - accounts with journal entries cannot be deleted.
    """

    model = Account
    id_field = "account_id"
    org_field = "organization_id"

    # Fields to export in CSV
    export_fields = [
        ("account_code", "Account Code"),
        ("account_name", "Account Name"),
        ("account_type", "Account Type"),
        ("account_category", "Category"),
        ("is_control_account", "Control Account"),
        ("currency_code", "Currency"),
        ("is_active", "Active"),
        ("description", "Description"),
    ]

    def can_delete(self, entity: Account) -> tuple[bool, str]:
        """
        Check if an account can be deleted.

        An account cannot be deleted if it has journal entries or is a system account.
        """
        # Check if this is a system/control account
        if entity.is_control_account:
            return (
                False,
                f"Cannot delete '{entity.account_name}': is a control account",
            )

        # Check for journal lines
        journal_count = (
            self.db.query(JournalLine)
            .filter(JournalLine.account_id == entity.account_id)
            .count()
        )

        if journal_count > 0:
            return (
                False,
                f"Cannot delete '{entity.account_name}': has {journal_count} journal entries",
            )

        return (True, "")

    def _get_export_value(self, entity: Account, field_name: str) -> str:
        """Handle special field formatting for account export."""
        if field_name == "account_type":
            return entity.account_type.value if entity.account_type else ""
        if field_name == "account_category":
            return entity.account_category.value if entity.account_category else ""

        return super()._get_export_value(entity, field_name)

    def _get_export_filename(self) -> str:
        """Get account export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"accounts_export_{timestamp}.csv"


def get_account_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> AccountBulkService:
    """Factory function to create an AccountBulkService instance."""
    return AccountBulkService(db, organization_id, user_id)
