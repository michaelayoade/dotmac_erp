"""
INV API Router.

Inventory API endpoints for item management, transactions, and costing.
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.schemas.ifrs.common import ListResponse, PostingResultSchema
from app.services.ifrs.inv import (
    item_service,
    inventory_transaction_service,
    inv_posting_adapter,
    ItemInput,
    TransactionInput,
)


router = APIRouter(prefix="/inv", tags=["inventory"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# =============================================================================
# Schemas
# =============================================================================

class InventoryItemCreate(BaseModel):
    """Create inventory item request."""

    item_code: str = Field(max_length=30)
    item_name: str = Field(max_length=200)
    item_category_id: Optional[UUID] = None
    unit_of_measure: str = Field(max_length=20)
    costing_method: str = "WEIGHTED_AVERAGE"
    standard_cost: Optional[Decimal] = None
    reorder_point: Optional[Decimal] = None
    reorder_quantity: Optional[Decimal] = None
    inventory_account_id: Optional[UUID] = None
    cogs_account_id: Optional[UUID] = None
    description: Optional[str] = None


class InventoryItemRead(BaseModel):
    """Inventory item response."""

    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    organization_id: UUID
    item_code: str
    item_name: str
    unit_of_measure: str
    costing_method: str
    standard_cost: Optional[Decimal]
    current_cost: Decimal
    quantity_on_hand: Decimal
    quantity_reserved: Decimal
    quantity_available: Decimal
    is_active: bool


class TransactionCreate(BaseModel):
    """Create inventory transaction request."""

    item_id: UUID
    warehouse_id: UUID
    transaction_type: str = Field(max_length=30)
    transaction_date: date
    quantity: Decimal
    unit_cost: Optional[Decimal] = None
    reference_type: Optional[str] = None
    reference_id: Optional[UUID] = None
    notes: Optional[str] = None


class TransactionRead(BaseModel):
    """Inventory transaction response."""

    model_config = ConfigDict(from_attributes=True)

    transaction_id: UUID
    organization_id: UUID
    item_id: UUID
    warehouse_id: UUID
    transaction_type: str
    transaction_date: date
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    running_quantity: Decimal


class CostingResultRead(BaseModel):
    """Costing calculation result."""

    item_id: UUID
    item_code: str
    costing_method: str
    quantity_on_hand: Decimal
    total_cost: Decimal
    average_cost: Decimal


class InventoryValuationRead(BaseModel):
    """Inventory valuation report."""

    as_of_date: date
    total_items: int
    total_quantity: Decimal
    total_value: Decimal
    items: list[CostingResultRead]


# =============================================================================
# Inventory Items
# =============================================================================

@router.post("/items", response_model=InventoryItemRead, status_code=status.HTTP_201_CREATED)
def create_inventory_item(
    payload: InventoryItemCreate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new inventory item."""
    input_data = ItemInput(
        item_code=payload.item_code,
        item_name=payload.item_name,
        item_category_id=payload.item_category_id,
        unit_of_measure=payload.unit_of_measure,
        costing_method=payload.costing_method,
        standard_cost=payload.standard_cost,
        reorder_point=payload.reorder_point,
        reorder_quantity=payload.reorder_quantity,
        inventory_account_id=payload.inventory_account_id,
        cogs_account_id=payload.cogs_account_id,
        description=payload.description,
    )
    return item_service.create_item(db, organization_id, input_data)


@router.get("/items/{item_id}", response_model=InventoryItemRead)
def get_inventory_item(
    item_id: UUID,
    db: Session = Depends(get_db),
):
    """Get an inventory item by ID."""
    return item_service.get(db, str(item_id))


