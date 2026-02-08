"""
Stock Ledger Entry Sync Service - ERPNext to DotMac ERP.

Imports ERPNext Stock Ledger Entries as inv.inventory_transaction records.
Each SLE represents a single stock movement at a specific warehouse.
"""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory.inventory_transaction import (
    InventoryTransaction,
    TransactionType,
)
from app.models.sync import SyncEntity
from app.services.erpnext.mappings.stock_ledger import StockLedgerMapping

from .base import BaseSyncService

logger = logging.getLogger(__name__)


class StockLedgerSyncService(BaseSyncService[InventoryTransaction]):
    """
    Sync Stock Ledger Entries from ERPNext.

    Maps each SLE to an inv.inventory_transaction record, resolving:
    - item_code → item_id (via SyncEntity lookup)
    - warehouse → warehouse_id (via SyncEntity lookup)
    - posting_date → fiscal_period_id (via PeriodGuardService)
    - voucher_type + qty sign → TransactionType enum
    """

    source_doctype = "Stock Ledger Entry"
    target_table = "inv.inventory_transaction"

    def __init__(
        self,
        db: Session,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ):
        super().__init__(db, organization_id, user_id)
        self._mapping = StockLedgerMapping()
        # FK resolution caches
        self._item_cache: dict[str, uuid.UUID] = {}
        self._warehouse_cache: dict[str, uuid.UUID] = {}
        self._period_cache: dict[str, uuid.UUID] = {}  # "YYYY-MM" → period_id

    def fetch_records(self, client: Any, since: datetime | None = None):
        """Fetch Stock Ledger Entries from ERPNext."""
        if since:
            yield from client.get_modified_since(
                doctype="Stock Ledger Entry",
                since=since,
            )
        else:
            yield from client.get_stock_ledger_entries()

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext SLE to DotMac format."""
        return self._mapping.transform_record(record)

    def _resolve_entity_id(
        self, source_name: str | None, source_doctype: str
    ) -> uuid.UUID | None:
        """Resolve DotMac entity ID from ERPNext source name via SyncEntity."""
        if not source_name:
            return None

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

    def _resolve_item_id(self, item_code: str | None) -> uuid.UUID | None:
        """Resolve DotMac item_id from ERPNext item_code."""
        if not item_code:
            return None
        if item_code in self._item_cache:
            return self._item_cache[item_code]

        result = self._resolve_entity_id(item_code, "Item")
        if result:
            self._item_cache[item_code] = result
        return result

    def _resolve_warehouse_id(self, warehouse: str | None) -> uuid.UUID | None:
        """Resolve DotMac warehouse_id from ERPNext warehouse name."""
        if not warehouse:
            return None
        if warehouse in self._warehouse_cache:
            return self._warehouse_cache[warehouse]

        result = self._resolve_entity_id(warehouse, "Warehouse")
        if result:
            self._warehouse_cache[warehouse] = result
        return result

    def _resolve_fiscal_period_id(self, posting_date: date) -> uuid.UUID | None:
        """
        Resolve fiscal_period_id for a posting date, auto-creating if needed.

        Caches by year-month to avoid repeated DB lookups.
        """
        cache_key = f"{posting_date.year}-{posting_date.month:02d}"
        if cache_key in self._period_cache:
            return self._period_cache[cache_key]

        from app.services.finance.gl.period_guard import PeriodGuardService

        # Use _ensure_period_exists to auto-create periods for historical dates
        period = PeriodGuardService._ensure_period_exists(
            self.db, self.organization_id, posting_date
        )

        if period:
            self._period_cache[cache_key] = period.fiscal_period_id
            return period.fiscal_period_id

        return None

    @staticmethod
    def _parse_posting_datetime(
        posting_date: str | None, posting_time: str | None
    ) -> datetime:
        """Build timezone-aware datetime from ERPNext posting_date + posting_time."""
        if not posting_date:
            return datetime.now(UTC)

        date_str = str(posting_date).strip()[:10]  # "YYYY-MM-DD"

        time_str = "00:00:00"
        if posting_time:
            time_str = str(posting_time).strip()[:8]  # "HH:MM:SS"

        try:
            dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                dt = datetime.now()

        return dt.replace(tzinfo=UTC)

    @staticmethod
    def _parse_date(posting_date: str | None) -> date:
        """Parse posting_date string to date object."""
        if not posting_date:
            return date.today()
        try:
            return datetime.strptime(str(posting_date).strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            return date.today()

    def create_entity(self, data: dict[str, Any]) -> InventoryTransaction:
        """Create InventoryTransaction from transformed SLE data."""
        # Pop internal fields used for FK resolution
        item_code = data.pop("_item_code", None)
        warehouse_code = data.pop("_warehouse_code", None)
        posting_date_str = data.pop("_posting_date", None)
        posting_time_str = data.pop("_posting_time", None)
        actual_qty = data.pop("_actual_qty", Decimal("0"))
        qty_after = data.pop("_qty_after", None)
        value_diff = data.pop("_value_diff", Decimal("0"))
        data.pop("_batch_no", None)  # TODO: resolve to lot_id when lots are imported
        data.pop("_source_modified", None)

        # Resolve FKs
        item_id = self._resolve_item_id(item_code)
        if not item_id:
            raise ValueError(f"Cannot resolve item_id for item_code={item_code}")

        warehouse_id = self._resolve_warehouse_id(warehouse_code)
        if not warehouse_id:
            raise ValueError(
                f"Cannot resolve warehouse_id for warehouse={warehouse_code}"
            )

        posting_date = self._parse_date(posting_date_str)
        fiscal_period_id = self._resolve_fiscal_period_id(posting_date)
        if not fiscal_period_id:
            raise ValueError(f"Cannot resolve fiscal period for date={posting_date}")

        # Build transaction datetime
        transaction_date = self._parse_posting_datetime(
            posting_date_str, posting_time_str
        )

        # Determine transaction type from the mapping
        transaction_type_str = data.get("transaction_type", "RECEIPT")
        try:
            transaction_type = TransactionType(transaction_type_str)
        except ValueError:
            transaction_type = (
                TransactionType.RECEIPT if actual_qty > 0 else TransactionType.ISSUE
            )

        # Quantities: store as absolute value
        quantity = abs(actual_qty) if actual_qty else Decimal("0")

        # Unit cost and total cost
        unit_cost = data.get("unit_cost") or Decimal("0")
        if isinstance(unit_cost, (int, float, str)):
            unit_cost = Decimal(str(unit_cost))
        total_cost = abs(value_diff) if value_diff else quantity * abs(unit_cost)

        # Running balances
        if qty_after is not None:
            quantity_after = Decimal(str(qty_after))
            quantity_before = quantity_after - (actual_qty or Decimal("0"))
        else:
            quantity_before = Decimal("0")
            quantity_after = Decimal("0")

        txn = InventoryTransaction(
            organization_id=self.organization_id,
            transaction_type=transaction_type,
            transaction_date=transaction_date,
            fiscal_period_id=fiscal_period_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            quantity=quantity,
            uom=data.get("uom", "Nos"),
            unit_cost=abs(unit_cost),
            total_cost=total_cost,
            currency_code=data.get("currency_code", "NGN"),
            cost_variance=Decimal("0"),
            quantity_before=quantity_before,
            quantity_after=quantity_after,
            source_document_type=data.get("source_document_type"),
            reference=data.get("reference"),
            created_by_user_id=self.user_id,
        )

        return txn

    def update_entity(
        self, entity: InventoryTransaction, data: dict[str, Any]
    ) -> InventoryTransaction:
        """
        Update existing transaction.

        SLEs are immutable in ERPNext (cancelled + recreated), so updates
        should be rare. We update cost/qty fields if the SLE was modified.
        """
        actual_qty = data.pop("_actual_qty", None)
        qty_after = data.pop("_qty_after", None)
        value_diff = data.pop("_value_diff", None)
        # Pop unused internal fields
        data.pop("_item_code", None)
        data.pop("_warehouse_code", None)
        data.pop("_posting_date", None)
        data.pop("_posting_time", None)
        data.pop("_batch_no", None)
        data.pop("_source_modified", None)

        if actual_qty is not None:
            entity.quantity = abs(actual_qty)

        unit_cost = data.get("unit_cost")
        if unit_cost is not None:
            entity.unit_cost = abs(Decimal(str(unit_cost)))

        if value_diff is not None:
            entity.total_cost = abs(Decimal(str(value_diff)))

        if qty_after is not None:
            entity.quantity_after = Decimal(str(qty_after))
            if actual_qty is not None:
                entity.quantity_before = entity.quantity_after - actual_qty

        return entity

    def get_entity_id(self, entity: InventoryTransaction) -> uuid.UUID:
        """Get the transaction ID."""
        return entity.transaction_id

    def find_existing_entity(self, source_name: str) -> InventoryTransaction | None:
        """Find existing transaction by sync record."""
        sync_entity = self.get_sync_entity(source_name)
        if sync_entity and sync_entity.target_id:
            return self.db.get(InventoryTransaction, sync_entity.target_id)
        return None
