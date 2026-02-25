"""
Weighted-average cost valuation service.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory.item_wac_ledger import ItemWACLedger
from app.services.common import coerce_uuid


@dataclass(frozen=True)
class WACSnapshot:
    quantity: Decimal
    wac: Decimal
    total_value: Decimal


@dataclass(frozen=True)
class WACResult:
    previous_wac: Decimal
    new_wac: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    new_balance_qty: Decimal
    new_balance_value: Decimal


class WACValuationService:
    """Weighted-average costing calculations and ledger updates."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_snapshot(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
    ) -> WACSnapshot:
        ledger = self.db.scalar(
            select(ItemWACLedger).where(
                ItemWACLedger.organization_id == coerce_uuid(organization_id),
                ItemWACLedger.item_id == coerce_uuid(item_id),
                ItemWACLedger.warehouse_id == coerce_uuid(warehouse_id),
            )
        )
        if not ledger:
            return WACSnapshot(
                quantity=Decimal("0"),
                wac=Decimal("0"),
                total_value=Decimal("0"),
            )
        return WACSnapshot(
            quantity=Decimal(str(ledger.quantity_on_hand or 0)),
            wac=Decimal(str(ledger.current_wac or 0)),
            total_value=Decimal(str(ledger.total_value or 0)),
        )

    def calculate_receipt_cost(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        receipt_qty: Decimal,
        receipt_unit_cost: Decimal,
    ) -> WACResult:
        current = self.get_snapshot(organization_id, item_id, warehouse_id)
        if receipt_qty <= 0:
            raise ValueError("Receipt quantity must be positive.")
        if receipt_unit_cost < 0:
            raise ValueError("Receipt unit cost cannot be negative.")

        receipt_total = receipt_qty * receipt_unit_cost
        new_qty = current.quantity + receipt_qty
        if new_qty == 0:
            new_wac = Decimal("0")
        else:
            new_wac = ((current.total_value + receipt_total) / new_qty).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )
        new_total = (new_qty * new_wac).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        return WACResult(
            previous_wac=current.wac,
            new_wac=new_wac,
            unit_cost=receipt_unit_cost,
            total_cost=receipt_total,
            new_balance_qty=new_qty,
            new_balance_value=new_total,
        )

    def calculate_issue_cost(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        issue_qty: Decimal,
    ) -> WACResult:
        current = self.get_snapshot(organization_id, item_id, warehouse_id)
        if issue_qty <= 0:
            raise ValueError("Issue quantity must be positive.")
        if current.quantity < issue_qty:
            raise ValueError(
                f"Insufficient stock: {current.quantity} available, {issue_qty} requested"
            )

        unit_cost = current.wac
        issue_total = (issue_qty * unit_cost).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        new_qty = current.quantity - issue_qty
        new_total = (new_qty * unit_cost).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        return WACResult(
            previous_wac=current.wac,
            new_wac=current.wac,
            unit_cost=unit_cost,
            total_cost=issue_total,
            new_balance_qty=new_qty,
            new_balance_value=new_total,
        )

    def apply_receipt(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        receipt_qty: Decimal,
        receipt_unit_cost: Decimal,
        *,
        transaction_id: UUID | None = None,
    ) -> WACResult:
        result = self.calculate_receipt_cost(
            organization_id,
            item_id,
            warehouse_id,
            receipt_qty,
            receipt_unit_cost,
        )
        ledger = self._get_or_create_ledger(organization_id, item_id, warehouse_id)
        ledger.current_wac = result.new_wac
        ledger.quantity_on_hand = result.new_balance_qty
        ledger.total_value = result.new_balance_value
        ledger.last_transaction_id = transaction_id
        self.db.flush()
        return result

    def apply_issue(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        issue_qty: Decimal,
        *,
        transaction_id: UUID | None = None,
    ) -> WACResult:
        result = self.calculate_issue_cost(
            organization_id,
            item_id,
            warehouse_id,
            issue_qty,
        )
        ledger = self._get_or_create_ledger(organization_id, item_id, warehouse_id)
        ledger.current_wac = result.new_wac
        ledger.quantity_on_hand = result.new_balance_qty
        ledger.total_value = result.new_balance_value
        ledger.last_transaction_id = transaction_id
        self.db.flush()
        return result

    def _get_or_create_ledger(
        self,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
    ) -> ItemWACLedger:
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)
        wh_id = coerce_uuid(warehouse_id)
        ledger = self.db.scalar(
            select(ItemWACLedger).where(
                ItemWACLedger.organization_id == org_id,
                ItemWACLedger.item_id == itm_id,
                ItemWACLedger.warehouse_id == wh_id,
            )
        )
        if ledger:
            return ledger

        ledger = ItemWACLedger(
            organization_id=org_id,
            item_id=itm_id,
            warehouse_id=wh_id,
            current_wac=Decimal("0"),
            quantity_on_hand=Decimal("0"),
            total_value=Decimal("0"),
        )
        self.db.add(ledger)
        self.db.flush()
        return ledger