@router.get("/items", response_model=ListResponse[InventoryItemRead])
def list_inventory_items(
    organization_id: UUID = Query(...),
    item_category_id: Optional[UUID] = None,
    costing_method: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List inventory items with filters."""
    items = item_service.list(
        db=db,
        organization_id=str(organization_id),
        item_category_id=str(item_category_id) if item_category_id else None,
        costing_method=costing_method,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=items,
        count=len(items),
        limit=limit,
        offset=offset,
    )


# =============================================================================
# Inventory Transactions
# =============================================================================

@router.post("/transactions", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
def create_inventory_transaction(
    payload: TransactionCreate,
    organization_id: UUID = Query(...),
    created_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create an inventory transaction."""
    input_data = TransactionInput(
        item_id=payload.item_id,
        warehouse_id=payload.warehouse_id,
        transaction_type=payload.transaction_type,
        transaction_date=payload.transaction_date,
        quantity=payload.quantity,
        unit_cost=payload.unit_cost,
        reference_type=payload.reference_type,
        reference_id=payload.reference_id,
        notes=payload.notes,
    )
    return inventory_transaction_service.create_transaction(
        db=db,
        organization_id=organization_id,
        input=input_data,
        created_by_user_id=created_by_user_id,
    )


@router.get("/transactions/{transaction_id}", response_model=TransactionRead)
def get_inventory_transaction(
    transaction_id: UUID,
    db: Session = Depends(get_db),
):
    """Get an inventory transaction by ID."""
    return inventory_transaction_service.get(db, str(transaction_id))


@router.get("/transactions", response_model=ListResponse[TransactionRead])
def list_inventory_transactions(
    organization_id: UUID = Query(...),
    item_id: Optional[UUID] = None,
    warehouse_id: Optional[UUID] = None,
    transaction_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List inventory transactions with filters."""
    transactions = inventory_transaction_service.list(
        db=db,
        organization_id=str(organization_id),
        item_id=str(item_id) if item_id else None,
        warehouse_id=str(warehouse_id) if warehouse_id else None,
        transaction_type=transaction_type,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=transactions,
        count=len(transactions),
        limit=limit,
        offset=offset,
    )


@router.post("/transactions/{transaction_id}/post", response_model=PostingResultSchema)
def post_inventory_transaction(
    transaction_id: UUID,
    posting_date: date = Query(...),
    organization_id: UUID = Query(...),
    posted_by_user_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Post inventory transaction to GL."""
    result = inv_posting_adapter.post_transaction(
        db=db,
        organization_id=organization_id,
        transaction_id=transaction_id,
        posting_date=posting_date,
        posted_by_user_id=posted_by_user_id,
    )
    return PostingResultSchema(
        success=result.success,
        journal_entry_id=result.journal_entry_id,
        entry_number=result.entry_number,
        message=result.message,
    )


# =============================================================================
# FIFO Valuation (IAS 2)
# =============================================================================

from app.services.ifrs.inv import fifo_valuation_service


class FIFOLayerRead(BaseModel):
    """FIFO layer response."""
    layer_date: date
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    lot_id: Optional[UUID] = None
    reference: Optional[str] = None


class FIFOInventoryRead(BaseModel):
    """FIFO inventory state."""
    item_id: UUID
    layers: list[FIFOLayerRead]
    total_quantity: Decimal
    total_cost: Decimal
    weighted_average_cost: Decimal


class ConsumptionResultRead(BaseModel):
    """Consumption result."""
    quantity_consumed: Decimal
    total_cost: Decimal
    cost_layers_used: list[dict]
    remaining_quantity: Decimal


class NRVCalculationRead(BaseModel):
    """NRV calculation result."""
    item_id: UUID
    cost: Decimal
    estimated_selling_price: Decimal
    costs_to_complete: Decimal
    selling_costs: Decimal
    nrv: Decimal
    carrying_amount: Decimal
    write_down: Decimal


class AddLayerCreate(BaseModel):
    """Add FIFO layer input."""
    item_id: UUID
    warehouse_id: UUID
    quantity: Decimal
    unit_cost: Decimal
    layer_date: date
    lot_id: Optional[UUID] = None
    reference: Optional[str] = None


@router.post("/fifo/add-layer", status_code=status.HTTP_201_CREATED)
def add_fifo_layer(
    payload: AddLayerCreate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Add a new FIFO cost layer."""
    return fifo_valuation_service.add_inventory_layer(
        db=db,
        organization_id=organization_id,
        item_id=payload.item_id,
        warehouse_id=payload.warehouse_id,
        quantity=payload.quantity,
        unit_cost=payload.unit_cost,
        layer_date=payload.layer_date,
        lot_id=payload.lot_id,
        reference=payload.reference,
    )


@router.post("/fifo/consume", response_model=ConsumptionResultRead)
def consume_fifo(
    item_id: UUID = Query(...),
    quantity: Decimal = Query(...),
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Consume inventory using FIFO method."""
    return fifo_valuation_service.consume_inventory_fifo(db, organization_id, item_id, quantity)


@router.get("/fifo/{item_id}", response_model=FIFOInventoryRead)
def get_fifo_inventory(
    item_id: UUID,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Get current FIFO inventory state for an item."""
    return fifo_valuation_service.get_fifo_inventory(db, organization_id, item_id)


@router.post("/fifo/calculate-nrv", response_model=NRVCalculationRead)
def calculate_nrv_write_down(
    item_id: UUID = Query(...),
    warehouse_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(...),
    valuation_date: date = Query(...),
    estimated_selling_price: Decimal = Query(...),
    costs_to_complete: Decimal = Query(default=Decimal("0")),
    selling_costs: Decimal = Query(default=Decimal("0")),
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Calculate NRV write-down for an item per IAS 2."""
    return fifo_valuation_service.calculate_write_down(
        db=db,
        organization_id=organization_id,
        item_id=item_id,
        warehouse_id=warehouse_id,
        fiscal_period_id=fiscal_period_id,
        valuation_date=valuation_date,
        estimated_selling_price=estimated_selling_price,
        costs_to_complete=costs_to_complete,
        selling_costs=selling_costs,
    )


@router.get("/fifo/valuation-summary")
def get_fifo_valuation_summary(
    organization_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Get valuation summary for a period."""
    return fifo_valuation_service.get_valuation_summary(db, organization_id, fiscal_period_id)


# =============================================================================
# Lot/Serial Tracking
# =============================================================================

from app.services.ifrs.inv import lot_serial_service, LotInput


class LotCreate(BaseModel):
    """Create lot input."""
    item_id: UUID
    lot_number: str = Field(max_length=50)
    received_date: date
    unit_cost: Decimal
    initial_quantity: Decimal
    manufacture_date: Optional[date] = None
    expiry_date: Optional[date] = None
    supplier_id: Optional[UUID] = None
    supplier_lot_number: Optional[str] = None
    purchase_order_id: Optional[UUID] = None
    certificate_of_analysis: Optional[str] = None


class LotRead(BaseModel):
    """Lot response."""
    model_config = ConfigDict(from_attributes=True)
    lot_id: UUID
    item_id: UUID
    lot_number: str
    received_date: date
    expiry_date: Optional[date]
    initial_quantity: Decimal
    quantity_on_hand: Decimal
    quantity_available: Decimal
    unit_cost: Decimal
    is_quarantined: bool
    is_active: bool


class LotTraceabilityRead(BaseModel):
    """Lot traceability response."""
    lot_id: UUID
    lot_number: str
    item_id: UUID
    item_code: str
    supplier_lot: Optional[str]
    received_date: date
    expiry_date: Optional[date]
    total_received: Decimal
    total_remaining: Decimal
    total_consumed: Decimal


@router.post("/lots", response_model=LotRead, status_code=status.HTTP_201_CREATED)
def create_lot(
    payload: LotCreate,
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Create a new inventory lot."""
    input_data = LotInput(
        item_id=payload.item_id,
        lot_number=payload.lot_number,
        received_date=payload.received_date,
        unit_cost=payload.unit_cost,
        initial_quantity=payload.initial_quantity,
        manufacture_date=payload.manufacture_date,
        expiry_date=payload.expiry_date,
        supplier_id=payload.supplier_id,
        supplier_lot_number=payload.supplier_lot_number,
        purchase_order_id=payload.purchase_order_id,
        certificate_of_analysis=payload.certificate_of_analysis,
    )
    return lot_serial_service.create_lot(db, organization_id, input_data)


@router.get("/lots/{lot_id}", response_model=LotRead)
def get_lot(lot_id: UUID, db: Session = Depends(get_db)):
    """Get a lot by ID."""
    return lot_serial_service.get(db, str(lot_id))


@router.get("/lots", response_model=ListResponse[LotRead])
def list_lots(
    organization_id: UUID = Query(...),
    item_id: Optional[UUID] = None,
    is_quarantined: Optional[bool] = None,
    has_expiry: Optional[bool] = None,
    include_zero_quantity: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List lots with filters."""
    lots = lot_serial_service.list(
        db=db,
        organization_id=str(organization_id),
        item_id=str(item_id) if item_id else None,
        is_quarantined=is_quarantined,
        has_expiry=has_expiry,
        include_zero_quantity=include_zero_quantity,
        limit=limit,
        offset=offset,
    )
    return ListResponse(items=lots, count=len(lots), limit=limit, offset=offset)


@router.post("/lots/{lot_id}/allocate", response_model=LotRead)
def allocate_from_lot(
    lot_id: UUID,
    quantity: Decimal = Query(...),
    reference: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Allocate quantity from a lot."""
    return lot_serial_service.allocate_from_lot(db, lot_id, quantity, reference)


@router.post("/lots/{lot_id}/deallocate", response_model=LotRead)
def deallocate_from_lot(
    lot_id: UUID,
    quantity: Decimal = Query(...),
    db: Session = Depends(get_db),
):
    """Release allocation from a lot."""
    return lot_serial_service.deallocate_from_lot(db, lot_id, quantity)


@router.post("/lots/{lot_id}/consume", response_model=LotRead)
def consume_from_lot(
    lot_id: UUID,
    quantity: Decimal = Query(...),
    db: Session = Depends(get_db),
):
    """Consume quantity from a lot."""
    return lot_serial_service.consume_from_lot(db, lot_id, quantity)


@router.post("/lots/{lot_id}/quarantine", response_model=LotRead)
def quarantine_lot(
    lot_id: UUID,
    reason: str = Query(...),
    db: Session = Depends(get_db),
):
    """Place a lot in quarantine."""
    return lot_serial_service.quarantine_lot(db, lot_id, reason)


@router.post("/lots/{lot_id}/release-quarantine", response_model=LotRead)
def release_quarantine(
    lot_id: UUID,
    qc_status: str = Query(default="PASSED"),
    db: Session = Depends(get_db),
):
    """Release a lot from quarantine."""
    return lot_serial_service.release_quarantine(db, lot_id, qc_status)


@router.get("/lots/{lot_id}/traceability", response_model=LotTraceabilityRead)
def get_lot_traceability(
    lot_id: UUID,
    db: Session = Depends(get_db),
):
    """Get traceability information for a lot."""
    return lot_serial_service.get_traceability(db, lot_id)


@router.get("/lots/expiring", response_model=ListResponse[LotRead])
def get_expiring_lots(
    organization_id: UUID = Query(...),
    days_ahead: int = Query(default=30),
    db: Session = Depends(get_db),
):
    """Get lots expiring within specified days."""
    lots = lot_serial_service.get_expiring_lots(db, organization_id, days_ahead)
    return ListResponse(items=lots, count=len(lots), limit=len(lots), offset=0)


@router.get("/lots/expired", response_model=ListResponse[LotRead])
def get_expired_lots(
    organization_id: UUID = Query(...),
    db: Session = Depends(get_db),
):
    """Get already expired lots."""
    lots = lot_serial_service.get_expired_lots(db, organization_id)
    return ListResponse(items=lots, count=len(lots), limit=len(lots), offset=0)
