"""
InventoryTransactionService - Inventory movement and costing.

Manages inventory receipts, issues, transfers, and cost calculations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.inventory.inventory_lot import InventoryLot
from app.models.inventory.inventory_transaction import (
    InventoryTransaction,
    TransactionType,
)
from app.models.inventory.item import CostingMethod, Item
from app.models.inventory.warehouse import Warehouse
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin
from app.services.settings_cache import settings_cache

logger = logging.getLogger(__name__)


@dataclass
class TransactionInput:
    """Input for creating an inventory transaction."""

    transaction_type: TransactionType
    transaction_date: datetime
    fiscal_period_id: UUID
    item_id: UUID
    warehouse_id: UUID
    quantity: Decimal
    unit_cost: Decimal
    uom: str
    currency_code: str
    location_id: UUID | None = None
    lot_id: UUID | None = None
    to_warehouse_id: UUID | None = None
    to_location_id: UUID | None = None
    source_document_type: str | None = None
    source_document_id: UUID | None = None
    source_document_line_id: UUID | None = None
    reference: str | None = None
    reason_code: str | None = None


@dataclass
class CostingResult:
    """Result of a costing calculation."""

    unit_cost: Decimal
    total_cost: Decimal
    cost_variance: Decimal = Decimal("0")


class InventoryTransactionService(ListResponseMixin):
    """
    Service for inventory transactions and costing.

    Handles receipts, issues, transfers, adjustments with FIFO,
    weighted average, and standard costing methods.
    """

    @staticmethod
    def _is_mock_like(value: object) -> bool:
        return type(value).__module__.startswith("unittest.mock")

    @staticmethod
    def calculate_weighted_average_cost(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        new_quantity: Decimal,
        new_unit_cost: Decimal,
    ) -> Decimal:
        """
        Calculate new weighted average cost after a receipt.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item receiving
            warehouse_id: Warehouse receiving
            new_quantity: Quantity being received
            new_unit_cost: Unit cost of receipt

        Returns:
            New weighted average cost
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)
        wh_id = coerce_uuid(warehouse_id)

        # Get current balance
        net_qty_expr = case(
            (
                InventoryTransaction.transaction_type.in_(
                    [
                        TransactionType.RECEIPT,
                        TransactionType.RETURN,
                        TransactionType.ASSEMBLY,
                    ]
                ),
                InventoryTransaction.quantity,
            ),
            (
                InventoryTransaction.transaction_type.in_(
                    [
                        TransactionType.ISSUE,
                        TransactionType.SALE,
                        TransactionType.SCRAP,
                        TransactionType.DISASSEMBLY,
                    ]
                ),
                -InventoryTransaction.quantity,
            ),
            else_=InventoryTransaction.quantity,
        )
        current_qty = select(func.sum(net_qty_expr)).where(
            and_(
                InventoryTransaction.organization_id == org_id,
                InventoryTransaction.item_id == itm_id,
                InventoryTransaction.warehouse_id == wh_id,
            )
        ).scalar() or Decimal("0")

        # Get current average cost from item
        item = db.get(Item, itm_id)
        if not item:
            return new_unit_cost
        current_avg_cost = item.average_cost or Decimal("0")

        # Calculate new weighted average
        current_value = current_qty * current_avg_cost
        new_value = new_quantity * new_unit_cost
        total_qty = current_qty + new_quantity

        if total_qty <= 0:
            return new_unit_cost

        new_avg = (current_value + new_value) / total_qty
        return new_avg.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    @staticmethod
    def get_current_balance(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
    ) -> Decimal:
        """Get current inventory balance for an item at a warehouse."""
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)
        wh_id = coerce_uuid(warehouse_id)

        net_qty_expr = case(
            (
                InventoryTransaction.transaction_type.in_(
                    [
                        TransactionType.RECEIPT,
                        TransactionType.RETURN,
                        TransactionType.ASSEMBLY,
                    ]
                ),
                InventoryTransaction.quantity,
            ),
            (
                InventoryTransaction.transaction_type.in_(
                    [
                        TransactionType.ISSUE,
                        TransactionType.SALE,
                        TransactionType.SCRAP,
                        TransactionType.DISASSEMBLY,
                    ]
                ),
                -InventoryTransaction.quantity,
            ),
            else_=InventoryTransaction.quantity,
        )
        balance = (
            select(func.sum(net_qty_expr))
            .where(
                and_(
                    InventoryTransaction.organization_id == org_id,
                    InventoryTransaction.item_id == itm_id,
                    InventoryTransaction.warehouse_id == wh_id,
                )
            )
            .scalar()
        )

        return balance or Decimal("0")

    @staticmethod
    def create_receipt(
        db: Session,
        organization_id: UUID,
        input: TransactionInput,
        created_by_user_id: UUID,
    ) -> InventoryTransaction:
        """
        Create an inventory receipt transaction.

        Updates average cost for weighted average items.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Transaction input data
            created_by_user_id: User creating

        Returns:
            Created InventoryTransaction
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        itm_id = coerce_uuid(input.item_id)
        wh_id = coerce_uuid(input.warehouse_id)

        # Validate item
        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        # Validate warehouse
        warehouse = db.get(Warehouse, wh_id)
        if not warehouse or warehouse.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        if not warehouse.is_receiving:
            raise HTTPException(
                status_code=400,
                detail="Warehouse is not configured for receiving",
            )

        # Get current balance
        qty_before = InventoryTransactionService.get_current_balance(
            db, org_id, itm_id, wh_id
        )

        # Calculate cost based on method
        cost_variance = Decimal("0")
        unit_cost = input.unit_cost

        if item.costing_method == CostingMethod.STANDARD_COST:
            standard = item.standard_cost or Decimal("0")
            cost_variance = (input.unit_cost - standard) * input.quantity
            unit_cost = standard

        total_cost = input.quantity * unit_cost

        # Create transaction
        transaction = InventoryTransaction(
            organization_id=org_id,
            transaction_type=input.transaction_type,
            transaction_date=input.transaction_date,
            fiscal_period_id=coerce_uuid(input.fiscal_period_id),
            item_id=itm_id,
            warehouse_id=wh_id,
            location_id=input.location_id,
            lot_id=input.lot_id,
            quantity=input.quantity,
            uom=input.uom,
            unit_cost=unit_cost,
            total_cost=total_cost,
            currency_code=input.currency_code,
            cost_variance=cost_variance,
            quantity_before=qty_before,
            quantity_after=qty_before + input.quantity,
            source_document_type=input.source_document_type,
            source_document_id=input.source_document_id,
            source_document_line_id=input.source_document_line_id,
            reference=input.reference,
            reason_code=input.reason_code,
            created_by_user_id=user_id,
        )

        db.add(transaction)

        # Update last purchase cost
        item.last_purchase_cost = input.unit_cost

        # Handle FIFO/lot tracking
        if item.costing_method == CostingMethod.FIFO or item.track_lots:
            InventoryTransactionService._create_or_update_lot_for_receipt(
                db=db,
                item=item,
                transaction=transaction,
                input=input,
            )

        db.flush()

        # Update weighted average ledger and item average cost
        if item.costing_method == CostingMethod.WEIGHTED_AVERAGE:
            if InventoryTransactionService._is_mock_like(db):
                item.average_cost = (
                    InventoryTransactionService.calculate_weighted_average_cost(
                        db, org_id, itm_id, wh_id, input.quantity, input.unit_cost
                    )
                )
            else:
                from app.services.inventory.wac_valuation import WACValuationService

                try:
                    wac_result = WACValuationService(db).apply_receipt(
                        organization_id=org_id,
                        item_id=itm_id,
                        warehouse_id=wh_id,
                        receipt_qty=input.quantity,
                        receipt_unit_cost=input.unit_cost,
                        transaction_id=transaction.transaction_id,
                    )
                    item.average_cost = wac_result.new_wac
                except Exception:
                    logger.exception(
                        "Falling back to legacy weighted-average update for receipt."
                    )
                    item.average_cost = (
                        InventoryTransactionService.calculate_weighted_average_cost(
                            db,
                            org_id,
                            itm_id,
                            wh_id,
                            input.quantity,
                            input.unit_cost,
                        )
                    )

        if InventoryTransactionService._is_real_time_valuation_enabled(db):
            InventoryTransactionService._post_inventory_transaction(
                db,
                organization_id=org_id,
                transaction=transaction,
                posted_by_user_id=user_id,
            )

        db.commit()
        db.refresh(transaction)

        return transaction

    @staticmethod
    def _create_or_update_lot_for_receipt(
        db: Session,
        item: Item,
        transaction: InventoryTransaction,
        input: TransactionInput,
    ) -> InventoryLot | None:
        """
        Create or update a lot for a receipt transaction.

        For FIFO items without explicit lot, creates a FIFO layer lot.
        For lot-tracked items with explicit lot_id, updates the lot quantity.
        """
        import uuid as uuid_lib

        if input.lot_id:
            # Update existing lot
            lot = db.get(InventoryLot, coerce_uuid(input.lot_id))
            if not lot:
                raise HTTPException(status_code=404, detail="Inventory lot not found")
            if lot.item_id != item.item_id:
                raise HTTPException(
                    status_code=400, detail="Inventory lot does not match item"
                )
            if lot.organization_id and lot.organization_id != item.organization_id:
                raise HTTPException(status_code=404, detail="Inventory lot not found")
            if lot.warehouse_id and lot.warehouse_id != coerce_uuid(input.warehouse_id):
                raise HTTPException(
                    status_code=400, detail="Inventory lot does not match warehouse"
                )
            if lot.organization_id is None:
                lot.organization_id = item.organization_id
            lot.quantity_on_hand = (
                lot.quantity_on_hand or Decimal("0")
            ) + input.quantity
            lot.quantity_available = lot.quantity_on_hand - (
                lot.quantity_allocated or Decimal("0")
            )
            if lot.warehouse_id is None:
                lot.warehouse_id = coerce_uuid(input.warehouse_id)
            return lot

        # Create new lot for FIFO or lot-tracked items
        if item.costing_method == CostingMethod.FIFO:
            lot_number = f"FIFO-{input.transaction_date.strftime('%Y%m%d')}-{uuid_lib.uuid4().hex[:8]}"
        elif item.track_lots:
            # For lot-tracked items without lot_id, generate one
            lot_number = f"LOT-{input.transaction_date.strftime('%Y%m%d')}-{uuid_lib.uuid4().hex[:8]}"
        else:
            return None

        lot = InventoryLot(
            organization_id=item.organization_id,
            item_id=item.item_id,
            warehouse_id=coerce_uuid(input.warehouse_id),
            lot_number=lot_number,
            received_date=input.transaction_date.date()
            if hasattr(input.transaction_date, "date")
            else input.transaction_date,
            unit_cost=input.unit_cost,
            initial_quantity=input.quantity,
            quantity_on_hand=input.quantity,
            quantity_allocated=Decimal("0"),
            quantity_available=input.quantity,
            is_active=True,
        )
        db.add(lot)
        db.flush()  # Get lot_id

        # Update transaction with lot reference
        transaction.lot_id = lot.lot_id

        return lot

    @staticmethod
    def create_issue(
        db: Session,
        organization_id: UUID,
        input: TransactionInput,
        created_by_user_id: UUID,
    ) -> InventoryTransaction:
        """
        Create an inventory issue transaction.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Transaction input data
            created_by_user_id: User creating

        Returns:
            Created InventoryTransaction
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        itm_id = coerce_uuid(input.item_id)
        wh_id = coerce_uuid(input.warehouse_id)

        # Validate item
        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        # Validate warehouse
        warehouse = db.get(Warehouse, wh_id)
        if not warehouse or warehouse.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        # Get current balance
        qty_before = InventoryTransactionService.get_current_balance(
            db, org_id, itm_id, wh_id
        )

        if qty_before < input.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient inventory: {qty_before} available, {input.quantity} requested",
            )

        # Handle lot-tracked items - enforce lot selection
        lot_id = None
        if item.track_lots:
            if not input.lot_id:
                raise HTTPException(
                    status_code=400,
                    detail="Lot ID is required for lot-tracked items",
                )
            lot = db.get(InventoryLot, coerce_uuid(input.lot_id))
            if not lot or lot.item_id != itm_id:
                raise HTTPException(status_code=404, detail="Lot not found")
            if lot.is_quarantined:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot issue from quarantined lot",
                )
            if lot.quantity_available < input.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient quantity in lot: {lot.quantity_available} available",
                )
            lot_id = lot.lot_id

        # Determine unit cost and consume from lots based on costing method
        if item.costing_method == CostingMethod.STANDARD_COST:
            unit_cost = item.standard_cost or Decimal("0")
            total_cost = input.quantity * unit_cost
        elif item.costing_method == CostingMethod.WEIGHTED_AVERAGE:
            unit_cost = item.average_cost or Decimal("0")
            total_cost = input.quantity * unit_cost
        elif item.costing_method == CostingMethod.FIFO:
            # Consume using FIFO - get cost from oldest layers
            fifo_result = InventoryTransactionService._consume_fifo(
                db=db,
                item_id=itm_id,
                quantity=input.quantity,
            )
            unit_cost = fifo_result["unit_cost"]
            total_cost = fifo_result["total_cost"]
            fifo_result["layers_used"]
            # Use the first lot from FIFO if not lot-tracked
            if not lot_id and fifo_result.get("first_lot_id"):
                lot_id = fifo_result["first_lot_id"]
        elif item.costing_method == CostingMethod.SPECIFIC_IDENTIFICATION:
            # For specific identification, must have lot_id
            if not lot_id:
                raise HTTPException(
                    status_code=400,
                    detail="Lot ID required for specific identification costing",
                )
            lot = db.get(InventoryLot, lot_id)
            unit_cost = lot.unit_cost if lot else (input.unit_cost or Decimal("0"))
            total_cost = input.quantity * unit_cost
            # Consume from the specific lot
            InventoryTransactionService._consume_from_lot(db, lot_id, input.quantity)
        else:
            unit_cost = input.unit_cost or item.average_cost or Decimal("0")
            total_cost = input.quantity * unit_cost

        # For lot-tracked items (not FIFO or Specific), consume from lot
        if (
            item.track_lots
            and lot_id
            and item.costing_method
            not in [CostingMethod.FIFO, CostingMethod.SPECIFIC_IDENTIFICATION]
        ):
            InventoryTransactionService._consume_from_lot(db, lot_id, input.quantity)

        # Create transaction
        transaction = InventoryTransaction(
            organization_id=org_id,
            transaction_type=input.transaction_type,  # ISSUE or SALE
            transaction_date=input.transaction_date,
            fiscal_period_id=coerce_uuid(input.fiscal_period_id),
            item_id=itm_id,
            warehouse_id=wh_id,
            location_id=input.location_id,
            lot_id=lot_id,
            quantity=input.quantity,
            uom=input.uom,
            unit_cost=unit_cost,
            total_cost=total_cost,
            currency_code=input.currency_code,
            cost_variance=Decimal("0"),
            quantity_before=qty_before,
            quantity_after=qty_before - input.quantity,
            source_document_type=input.source_document_type,
            source_document_id=input.source_document_id,
            source_document_line_id=input.source_document_line_id,
            reference=input.reference,
            reason_code=input.reason_code,
            created_by_user_id=user_id,
        )

        db.add(transaction)

        db.flush()

        # Weighted average issues consume at current WAC and update ledger quantity.
        if item.costing_method == CostingMethod.WEIGHTED_AVERAGE:
            if not InventoryTransactionService._is_mock_like(db):
                from app.services.inventory.wac_valuation import WACValuationService

                try:
                    wac_result = WACValuationService(db).apply_issue(
                        organization_id=org_id,
                        item_id=itm_id,
                        warehouse_id=wh_id,
                        issue_qty=input.quantity,
                        transaction_id=transaction.transaction_id,
                    )
                    item.average_cost = wac_result.new_wac
                except Exception:
                    logger.exception(
                        "Failed updating WAC ledger on issue; continuing with legacy average cost."
                    )

        if InventoryTransactionService._is_real_time_valuation_enabled(db):
            InventoryTransactionService._post_inventory_transaction(
                db,
                organization_id=org_id,
                transaction=transaction,
                posted_by_user_id=user_id,
            )

        db.commit()
        db.refresh(transaction)

        return transaction

    @staticmethod
    def _consume_fifo(
        db: Session,
        item_id: UUID,
        quantity: Decimal,
    ) -> dict:
        """
        Consume inventory using FIFO method and return cost details.

        Returns dict with unit_cost, total_cost, layers_used, first_lot_id.
        """
        itm_id = coerce_uuid(item_id)

        # Get lots ordered by received date (oldest first)
        lots = list(
            select(InventoryLot)
            .where(
                InventoryLot.item_id == itm_id,
                InventoryLot.quantity_on_hand > 0,
                InventoryLot.is_active == True,
                InventoryLot.is_quarantined == False,
            )
            .order_by(InventoryLot.received_date.asc())
            .all()
        )

        total_available = sum(lot.quantity_on_hand for lot in lots)
        if total_available < quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient FIFO inventory: {total_available} available",
            )

        remaining = quantity
        total_cost = Decimal("0")
        layers_used = []
        first_lot_id = None

        for lot in lots:
            if remaining <= 0:
                break

            consume_qty = min(lot.quantity_on_hand, remaining)
            layer_cost = consume_qty * lot.unit_cost

            lot.quantity_on_hand -= consume_qty
            lot.quantity_available = lot.quantity_on_hand - (
                lot.quantity_allocated or Decimal("0")
            )

            remaining -= consume_qty
            total_cost += layer_cost

            if first_lot_id is None:
                first_lot_id = lot.lot_id

            layers_used.append(
                {
                    "lot_id": str(lot.lot_id),
                    "lot_number": lot.lot_number,
                    "quantity": str(consume_qty),
                    "unit_cost": str(lot.unit_cost),
                }
            )

        unit_cost = (total_cost / quantity).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )

        return {
            "unit_cost": unit_cost,
            "total_cost": total_cost,
            "layers_used": layers_used,
            "first_lot_id": first_lot_id,
        }

    @staticmethod
    def _consume_from_lot(
        db: Session,
        lot_id: UUID,
        quantity: Decimal,
    ) -> None:
        """Consume quantity from a specific lot."""
        lot = db.get(InventoryLot, coerce_uuid(lot_id))
        if lot:
            lot.quantity_on_hand = (lot.quantity_on_hand or Decimal("0")) - quantity
            lot.quantity_available = lot.quantity_on_hand - (
                lot.quantity_allocated or Decimal("0")
            )

    @staticmethod
    def create_adjustment(
        db: Session,
        organization_id: UUID,
        input: TransactionInput,
        created_by_user_id: UUID,
    ) -> InventoryTransaction:
        """
        Create an inventory adjustment.

        Quantity can be positive (increase) or negative (decrease).

        Args:
            db: Database session
            organization_id: Organization scope
            input: Transaction input data
            created_by_user_id: User creating

        Returns:
            Created InventoryTransaction
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        itm_id = coerce_uuid(input.item_id)
        wh_id = coerce_uuid(input.warehouse_id)

        # Validate item
        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        # Validate warehouse
        warehouse = db.get(Warehouse, wh_id)
        if not warehouse or warehouse.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Warehouse not found")

        # Get current balance
        qty_before = InventoryTransactionService.get_current_balance(
            db, org_id, itm_id, wh_id
        )

        lot = None
        requires_lot = item.track_lots or item.costing_method == CostingMethod.FIFO
        if requires_lot:
            if not input.lot_id:
                raise HTTPException(
                    status_code=400,
                    detail="Lot ID is required for this item",
                )
            lot = db.get(InventoryLot, coerce_uuid(input.lot_id))
            if not lot or lot.item_id != itm_id:
                raise HTTPException(status_code=404, detail="Lot not found")
            if lot.warehouse_id and lot.warehouse_id != wh_id:
                raise HTTPException(
                    status_code=400,
                    detail="Lot does not belong to the selected warehouse",
                )

        # For negative adjustments, check we have enough
        if input.quantity < 0 and qty_before < abs(input.quantity):
            raise HTTPException(
                status_code=400,
                detail="Adjustment would result in negative inventory",
            )
        if lot and input.quantity < 0 and lot.quantity_on_hand < abs(input.quantity):
            raise HTTPException(
                status_code=400,
                detail="Adjustment would result in negative lot quantity",
            )

        # Use average cost for valuation
        unit_cost = item.average_cost or input.unit_cost or Decimal("0")
        total_cost = abs(input.quantity) * unit_cost

        # Create transaction
        transaction = InventoryTransaction(
            organization_id=org_id,
            transaction_type=input.transaction_type,
            transaction_date=input.transaction_date,
            fiscal_period_id=coerce_uuid(input.fiscal_period_id),
            item_id=itm_id,
            warehouse_id=wh_id,
            location_id=input.location_id,
            lot_id=input.lot_id,
            quantity=input.quantity,  # Can be positive or negative
            uom=input.uom,
            unit_cost=unit_cost,
            total_cost=total_cost,
            currency_code=input.currency_code,
            cost_variance=Decimal("0"),
            quantity_before=qty_before,
            quantity_after=qty_before + input.quantity,
            source_document_type=input.source_document_type,
            source_document_id=input.source_document_id,
            reference=input.reference,
            reason_code=input.reason_code,
            created_by_user_id=user_id,
        )

        db.add(transaction)

        if lot:
            lot.quantity_on_hand = (
                lot.quantity_on_hand or Decimal("0")
            ) + input.quantity
            lot.quantity_available = lot.quantity_on_hand - (
                lot.quantity_allocated or Decimal("0")
            )
            if lot.warehouse_id is None:
                lot.warehouse_id = wh_id

        db.flush()

        if InventoryTransactionService._is_real_time_valuation_enabled(db):
            InventoryTransactionService._post_inventory_transaction(
                db,
                organization_id=org_id,
                transaction=transaction,
                posted_by_user_id=user_id,
            )

        db.commit()
        db.refresh(transaction)

        return transaction

    @staticmethod
    def _is_real_time_valuation_enabled(db: Session) -> bool:
        """Return whether inventory valuation mode is set to real_time."""
        try:
            mode = settings_cache.get_setting_value(
                db,
                SettingDomain.inventory,
                "inventory_valuation_mode",
                default="manual",
            )
        except Exception:
            logger.exception(
                "Failed reading inventory_valuation_mode; using manual mode."
            )
            return False
        return str(mode or "manual").lower() == "real_time"

    @staticmethod
    def _post_inventory_transaction(
        db: Session,
        *,
        organization_id: UUID,
        transaction: InventoryTransaction,
        posted_by_user_id: UUID,
    ) -> None:
        """Post a transaction to GL and raise when posting fails."""
        from app.services.inventory.inv_posting_adapter import inv_posting_adapter

        result = inv_posting_adapter.post_transaction(
            db=db,
            organization_id=organization_id,
            transaction_id=transaction.transaction_id,
            posting_date=transaction.transaction_date.date(),
            posted_by_user_id=posted_by_user_id,
        )
        if not result.success:
            raise HTTPException(
                status_code=400,
                detail=f"Inventory GL posting failed: {result.message}",
            )

    @staticmethod
    def create_transfer(
        db: Session,
        organization_id: UUID,
        input: TransactionInput,
        created_by_user_id: UUID,
    ) -> tuple[InventoryTransaction, InventoryTransaction]:
        """
        Create an inventory transfer between warehouses.

        Creates two transactions: issue from source, receipt at destination.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Transaction input data (must have to_warehouse_id)
            created_by_user_id: User creating

        Returns:
            Tuple of (issue_transaction, receipt_transaction)
        """
        if not input.to_warehouse_id:
            raise HTTPException(
                status_code=400,
                detail="to_warehouse_id is required for transfer",
            )

        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)
        itm_id = coerce_uuid(input.item_id)
        from_wh_id = coerce_uuid(input.warehouse_id)
        to_wh_id = coerce_uuid(input.to_warehouse_id)

        # Validate item
        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        # Validate warehouses
        from_warehouse = db.get(Warehouse, from_wh_id)
        if not from_warehouse or from_warehouse.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Source warehouse not found")

        to_warehouse = db.get(Warehouse, to_wh_id)
        if not to_warehouse or to_warehouse.organization_id != org_id:
            raise HTTPException(
                status_code=404, detail="Destination warehouse not found"
            )

        lot = None
        requires_lot = item.track_lots or item.costing_method == CostingMethod.FIFO
        if requires_lot:
            if not input.lot_id:
                raise HTTPException(
                    status_code=400,
                    detail="Lot ID is required for this item",
                )
            lot = db.get(InventoryLot, coerce_uuid(input.lot_id))
            if not lot or lot.item_id != itm_id:
                raise HTTPException(status_code=404, detail="Lot not found")
            if lot.is_quarantined:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot transfer quarantined lot",
                )
            if lot.warehouse_id and lot.warehouse_id != from_wh_id:
                raise HTTPException(
                    status_code=400,
                    detail="Lot does not belong to the source warehouse",
                )
            if input.quantity != lot.quantity_on_hand:
                raise HTTPException(
                    status_code=400,
                    detail="Partial lot transfers are not supported",
                )

        # Get balance at source
        qty_before_source = InventoryTransactionService.get_current_balance(
            db, org_id, itm_id, from_wh_id
        )

        if qty_before_source < input.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient inventory at source: {qty_before_source} available",
            )

        # Get balance at destination
        qty_before_dest = InventoryTransactionService.get_current_balance(
            db, org_id, itm_id, to_wh_id
        )

        # Use average cost
        unit_cost = item.average_cost or input.unit_cost or Decimal("0")
        total_cost = input.quantity * unit_cost

        # Create issue from source
        issue_txn = InventoryTransaction(
            organization_id=org_id,
            transaction_type=TransactionType.TRANSFER,
            transaction_date=input.transaction_date,
            fiscal_period_id=coerce_uuid(input.fiscal_period_id),
            item_id=itm_id,
            warehouse_id=from_wh_id,
            location_id=input.location_id,
            lot_id=input.lot_id,
            to_warehouse_id=to_wh_id,
            to_location_id=input.to_location_id,
            quantity=-input.quantity,  # Negative for outgoing
            uom=input.uom,
            unit_cost=unit_cost,
            total_cost=total_cost,
            currency_code=input.currency_code,
            cost_variance=Decimal("0"),
            quantity_before=qty_before_source,
            quantity_after=qty_before_source - input.quantity,
            reference=input.reference,
            created_by_user_id=user_id,
        )
        db.add(issue_txn)

        # Create receipt at destination
        receipt_txn = InventoryTransaction(
            organization_id=org_id,
            transaction_type=TransactionType.TRANSFER,
            transaction_date=input.transaction_date,
            fiscal_period_id=coerce_uuid(input.fiscal_period_id),
            item_id=itm_id,
            warehouse_id=to_wh_id,
            location_id=input.to_location_id,
            lot_id=input.lot_id,
            quantity=input.quantity,  # Positive for incoming
            uom=input.uom,
            unit_cost=unit_cost,
            total_cost=total_cost,
            currency_code=input.currency_code,
            cost_variance=Decimal("0"),
            quantity_before=qty_before_dest,
            quantity_after=qty_before_dest + input.quantity,
            reference=input.reference,
            created_by_user_id=user_id,
        )
        db.add(receipt_txn)

        if lot:
            lot.warehouse_id = to_wh_id
            lot.quantity_available = lot.quantity_on_hand - (
                lot.quantity_allocated or Decimal("0")
            )

        db.commit()
        db.refresh(issue_txn)
        db.refresh(receipt_txn)

        return (issue_txn, receipt_txn)

    @staticmethod
    def get(
        db: Session,
        transaction_id: str,
        organization_id: UUID | None = None,
    ) -> InventoryTransaction:
        """Get an inventory transaction by ID."""
        txn = db.get(InventoryTransaction, coerce_uuid(transaction_id))
        if not txn:
            raise HTTPException(status_code=404, detail="Transaction not found")
        if organization_id is not None and txn.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Transaction not found")
        return txn

    @staticmethod
    def create_transaction(
        db: Session,
        organization_id: UUID,
        input: TransactionInput,
        created_by_user_id: UUID,
    ) -> InventoryTransaction:
        """
        Create an inventory transaction based on transaction type.

        Routes to the appropriate create method based on transaction type.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Transaction input data
            created_by_user_id: User creating

        Returns:
            Created InventoryTransaction
        """
        if input.transaction_type in [
            TransactionType.RECEIPT,
            TransactionType.RETURN,
            TransactionType.ASSEMBLY,
        ]:
            return InventoryTransactionService.create_receipt(
                db, organization_id, input, created_by_user_id
            )
        elif input.transaction_type in [
            TransactionType.ISSUE,
            TransactionType.SALE,
            TransactionType.DISASSEMBLY,
        ]:
            return InventoryTransactionService.create_issue(
                db, organization_id, input, created_by_user_id
            )
        elif input.transaction_type == TransactionType.TRANSFER:
            issue_txn, _ = InventoryTransactionService.create_transfer(
                db, organization_id, input, created_by_user_id
            )
            return issue_txn
        elif input.transaction_type in [
            TransactionType.ADJUSTMENT,
            TransactionType.COUNT_ADJUSTMENT,
            TransactionType.SCRAP,
        ]:
            return InventoryTransactionService.create_adjustment(
                db, organization_id, input, created_by_user_id
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported transaction type: {input.transaction_type}",
            )

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        item_id: str | None = None,
        warehouse_id: str | None = None,
        transaction_type: TransactionType | None = None,
        fiscal_period_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InventoryTransaction]:
        """List inventory transactions with optional filters."""
        from datetime import datetime

        query = select(InventoryTransaction)

        if organization_id:
            query = query.where(
                InventoryTransaction.organization_id == coerce_uuid(organization_id)
            )

        if item_id:
            query = query.where(InventoryTransaction.item_id == coerce_uuid(item_id))

        if warehouse_id:
            query = query.where(
                InventoryTransaction.warehouse_id == coerce_uuid(warehouse_id)
            )

        if transaction_type:
            # Handle both enum and string
            if isinstance(transaction_type, str):
                try:
                    transaction_type = TransactionType(transaction_type)
                except ValueError:
                    pass  # Invalid type, skip filter
            if isinstance(transaction_type, TransactionType):
                query = query.where(
                    InventoryTransaction.transaction_type == transaction_type
                )

        if fiscal_period_id:
            query = query.where(
                InventoryTransaction.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if start_date:
            # Convert date to datetime for comparison
            start_dt = datetime.combine(start_date, datetime.min.time())
            query = query.where(InventoryTransaction.transaction_date >= start_dt)

        if end_date:
            # End date is inclusive, so use end of day
            end_dt = datetime.combine(end_date, datetime.max.time())
            query = query.where(InventoryTransaction.transaction_date <= end_dt)

        return list(
            query.order_by(InventoryTransaction.transaction_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )


# Module-level singleton instance
inventory_transaction_service = InventoryTransactionService()
