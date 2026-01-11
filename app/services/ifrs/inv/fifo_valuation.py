"""
FIFOValuationService - FIFO Inventory Costing and Valuation.

Manages FIFO cost layers, inventory valuation, and NRV write-downs per IAS 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ifrs.inv.item import Item, CostingMethod
from app.models.ifrs.inv.inventory_valuation import InventoryValuation
from app.models.ifrs.inv.inventory_lot import InventoryLot
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class FIFOLayer:
    """A single FIFO cost layer."""

    layer_date: date
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    lot_id: Optional[UUID] = None
    reference: Optional[str] = None


@dataclass
class FIFOInventory:
    """FIFO inventory state for an item."""

    item_id: UUID
    layers: list[FIFOLayer] = field(default_factory=list)
    total_quantity: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    weighted_average_cost: Decimal = Decimal("0")


@dataclass
class ConsumptionResult:
    """Result of consuming inventory."""

    quantity_consumed: Decimal
    total_cost: Decimal
    cost_layers_used: list[dict]
    remaining_quantity: Decimal


@dataclass
class NRVCalculation:
    """NRV calculation for an item."""

    item_id: UUID
    cost: Decimal
    estimated_selling_price: Decimal
    costs_to_complete: Decimal
    selling_costs: Decimal
    nrv: Decimal
    carrying_amount: Decimal
    write_down: Decimal


class FIFOValuationService(ListResponseMixin):
    """
    Service for FIFO inventory costing and IAS 2 valuation.

    Manages FIFO cost layers, consumption, and NRV write-downs.
    """

    @staticmethod
    def add_inventory_layer(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        quantity: Decimal,
        unit_cost: Decimal,
        layer_date: date,
        lot_id: Optional[UUID] = None,
        reference: Optional[str] = None,
    ) -> InventoryLot:
        """
        Add a new FIFO cost layer (via lot creation).

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item ID
            warehouse_id: Warehouse ID
            quantity: Quantity received
            unit_cost: Unit cost
            layer_date: Date of receipt
            lot_id: Optional lot ID
            reference: Reference document

        Returns:
            Created InventoryLot representing the layer
        """
        org_id = coerce_uuid(organization_id)
        item_id = coerce_uuid(item_id)

        # Validate item
        item = db.query(Item).filter(
            Item.item_id == item_id,
            Item.organization_id == org_id,
        ).first()

        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        # Create a lot to represent the FIFO layer
        lot_number = f"FIFO-{layer_date.strftime('%Y%m%d')}-{uuid_lib.uuid4().hex[:8]}"

        lot = InventoryLot(
            organization_id=org_id,
            item_id=item_id,
            warehouse_id=coerce_uuid(warehouse_id),
            lot_number=lot_number,
            received_date=layer_date,
            unit_cost=unit_cost,
            initial_quantity=quantity,
            quantity_on_hand=quantity,
            quantity_allocated=Decimal("0"),
            quantity_available=quantity,
        )

        db.add(lot)

        # Update item average cost
        total_on_hand = db.query(func.sum(InventoryLot.quantity_on_hand)).filter(
            InventoryLot.organization_id == org_id,
            InventoryLot.item_id == item_id,
            InventoryLot.quantity_on_hand > 0,
        ).scalar() or Decimal("0")

        total_value = db.query(
            func.sum(InventoryLot.quantity_on_hand * InventoryLot.unit_cost)
        ).filter(
            InventoryLot.organization_id == org_id,
            InventoryLot.item_id == item_id,
            InventoryLot.quantity_on_hand > 0,
        ).scalar() or Decimal("0")

        new_total_qty = total_on_hand + quantity
        new_total_value = total_value + (quantity * unit_cost)

        if new_total_qty > 0:
            item.average_cost = (new_total_value / new_total_qty).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )

        item.last_purchase_cost = unit_cost

        db.commit()
        db.refresh(lot)

        return lot

    @staticmethod
    def consume_inventory_fifo(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        quantity: Decimal,
    ) -> ConsumptionResult:
        """
        Consume inventory using FIFO method.

        Consumes from oldest layers first.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item ID
            quantity: Quantity to consume

        Returns:
            ConsumptionResult with cost details
        """
        org_id = coerce_uuid(organization_id)
        item_id = coerce_uuid(item_id)

        # Get layers ordered by received date (oldest first)
        layers = db.query(InventoryLot).filter(
            InventoryLot.organization_id == org_id,
            InventoryLot.item_id == item_id,
            InventoryLot.quantity_on_hand > 0,
            InventoryLot.is_active == True,
        ).order_by(InventoryLot.received_date.asc()).all()

        total_available = sum(l.quantity_on_hand for l in layers)

        if total_available < quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient inventory. Available: {total_available}, Requested: {quantity}"
            )

        remaining_to_consume = quantity
        total_cost = Decimal("0")
        layers_used = []

        for layer in layers:
            if remaining_to_consume <= 0:
                break

            consume_from_layer = min(layer.quantity_on_hand, remaining_to_consume)
            layer_cost = consume_from_layer * layer.unit_cost

            layer.quantity_on_hand -= consume_from_layer
            layer.quantity_available = layer.quantity_on_hand - layer.quantity_allocated

            remaining_to_consume -= consume_from_layer
            total_cost += layer_cost

            layers_used.append({
                "lot_id": str(layer.lot_id),
                "lot_number": layer.lot_number,
                "quantity": str(consume_from_layer),
                "unit_cost": str(layer.unit_cost),
                "total_cost": str(layer_cost),
            })

        db.commit()

        return ConsumptionResult(
            quantity_consumed=quantity,
            total_cost=total_cost,
            cost_layers_used=layers_used,
            remaining_quantity=total_available - quantity,
        )

    @staticmethod
    def get_fifo_inventory(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
    ) -> FIFOInventory:
        """
        Get current FIFO inventory state for an item.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item ID

        Returns:
            FIFOInventory with layers
        """
        item_id = coerce_uuid(item_id)

        org_id = coerce_uuid(organization_id)
        layers_data = db.query(InventoryLot).filter(
            InventoryLot.organization_id == org_id,
            InventoryLot.item_id == item_id,
            InventoryLot.quantity_on_hand > 0,
            InventoryLot.is_active == True,
        ).order_by(InventoryLot.received_date.asc()).all()

        layers = []
        total_qty = Decimal("0")
        total_cost = Decimal("0")

        for lot in layers_data:
            layer = FIFOLayer(
                layer_date=lot.received_date,
                quantity=lot.quantity_on_hand,
                unit_cost=lot.unit_cost,
                total_cost=lot.quantity_on_hand * lot.unit_cost,
                lot_id=lot.lot_id,
                reference=lot.lot_number,
            )
            layers.append(layer)
            total_qty += lot.quantity_on_hand
            total_cost += layer.total_cost

        avg_cost = (total_cost / total_qty) if total_qty > 0 else Decimal("0")

        return FIFOInventory(
            item_id=item_id,
            layers=layers,
            total_quantity=total_qty,
            total_cost=total_cost,
            weighted_average_cost=avg_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
        )

    @staticmethod
    def calculate_nrv(
        estimated_selling_price: Decimal,
        costs_to_complete: Decimal,
        selling_costs: Decimal,
    ) -> Decimal:
        """
        Calculate Net Realizable Value per IAS 2.

        NRV = Estimated selling price - Costs to complete - Selling costs

        Args:
            estimated_selling_price: Expected selling price
            costs_to_complete: Costs to finish goods
            selling_costs: Costs to make the sale

        Returns:
            Net Realizable Value
        """
        return estimated_selling_price - costs_to_complete - selling_costs

    @staticmethod
    def calculate_write_down(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        fiscal_period_id: UUID,
        valuation_date: date,
        estimated_selling_price: Decimal,
        costs_to_complete: Decimal = Decimal("0"),
        selling_costs: Decimal = Decimal("0"),
    ) -> NRVCalculation:
        """
        Calculate NRV write-down for an item per IAS 2.

        Inventories must be measured at the lower of cost and NRV.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item ID
            warehouse_id: Warehouse ID
            fiscal_period_id: Fiscal period
            valuation_date: Valuation date
            estimated_selling_price: Expected selling price
            costs_to_complete: Costs to complete
            selling_costs: Selling costs

        Returns:
            NRVCalculation with write-down amount
        """
        org_id = coerce_uuid(organization_id)
        item_id = coerce_uuid(item_id)
        warehouse_id = coerce_uuid(warehouse_id)

        # Get current inventory state
        fifo_inv = FIFOValuationService.get_fifo_inventory(db, org_id, item_id)

        if fifo_inv.total_quantity <= 0:
            return NRVCalculation(
                item_id=item_id,
                cost=Decimal("0"),
                estimated_selling_price=estimated_selling_price,
                costs_to_complete=costs_to_complete,
                selling_costs=selling_costs,
                nrv=Decimal("0"),
                carrying_amount=Decimal("0"),
                write_down=Decimal("0"),
            )

        # Calculate NRV
        nrv = FIFOValuationService.calculate_nrv(
            estimated_selling_price, costs_to_complete, selling_costs
        )

        # Calculate per-unit values
        unit_cost = fifo_inv.weighted_average_cost
        unit_nrv = nrv

        # Lower of cost and NRV
        if unit_nrv < unit_cost:
            # Write-down required
            write_down_per_unit = unit_cost - unit_nrv
            total_write_down = write_down_per_unit * fifo_inv.total_quantity
            carrying_amount = unit_nrv * fifo_inv.total_quantity
        else:
            # No write-down
            total_write_down = Decimal("0")
            carrying_amount = fifo_inv.total_cost

        return NRVCalculation(
            item_id=item_id,
            cost=fifo_inv.total_cost,
            estimated_selling_price=estimated_selling_price,
            costs_to_complete=costs_to_complete,
            selling_costs=selling_costs,
            nrv=nrv * fifo_inv.total_quantity,
            carrying_amount=carrying_amount,
            write_down=total_write_down,
        )

    @staticmethod
    def create_valuation_record(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
        warehouse_id: UUID,
        fiscal_period_id: UUID,
        valuation_date: date,
        nrv_calc: NRVCalculation,
        currency_code: str = "USD",
    ) -> InventoryValuation:
        """
        Create an inventory valuation record.

        Args:
            db: Database session
            organization_id: Organization scope
            item_id: Item ID
            warehouse_id: Warehouse ID
            fiscal_period_id: Fiscal period
            valuation_date: Valuation date
            nrv_calc: NRV calculation result
            currency_code: Currency code

        Returns:
            Created InventoryValuation
        """
        org_id = coerce_uuid(organization_id)
        item_id = coerce_uuid(item_id)
        warehouse_id = coerce_uuid(warehouse_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Get item details
        item = db.query(Item).filter(Item.item_id == item_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="Item not found")

        # Get FIFO inventory
        fifo_inv = FIFOValuationService.get_fifo_inventory(db, org_id, item_id)

        # Check for existing valuation
        existing = db.query(InventoryValuation).filter(
            InventoryValuation.fiscal_period_id == period_id,
            InventoryValuation.item_id == item_id,
            InventoryValuation.warehouse_id == warehouse_id,
        ).first()

        if existing:
            # Update existing
            existing.quantity_on_hand = fifo_inv.total_quantity
            existing.unit_cost = fifo_inv.weighted_average_cost
            existing.total_cost = fifo_inv.total_cost
            existing.estimated_selling_price = nrv_calc.estimated_selling_price
            existing.estimated_costs_to_complete = nrv_calc.costs_to_complete
            existing.estimated_selling_costs = nrv_calc.selling_costs
            existing.net_realizable_value = nrv_calc.nrv
            existing.carrying_amount = nrv_calc.carrying_amount
            existing.write_down_amount = nrv_calc.write_down
            existing.functional_currency_amount = nrv_calc.carrying_amount
            db.commit()
            db.refresh(existing)
            return existing

        # Create new
        valuation = InventoryValuation(
            organization_id=org_id,
            fiscal_period_id=period_id,
            valuation_date=valuation_date,
            item_id=item_id,
            warehouse_id=warehouse_id,
            quantity_on_hand=fifo_inv.total_quantity,
            uom=item.base_uom,
            unit_cost=fifo_inv.weighted_average_cost,
            total_cost=fifo_inv.total_cost,
            costing_method=item.costing_method.value,
            estimated_selling_price=nrv_calc.estimated_selling_price,
            estimated_costs_to_complete=nrv_calc.costs_to_complete,
            estimated_selling_costs=nrv_calc.selling_costs,
            net_realizable_value=nrv_calc.nrv,
            carrying_amount=nrv_calc.carrying_amount,
            write_down_amount=nrv_calc.write_down,
            currency_code=currency_code,
            functional_currency_amount=nrv_calc.carrying_amount,
        )

        db.add(valuation)
        db.commit()
        db.refresh(valuation)

        return valuation

    @staticmethod
    def get_valuation_summary(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
    ) -> dict:
        """
        Get valuation summary for a period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period

        Returns:
            Summary dictionary
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        valuations = db.query(InventoryValuation).filter(
            InventoryValuation.organization_id == org_id,
            InventoryValuation.fiscal_period_id == period_id,
        ).all()

        total_cost = sum(v.total_cost for v in valuations)
        total_carrying = sum(v.carrying_amount for v in valuations)
        total_write_down = sum(v.write_down_amount for v in valuations)

        return {
            "fiscal_period_id": str(period_id),
            "item_count": len(valuations),
            "total_cost": str(total_cost),
            "total_carrying_amount": str(total_carrying),
            "total_write_down": str(total_write_down),
            "write_down_percentage": str(
                (total_write_down / total_cost * 100) if total_cost > 0 else Decimal("0")
            ),
        }


# Module-level instance
fifo_valuation_service = FIFOValuationService()
