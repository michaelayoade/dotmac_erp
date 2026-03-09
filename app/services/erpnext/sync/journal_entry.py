"""
Journal Entry Sync Service - ERPNext to DotMac ERP.

Syncs ERPNext Journal Entries → gl.journal_entry + gl.journal_entry_line.
These are already-posted GL entries from ERPNext, so they arrive as POSTED.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.gl.journal_entry import (
    JournalEntry,
    JournalEntryLine,
    JournalStatus,
    JournalType,
)

from ..mappings.journal_entry import JournalEntryAccountMapping, JournalEntryMapping
from .base import BaseSyncService

logger = logging.getLogger(__name__)

# Map ERPNext voucher_type → DotMac JournalType
_TYPE_MAP: dict[str, JournalType] = {
    "Journal Entry": JournalType.STANDARD,
    "Bank Entry": JournalType.STANDARD,
    "Cash Entry": JournalType.STANDARD,
    "Credit Card Entry": JournalType.STANDARD,
    "Debit Note": JournalType.ADJUSTMENT,
    "Credit Note": JournalType.ADJUSTMENT,
    "Contra Entry": JournalType.STANDARD,
    "Excise Entry": JournalType.STANDARD,
    "Write Off Entry": JournalType.ADJUSTMENT,
    "Opening Entry": JournalType.OPENING,
    "Depreciation Entry": JournalType.STANDARD,
    "Exchange Rate Revaluation": JournalType.REVALUATION,
    "Exchange Gain Or Loss": JournalType.REVALUATION,
    "Deferred Revenue": JournalType.ADJUSTMENT,
    "Deferred Expense": JournalType.ADJUSTMENT,
}


class JournalEntrySyncService(BaseSyncService[JournalEntry]):
    """Sync Journal Entries from ERPNext."""

    source_doctype = "Journal Entry"
    target_table = "gl.journal_entry"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        super().__init__(db, organization_id, user_id)
        self._mapping = JournalEntryMapping()
        self._line_mapping = JournalEntryAccountMapping()
        self._journal_cache: dict[str, JournalEntry] = {}

    def fetch_records(self, client: Any, since: datetime | None = None):
        """Fetch Journal Entries with account lines."""
        if since:
            for je in client.get_modified_since(
                doctype="Journal Entry",
                since=since,
                filters={"docstatus": 1},
            ):
                je["accounts"] = client.list_documents(
                    doctype="Journal Entry Account",
                    filters={"parent": je["name"]},
                    parent="Journal Entry",
                )
                yield je
        else:
            yield from client.get_journal_entries()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        result = self._mapping.transform_record(record)
        result["_accounts"] = [
            self._line_mapping.transform_record(acct)
            for acct in record.get("accounts", [])
        ]
        return result

    def _resolve_entity_id(
        self, source_name: str | None, source_doctype: str
    ) -> uuid.UUID | None:
        if not source_name:
            return None

        from app.models.sync import SyncEntity

        sync_entity = self.db.execute(
            select(SyncEntity).where(
                SyncEntity.organization_id == self.organization_id,
                SyncEntity.source_system == "erpnext",
                SyncEntity.source_doctype == source_doctype,
                SyncEntity.source_name == source_name,
            )
        ).scalar_one_or_none()

        if sync_entity and sync_entity.target_id:
            return sync_entity.target_id
        return None

    def _resolve_account_id(self, account_source_name: str | None) -> uuid.UUID | None:
        return self._resolve_entity_id(account_source_name, "Account")

    def _resolve_fiscal_period(self, posting_date) -> uuid.UUID | None:
        """Find the fiscal period containing the posting date."""
        from app.models.finance.gl.fiscal_period import FiscalPeriod

        period = self.db.scalar(
            select(FiscalPeriod).where(
                FiscalPeriod.organization_id == self.organization_id,
                FiscalPeriod.start_date <= posting_date,
                FiscalPeriod.end_date >= posting_date,
            )
        )
        if period:
            return period.fiscal_period_id
        return None

    def _map_journal_type(self, voucher_type: str | None) -> JournalType:
        if not voucher_type:
            return JournalType.STANDARD
        return _TYPE_MAP.get(voucher_type, JournalType.STANDARD)

    def _generate_journal_number(self, reference_date=None) -> str:
        """Generate journal number."""
        from app.models.finance.core_config.numbering_sequence import SequenceType
        from app.services.finance.common.numbering import SyncNumberingService

        svc = SyncNumberingService(self.db)
        try:
            return svc.generate_next_number(
                self.organization_id, SequenceType.INVOICE, reference_date
            )
        except Exception:
            import time

            return f"JE-{int(time.time())}"

    def _create_journal_lines(
        self,
        journal_entry_id: uuid.UUID,
        accounts_data: list[dict[str, Any]],
    ) -> None:
        """Create JournalEntryLine records from account data."""
        for seq, acct_data in enumerate(accounts_data, 1):
            acct_data.pop("_source_name", None)
            acct_data.pop("_source_modified", None)
            account_source = acct_data.pop("_account_source_name", None)
            acct_data.pop("_cost_center_source_name", None)
            acct_data.pop("_project_source_name", None)
            acct_data.pop("_party_type", None)
            acct_data.pop("_party_source_name", None)

            account_id = self._resolve_account_id(account_source)
            if not account_id:
                logger.warning(
                    "Skipping journal line: account '%s' not found",
                    account_source,
                )
                continue

            line = JournalEntryLine(
                journal_entry_id=journal_entry_id,
                line_number=seq,
                account_id=account_id,
                debit_amount=acct_data.get("debit_amount", Decimal("0")),
                credit_amount=acct_data.get("credit_amount", Decimal("0")),
                debit_amount_functional=acct_data.get(
                    "debit_amount_functional", Decimal("0")
                ),
                credit_amount_functional=acct_data.get(
                    "credit_amount_functional", Decimal("0")
                ),
                currency_code=acct_data.get(
                    "currency_code", settings.default_functional_currency_code
                ),
                exchange_rate=acct_data.get("exchange_rate", Decimal("1")),
            )
            self.db.add(line)

    def create_entity(self, data: dict[str, Any]) -> JournalEntry:
        data.pop("_source_name", None)
        data.pop("_source_modified", None)
        voucher_type = data.pop("_voucher_type", None)
        accounts_data = data.pop("_accounts", [])
        data.pop("_multi_currency", None)
        user_remark = data.pop("_user_remark", None)
        data.pop("_cheque_date", None)
        docstatus = data.pop("_docstatus", 1)

        journal_type = self._map_journal_type(voucher_type)

        # These come in as submitted → treat as POSTED
        if docstatus == 1:
            status = JournalStatus.POSTED
        elif docstatus == 2:
            status = JournalStatus.VOID
        else:
            status = JournalStatus.DRAFT

        posting_date = data.get("posting_date")

        # Resolve fiscal period
        fiscal_period_id = self._resolve_fiscal_period(posting_date)
        if not fiscal_period_id:
            logger.warning(
                "No fiscal period found for posting date %s, skipping journal",
                posting_date,
            )
            raise ValueError(f"No fiscal period for date {posting_date}")

        journal_number = self._generate_journal_number(posting_date)

        # Use user_remark as description if available
        description = user_remark or data.get("description", "Journal Entry")

        journal = JournalEntry(
            organization_id=self.organization_id,
            journal_number=journal_number[:30],
            journal_type=journal_type,
            status=status,
            entry_date=posting_date,
            posting_date=posting_date,
            fiscal_period_id=fiscal_period_id,
            description=str(description)[:1000],
            reference=data.get("reference"),
            currency_code=settings.default_functional_currency_code,
            total_debit=data.get("total_debit", Decimal("0")),
            total_credit=data.get("total_credit", Decimal("0")),
            total_debit_functional=data.get("total_debit", Decimal("0")),
            total_credit_functional=data.get("total_credit", Decimal("0")),
            source_module="FIN",
            created_by_user_id=self.user_id,
        )

        self.db.add(journal)
        self.db.flush()

        if accounts_data:
            self._create_journal_lines(journal.journal_entry_id, accounts_data)

        return journal

    def update_entity(self, entity: JournalEntry, data: dict[str, Any]) -> JournalEntry:
        data.pop("_source_name", None)
        data.pop("_source_modified", None)
        data.pop("_voucher_type", None)
        data.pop("_accounts", [])
        data.pop("_multi_currency", None)
        data.pop("_user_remark", None)
        data.pop("_cheque_date", None)
        docstatus = data.pop("_docstatus", 1)

        # Update status based on docstatus
        if docstatus == 2:
            entity.status = JournalStatus.VOID
        elif docstatus == 1:
            entity.status = JournalStatus.POSTED

        entity.total_debit = data.get("total_debit", entity.total_debit)
        entity.total_credit = data.get("total_credit", entity.total_credit)

        return entity

    def get_entity_id(self, entity: JournalEntry) -> uuid.UUID:
        return entity.journal_entry_id

    def find_existing_entity(self, source_name: str) -> JournalEntry | None:
        if source_name in self._journal_cache:
            return self._journal_cache[source_name]

        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            journal = self.db.get(JournalEntry, sync_entity.target_id)
            if journal:
                self._journal_cache[source_name] = journal
                return journal

        return None
