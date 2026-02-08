"""
BOMService - Bill of Materials management and assembly processing.

Manages BOMs and processes assembly/disassembly transactions.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.inventory.bom import BillOfMaterials, BOMComponent, BOMType
from app.models.inventory.inventory_transaction import TransactionType
from app.models.inventory.item import Item
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class BOMInput:
    """Input for creating a BOM."""

    bom_code: str
    bom_name: str
    item_id: UUID
    output_quantity: Decimal
    output_uom: str
    bom_type: BOMType = BOMType.ASSEMBLY
    description: str | None = None
    is_default: bool = True


@dataclass
class BOMComponentInput:
    """Input for adding a component to a BOM."""

    component_item_id: UUID
    quantity: Decimal
    uom: str
    scrap_percent: Decimal = Decimal("0")
    line_number: int = 1
    warehouse_id: UUID | None = None


@dataclass
class AssemblyInput:
    """Input for assembly/disassembly operation."""

    bom_id: UUID
    warehouse_id: UUID
    quantity: Decimal  # Number of finished goods to produce
    fiscal_period_id: UUID
    transaction_date: datetime
    reference: str | None = None


@dataclass
class AssemblyResult:
    """Result of assembly operation."""

    assembly_transaction_id: UUID
    component_transactions: list[UUID]
    output_item_id: UUID
    output_quantity: Decimal
    total_component_cost: Decimal
    unit_cost: Decimal


class BOMService(ListResponseMixin):
    """
    Service for BOM management and assembly processing.

    Handles BOM creation, component management, and assembly/disassembly.
    """

    @staticmethod
    def create_bom(
        db: Session,
        organization_id: UUID,
        input: BOMInput,
    ) -> BillOfMaterials:
        """
        Create a new Bill of Materials.

        Args:
            db: Database session
            organization_id: Organization scope
            input: BOM input data

        Returns:
            Created BillOfMaterials
        """
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(input.item_id)

        # Check for duplicate code
        existing = (
            db.query(BillOfMaterials)
            .filter(
                and_(
                    BillOfMaterials.organization_id == org_id,
                    BillOfMaterials.bom_code == input.bom_code,
                )
            )
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"BOM code '{input.bom_code}' already exists",
            )

        # Validate item
        item = db.get(Item, itm_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Item not found")

        # If setting as default, clear other defaults for this item
        if input.is_default:
            db.query(BillOfMaterials).filter(
                and_(
                    BillOfMaterials.organization_id == org_id,
                    BillOfMaterials.item_id == itm_id,
                    BillOfMaterials.is_default == True,
                )
            ).update({"is_default": False})

        bom = BillOfMaterials(
            organization_id=org_id,
            bom_code=input.bom_code,
            bom_name=input.bom_name,
            description=input.description,
            item_id=itm_id,
            bom_type=input.bom_type,
            output_quantity=input.output_quantity,
            output_uom=input.output_uom,
            is_default=input.is_default,
            is_active=True,
        )

        db.add(bom)
        db.commit()
        db.refresh(bom)

        return bom

    @staticmethod
    def add_component(
        db: Session,
        organization_id: UUID,
        bom_id: UUID,
        input: BOMComponentInput,
    ) -> BOMComponent:
        """
        Add a component to a BOM.

        Args:
            db: Database session
            organization_id: Organization scope
            bom_id: BOM ID
            input: Component input data

        Returns:
            Created BOMComponent
        """
        org_id = coerce_uuid(organization_id)
        b_id = coerce_uuid(bom_id)
        comp_id = coerce_uuid(input.component_item_id)

        # Validate BOM
        bom = db.get(BillOfMaterials, b_id)
        if not bom or bom.organization_id != org_id:
            raise HTTPException(status_code=404, detail="BOM not found")

        # Validate component item
        item = db.get(Item, comp_id)
        if not item or item.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Component item not found")

        # Check for circular reference
        if comp_id == bom.item_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot add output item as its own component",
            )

        component = BOMComponent(
            bom_id=b_id,
            component_item_id=comp_id,
            quantity=input.quantity,
            uom=input.uom,
            scrap_percent=input.scrap_percent,
            line_number=input.line_number,
            warehouse_id=coerce_uuid(input.warehouse_id)
            if input.warehouse_id
            else None,
            is_active=True,
        )

        db.add(component)
        db.commit()
        db.refresh(component)

        return component

    @staticmethod
    def process_assembly(
        db: Session,
        organization_id: UUID,
        input: AssemblyInput,
        created_by_user_id: UUID,
    ) -> AssemblyResult:
        """
        Process an assembly operation.

        Issues component items and receives finished goods.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Assembly input data
            created_by_user_id: User creating

        Returns:
            AssemblyResult with transaction details
        """
        from app.services.inventory.balance import inventory_balance_service
        from app.services.inventory.transaction import (
            TransactionInput,
            inventory_transaction_service,
        )

        org_id = coerce_uuid(organization_id)
        b_id = coerce_uuid(input.bom_id)
        wh_id = coerce_uuid(input.warehouse_id)
        user_id = coerce_uuid(created_by_user_id)

        # Get BOM with components
        bom = db.get(BillOfMaterials, b_id)
        if not bom or bom.organization_id != org_id:
            raise HTTPException(status_code=404, detail="BOM not found")

        if not bom.is_active:
            raise HTTPException(status_code=400, detail="BOM is not active")

        components = (
            db.query(BOMComponent)
            .filter(
                and_(
                    BOMComponent.bom_id == b_id,
                    BOMComponent.is_active == True,
                )
            )
            .all()
        )

        if not components:
            raise HTTPException(status_code=400, detail="BOM has no components")

        # Calculate multiplier (how many BOM batches needed)
        multiplier = input.quantity / bom.output_quantity

        # Check component availability and calculate costs
        total_component_cost = Decimal("0")
        component_issues = []

        for comp in components:
            comp_item = db.get(Item, comp.component_item_id)
            if not comp_item:
                raise HTTPException(
                    status_code=400,
                    detail=f"Component item not found: {comp.component_item_id}",
                )

            # Calculate required quantity (including scrap)
            required_qty = comp.quantity * multiplier
            if comp.scrap_percent > 0:
                required_qty = required_qty * (1 + comp.scrap_percent / 100)
            required_qty = required_qty.quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )

            # Check availability
            available = inventory_balance_service.get_available(
                db=db,
                organization_id=org_id,
                item_id=comp.component_item_id,
                warehouse_id=comp.warehouse_id or wh_id,
            )

            if available < required_qty:
                raise HTTPException(
                    status_code=400,
                    detail=f"Insufficient {comp_item.item_code}: need {required_qty}, have {available}",
                )

            # Get component cost
            unit_cost = (
                comp_item.average_cost or comp_item.standard_cost or Decimal("0")
            )
            total_component_cost += required_qty * unit_cost

            component_issues.append(
                {
                    "item": comp_item,
                    "component": comp,
                    "quantity": required_qty,
                    "warehouse_id": comp.warehouse_id or wh_id,
                    "unit_cost": unit_cost,
                }
            )

        # Issue components
        component_txn_ids = []
        for issue in component_issues:
            comp_item = cast(Item, issue["item"])
            comp_component = cast(BOMComponent, issue["component"])
            comp_quantity = cast(Decimal, issue["quantity"])
            comp_unit_cost = cast(Decimal, issue["unit_cost"])
            comp_warehouse_id = cast(UUID, issue["warehouse_id"])
            txn_input = TransactionInput(
                transaction_type=TransactionType.DISASSEMBLY,  # Component consumption
                transaction_date=input.transaction_date,
                fiscal_period_id=input.fiscal_period_id,
                item_id=comp_item.item_id,
                warehouse_id=comp_warehouse_id,
                quantity=comp_quantity,
                unit_cost=comp_unit_cost,
                uom=comp_component.uom,
                currency_code=comp_item.currency_code,
                source_document_type="ASSEMBLY",
                source_document_id=bom.bom_id,
                reference=input.reference or f"Assembly: {bom.bom_code}",
            )

            txn = inventory_transaction_service.create_issue(
                db=db,
                organization_id=org_id,
                input=txn_input,
                created_by_user_id=user_id,
            )
            component_txn_ids.append(txn.transaction_id)

        # Calculate unit cost for finished goods
        finished_unit_cost = (total_component_cost / input.quantity).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )

        # Get finished goods item
        finished_item = db.get(Item, bom.item_id)
        if not finished_item:
            raise HTTPException(status_code=404, detail="Finished goods item not found")

        # Receive finished goods
        assembly_input = TransactionInput(
            transaction_type=TransactionType.ASSEMBLY,
            transaction_date=input.transaction_date,
            fiscal_period_id=input.fiscal_period_id,
            item_id=bom.item_id,
            warehouse_id=wh_id,
            quantity=input.quantity,
            unit_cost=finished_unit_cost,
            uom=bom.output_uom,
            currency_code=finished_item.currency_code,
            source_document_type="ASSEMBLY",
            source_document_id=bom.bom_id,
            reference=input.reference or f"Assembly: {bom.bom_code}",
        )

        assembly_txn = inventory_transaction_service.create_receipt(
            db=db,
            organization_id=org_id,
            input=assembly_input,
            created_by_user_id=user_id,
        )

        return AssemblyResult(
            assembly_transaction_id=assembly_txn.transaction_id,
            component_transactions=component_txn_ids,
            output_item_id=bom.item_id,
            output_quantity=input.quantity,
            total_component_cost=total_component_cost,
            unit_cost=finished_unit_cost,
        )

    @staticmethod
    def process_disassembly(
        db: Session,
        organization_id: UUID,
        input: AssemblyInput,
        created_by_user_id: UUID,
    ) -> AssemblyResult:
        """
        Process a disassembly operation (reverse assembly).

        Issues finished goods and receives components back.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Assembly input data
            created_by_user_id: User creating

        Returns:
            AssemblyResult with transaction details
        """
        from app.services.inventory.balance import inventory_balance_service
        from app.services.inventory.transaction import (
            TransactionInput,
            inventory_transaction_service,
        )

        org_id = coerce_uuid(organization_id)
        b_id = coerce_uuid(input.bom_id)
        wh_id = coerce_uuid(input.warehouse_id)
        user_id = coerce_uuid(created_by_user_id)

        # Get BOM
        bom = db.get(BillOfMaterials, b_id)
        if not bom or bom.organization_id != org_id:
            raise HTTPException(status_code=404, detail="BOM not found")

        # Check finished goods availability
        available = inventory_balance_service.get_available(
            db=db,
            organization_id=org_id,
            item_id=bom.item_id,
            warehouse_id=wh_id,
        )

        if available < input.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient finished goods: need {input.quantity}, have {available}",
            )

        finished_item = db.get(Item, bom.item_id)
        if not finished_item:
            raise HTTPException(status_code=404, detail="Finished goods item not found")
        finished_unit_cost = finished_item.average_cost or Decimal("0")

        # Issue finished goods
        disassembly_input = TransactionInput(
            transaction_type=TransactionType.DISASSEMBLY,
            transaction_date=input.transaction_date,
            fiscal_period_id=input.fiscal_period_id,
            item_id=bom.item_id,
            warehouse_id=wh_id,
            quantity=input.quantity,
            unit_cost=finished_unit_cost,
            uom=bom.output_uom,
            currency_code=finished_item.currency_code,
            source_document_type="DISASSEMBLY",
            source_document_id=bom.bom_id,
            reference=input.reference or f"Disassembly: {bom.bom_code}",
        )

        disassembly_txn = inventory_transaction_service.create_issue(
            db=db,
            organization_id=org_id,
            input=disassembly_input,
            created_by_user_id=user_id,
        )

        # Calculate component receipts
        components = (
            db.query(BOMComponent)
            .filter(
                and_(
                    BOMComponent.bom_id == b_id,
                    BOMComponent.is_active == True,
                )
            )
            .all()
        )

        multiplier = input.quantity / bom.output_quantity
        total_component_cost = Decimal("0")
        component_txn_ids = []

        for comp in components:
            comp_item = db.get(Item, comp.component_item_id)
            if not comp_item:
                continue

            # Calculate recovered quantity (no scrap recovery)
            recovered_qty = (comp.quantity * multiplier).quantize(
                Decimal("0.000001"), rounding=ROUND_HALF_UP
            )

            # Allocate cost proportionally
            comp_cost = (finished_unit_cost * input.quantity * comp.quantity) / sum(
                c.quantity for c in components
            )
            unit_cost = (
                (comp_cost / recovered_qty).quantize(
                    Decimal("0.000001"), rounding=ROUND_HALF_UP
                )
                if recovered_qty > 0
                else Decimal("0")
            )

            total_component_cost += recovered_qty * unit_cost

            # Receive component back
            comp_input = TransactionInput(
                transaction_type=TransactionType.RETURN,
                transaction_date=input.transaction_date,
                fiscal_period_id=input.fiscal_period_id,
                item_id=comp.component_item_id,
                warehouse_id=comp.warehouse_id or wh_id,
                quantity=recovered_qty,
                unit_cost=unit_cost,
                uom=comp.uom,
                currency_code=comp_item.currency_code,
                source_document_type="DISASSEMBLY",
                source_document_id=bom.bom_id,
                reference=input.reference or f"Disassembly: {bom.bom_code}",
            )

            txn = inventory_transaction_service.create_receipt(
                db=db,
                organization_id=org_id,
                input=comp_input,
                created_by_user_id=user_id,
            )
            component_txn_ids.append(txn.transaction_id)

        return AssemblyResult(
            assembly_transaction_id=disassembly_txn.transaction_id,
            component_transactions=component_txn_ids,
            output_item_id=bom.item_id,
            output_quantity=input.quantity,
            total_component_cost=total_component_cost,
            unit_cost=finished_unit_cost,
        )

    @staticmethod
    def get(
        db: Session,
        bom_id: str,
    ) -> BillOfMaterials:
        """Get a BOM by ID."""
        bom = db.get(BillOfMaterials, coerce_uuid(bom_id))
        if not bom:
            raise HTTPException(status_code=404, detail="BOM not found")
        return bom

    @staticmethod
    def get_default_for_item(
        db: Session,
        organization_id: UUID,
        item_id: UUID,
    ) -> BillOfMaterials | None:
        """Get the default BOM for an item."""
        org_id = coerce_uuid(organization_id)
        itm_id = coerce_uuid(item_id)

        return (
            db.query(BillOfMaterials)
            .filter(
                and_(
                    BillOfMaterials.organization_id == org_id,
                    BillOfMaterials.item_id == itm_id,
                    BillOfMaterials.is_default == True,
                    BillOfMaterials.is_active == True,
                )
            )
            .first()
        )

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        item_id: str | None = None,
        bom_type: BOMType | None = None,
        is_active: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[BillOfMaterials]:
        """List BOMs with optional filters."""
        query = db.query(BillOfMaterials)

        if organization_id:
            query = query.filter(
                BillOfMaterials.organization_id == coerce_uuid(organization_id)
            )

        if item_id:
            query = query.filter(BillOfMaterials.item_id == coerce_uuid(item_id))

        if bom_type:
            query = query.filter(BillOfMaterials.bom_type == bom_type)

        if is_active is not None:
            query = query.filter(BillOfMaterials.is_active == is_active)

        query = query.order_by(BillOfMaterials.bom_code)
        return query.limit(limit).offset(offset).all()

    @staticmethod
    def list_components(
        db: Session,
        bom_id: str,
    ) -> builtins.list[BOMComponent]:
        """List components for a BOM."""
        b_id = coerce_uuid(bom_id)

        return (
            db.query(BOMComponent)
            .filter(BOMComponent.bom_id == b_id)
            .order_by(BOMComponent.line_number)
            .all()
        )


# Module-level singleton instance
bom_service = BOMService()
