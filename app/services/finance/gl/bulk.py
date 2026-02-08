"""
GL Bulk Action Services.

Provides bulk operations for chart of accounts and journal entries.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import Response
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account, AccountType
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.schemas.bulk_actions import BulkActionResult
from app.services.bulk_actions import BulkActionService
from app.services.finance.gl.journal import JournalService

logger = logging.getLogger(__name__)


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
    search_fields = ["account_code", "account_name"]

    # Fields to export in CSV
    export_fields = [
        ("account_code", "Account Code"),
        ("account_name", "Account Name"),
        ("account_type", "Account Type"),
        ("category", "Category"),
        ("normal_balance", "Normal Balance"),
        ("subledger_type", "Subledger Type"),
        ("default_currency_code", "Currency"),
        ("is_active", "Active"),
        ("description", "Description"),
    ]

    def can_delete(self, entity: Account) -> tuple[bool, str]:
        """
        Check if an account can be deleted.

        An account cannot be deleted if it has journal entries or is a control account.
        """
        # Check if this is a control account (not for direct posting)
        is_control = getattr(entity, "is_control_account", False)
        account_type = getattr(entity, "account_type", None)
        if not is_control and account_type is not None:
            if isinstance(account_type, AccountType):
                is_control = account_type == AccountType.CONTROL
            else:
                value = getattr(account_type, "value", account_type)
                is_control = str(value).lower() == "control"

        if is_control:
            return (
                False,
                f"Cannot delete '{entity.account_name}': is a control account",
            )

        # Check for journal entry lines
        journal_count = (
            self.db.query(JournalEntryLine)
            .filter(JournalEntryLine.account_id == entity.account_id)
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
        if field_name == "normal_balance":
            normal_balance = getattr(entity, "normal_balance", None)
            return normal_balance.value if normal_balance else ""
        if field_name in ("category", "account_category"):
            category = getattr(entity, "category", None) or getattr(
                entity, "account_category", None
            )
            if category is None:
                return ""
            name = getattr(category, "category_name", None)
            if name is not None:
                return str(name)
            value = getattr(category, "value", category)
            return str(value)

        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get account export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"accounts_export_{timestamp}.csv"

    async def export_all(
        self,
        search: str = "",
        status: str = "",
        start_date: str = "",
        end_date: str = "",
        extra_filters: dict[str, object] | None = None,
        format: str = "csv",
    ) -> Response:
        """
        Export all accounts matching filters to CSV.
        """
        from app.services.finance.gl.account_query import build_account_query

        category = ""
        if extra_filters:
            category = str(
                extra_filters.get("category") or extra_filters.get("account_type") or ""
            )

        query = build_account_query(
            db=self.db,
            organization_id=str(self.organization_id),
            search=search,
            category=category or None,
            status=status,
        )

        entities = query.all()
        return self._build_csv(entities)


def get_account_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> AccountBulkService:
    """Factory function to create an AccountBulkService instance."""
    return AccountBulkService(db, organization_id, user_id)


class JournalBulkService(BulkActionService[JournalEntry]):
    """
    Bulk operations for journal entries.

    Supported actions:
    - post: Post journal entries to the ledger
    - delete: Remove journal entries (only DRAFT status)
    - export: Export to CSV
    """

    model = JournalEntry
    id_field = "journal_entry_id"
    org_field = "organization_id"
    search_fields = ["entry_number", "description", "reference"]
    date_field = "entry_date"

    # Fields to export in CSV
    export_fields = [
        ("journal_number", "Journal Number"),
        ("entry_date", "Entry Date"),
        ("posting_date", "Posting Date"),
        ("description", "Description"),
        ("source_module", "Source"),
        ("reference", "Reference"),
        ("total_debit", "Total Debit"),
        ("total_credit", "Total Credit"),
        ("status", "Status"),
    ]

    def can_delete(self, entity: JournalEntry) -> tuple[bool, str]:
        """
        Check if a journal entry can be deleted.

        A journal entry can only be deleted if status is DRAFT.
        """
        if entity.status != JournalStatus.DRAFT:
            return (
                False,
                f"Cannot delete '{entity.journal_number}': only DRAFT entries can be deleted (current status: {entity.status.value})",
            )
        return (True, "")

    def _get_export_value(self, entity: JournalEntry, field_name: str) -> str:
        """Handle special field formatting for journal export."""
        if field_name == "status":
            return entity.status.value if entity.status else ""
        if field_name in ("entry_date", "posting_date"):
            val = getattr(entity, field_name, None)
            return val.isoformat() if val else ""

        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get journal export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"journals_export_{timestamp}.csv"

    async def export_all(
        self,
        search: str = "",
        status: str = "",
        start_date: str = "",
        end_date: str = "",
        extra_filters: dict[str, object] | None = None,
        format: str = "csv",
    ) -> Response:
        """
        Export all journal entries matching filters to CSV.
        """
        from app.services.finance.gl.journal_query import build_journal_query

        query = build_journal_query(
            db=self.db,
            organization_id=str(self.organization_id),
            search=search,
            status=status,
            start_date=start_date,
            end_date=end_date,
        )

        entities = query.all()
        return self._build_csv(entities)

    async def bulk_post(self, ids: list[UUID]) -> BulkActionResult:
        """
        Post multiple journal entries to the ledger.

        Only entries in DRAFT or APPROVED status can be posted.
        """
        if not ids:
            return BulkActionResult.failure("No IDs provided")

        user_id = self.user_id
        if user_id is None:
            return BulkActionResult.failure("User ID is required to post journals")

        entities = self._get_entities(ids)
        if not entities:
            return BulkActionResult.failure(
                "No journal entries found with provided IDs"
            )

        success_count = 0
        failed_count = 0
        errors: list[str] = []

        for entry in entities:
            try:
                JournalService.post_journal(
                    self.db,
                    self.organization_id,
                    entry.journal_entry_id,
                    user_id,
                )
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{entry.journal_number}: {str(e)}")

        if success_count > 0:
            self.db.commit()

        if failed_count > 0:
            return BulkActionResult.partial(success_count, failed_count, errors)

        return BulkActionResult.success(
            success_count, f"Posted {success_count} journal entries"
        )

    async def bulk_approve(self, ids: list[UUID]) -> BulkActionResult:
        """
        Approve multiple journal entries.

        Only entries in DRAFT status can be approved.
        """
        if not ids:
            return BulkActionResult.failure("No IDs provided")

        user_id = self.user_id
        if user_id is None:
            return BulkActionResult.failure("User ID is required to approve journals")

        entities = self._get_entities(ids)
        if not entities:
            return BulkActionResult.failure(
                "No journal entries found with provided IDs"
            )

        success_count = 0
        failed_count = 0
        errors: list[str] = []

        for entry in entities:
            try:
                JournalService.approve_journal(
                    self.db,
                    self.organization_id,
                    entry.journal_entry_id,
                    user_id,
                )
                success_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"{entry.journal_number}: {str(e)}")

        if success_count > 0:
            self.db.commit()

        if failed_count > 0:
            return BulkActionResult.partial(success_count, failed_count, errors)

        return BulkActionResult.success(
            success_count, f"Approved {success_count} journal entries"
        )


def get_journal_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> JournalBulkService:
    """Factory function to create a JournalBulkService instance."""
    return JournalBulkService(db, organization_id, user_id)
