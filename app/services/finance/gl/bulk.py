"""
GL Bulk Action Services.

Provides bulk operations for chart of accounts and journal entries.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.gl.account import Account, AccountType
from app.models.finance.gl.account_balance import AccountBalance
from app.models.finance.gl.journal_entry import JournalEntry, JournalStatus
from app.models.finance.gl.journal_entry_line import JournalEntryLine
from app.models.finance.gl.posted_ledger_line import PostedLedgerLine
from app.schemas.bulk_actions import BulkActionResult
from app.services.bulk_actions import BulkActionService
from app.services.common import coerce_uuid
from app.services.finance.common.helpers import coerce_scalar_count
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

    def _is_control_account(self, entity: Account) -> bool:
        """True if the account is a CONTROL type (roll-up, not postable)."""
        if bool(getattr(entity, "is_control_account", False)):
            return True
        account_type = getattr(entity, "account_type", None)
        if account_type is None:
            return False
        if isinstance(account_type, AccountType):
            return account_type == AccountType.CONTROL
        value = getattr(account_type, "value", account_type)
        return str(value).lower() == "control"

    def can_delete(self, entity: Account) -> tuple[bool, str]:
        """
        Check if an account can be deleted.

        An account cannot be deleted if it has journal entries or is a control account.
        """
        if self._is_control_account(entity):
            return (
                False,
                f"Cannot delete '{entity.account_name}': is a control account",
            )

        # Check for journal entry lines
        count_stmt = (
            select(func.count())
            .select_from(JournalEntryLine)
            .where(JournalEntryLine.account_id == entity.account_id)
        )
        journal_count = coerce_scalar_count(self.db.scalar(count_stmt)) or 0

        if journal_count > 0:
            return (
                False,
                f"Cannot delete '{entity.account_name}': has {journal_count} journal entries",
            )

        return (True, "")

    async def bulk_delete(self, ids: list[UUID]) -> BulkActionResult:
        """
        Atomic bulk delete.

        Pre-flights every account in the batch with a single aggregate query
        per dependency type. If *any* account fails validation, no delete
        runs — the entire batch is rejected so the caller never ends up in
        a half-applied state.
        """
        if not ids:
            return BulkActionResult.failure("No IDs provided")

        entities = self._get_entities(ids)
        if not entities:
            return BulkActionResult.failure("No entities found with provided IDs")

        account_ids = [coerce_uuid(e.account_id) for e in entities]
        by_id = {coerce_uuid(e.account_id): e for e in entities}

        # One query: journal-line counts per account.
        line_count_rows = self.db.execute(
            select(
                JournalEntryLine.account_id,
                func.count().label("line_count"),
            )
            .where(JournalEntryLine.account_id.in_(account_ids))
            .group_by(JournalEntryLine.account_id)
        ).all()
        line_counts = {row[0]: row[1] for row in line_count_rows}

        # One query: balance-row counts per account.
        balance_count_rows = self.db.execute(
            select(
                AccountBalance.account_id,
                func.count().label("balance_count"),
            )
            .where(AccountBalance.account_id.in_(account_ids))
            .group_by(AccountBalance.account_id)
        ).all()
        balance_counts = {row[0]: row[1] for row in balance_count_rows}

        errors: list[str] = []
        for acc_id in account_ids:
            account = by_id[acc_id]
            if self._is_control_account(account):
                errors.append(
                    f"Cannot delete '{account.account_name}': is a control account"
                )
                continue
            lc = line_counts.get(acc_id, 0)
            if lc > 0:
                errors.append(
                    f"Cannot delete '{account.account_name}': has {lc} journal entries"
                )
                continue
            bc = balance_counts.get(acc_id, 0)
            if bc > 0:
                errors.append(
                    f"Cannot delete '{account.account_name}': has {bc} balance records"
                )

        if errors:
            return BulkActionResult.failure(
                "Batch rejected — no accounts deleted. " + "; ".join(errors)
            )

        try:
            for account in entities:
                self.db.delete(account)
            self.db.commit()
        except Exception as exc:
            self.db.rollback()
            logger.exception("Atomic bulk delete failed: %s", exc)
            return BulkActionResult.failure(f"Delete failed: {exc}")

        return BulkActionResult.success(
            len(entities), f"Deleted {len(entities)} accounts"
        )

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

        entities = list(self.db.scalars(query).all())
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

        entities = list(self.db.scalars(query).all())
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


class LedgerBulkService(BulkActionService[PostedLedgerLine]):
    """
    Bulk operations for posted ledger lines.

    Supported actions:
    - export: Export posted ledger transactions to CSV

    Note: The posted ledger is append-only and immutable (never UPDATE or
    DELETE), so delete is not supported.
    """

    model = PostedLedgerLine
    id_field = "ledger_line_id"
    org_field = "organization_id"
    search_fields = ["description", "journal_reference", "account_code"]
    date_field = "posting_date"

    # Fields to export in CSV
    export_fields = [
        ("posting_date", "Posting Date"),
        ("entry_date", "Entry Date"),
        ("account_code", "Account Code"),
        ("description", "Description"),
        ("journal_reference", "Reference"),
        ("debit_amount", "Debit"),
        ("credit_amount", "Credit"),
        ("original_currency_code", "Currency"),
        ("source_module", "Source"),
    ]

    def can_delete(self, entity: PostedLedgerLine) -> tuple[bool, str]:
        """The posted ledger is immutable — lines can never be deleted."""
        return (False, "Posted ledger lines are immutable and cannot be deleted")

    def _get_all_query(self, search: str = ""):
        """Order ledger export chronologically (most recent first)."""
        query = super()._get_all_query(search)
        return query.order_by(
            PostedLedgerLine.posting_date.desc(),
            PostedLedgerLine.posted_at.desc(),
        )

    def _get_export_value(self, entity: PostedLedgerLine, field_name: str) -> str:
        """Handle special field formatting for ledger export."""
        if field_name in ("posting_date", "entry_date"):
            val = getattr(entity, field_name, None)
            return val.isoformat() if val else ""

        return str(super()._get_export_value(entity, field_name))

    def _get_export_filename(self) -> str:
        """Get ledger export filename."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"ledger_export_{timestamp}.csv"


def get_ledger_bulk_service(
    db: Session,
    organization_id: UUID,
    user_id: UUID | None = None,
) -> LedgerBulkService:
    """Factory function to create a LedgerBulkService instance."""
    return LedgerBulkService(db, organization_id, user_id)
