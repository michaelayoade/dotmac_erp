"""
INV API Router.

Inventory API endpoints for item management, transactions, and costing.
"""

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_organization_id, require_tenant_auth
from app.api.finance.utils import parse_enum
from app.config import settings
from app.db import SessionLocal
from app.models.inventory.inventory_transaction import TransactionType
from app.schemas.finance.common import ListResponse, PostingResultSchema
from app.schemas.inventory import (
    AddLayerCreate,
    ConsumptionResultRead,
    FIFOInventoryRead,
    InventoryItemCreate,
    InventoryItemRead,
    ItemCategoryCreate,
    ItemCategoryRead,
    LotCreate,
    LotRead,
    LotTraceabilityRead,
    LowStockItemRead,
    NRVCalculationRead,
    StockBalanceRead,
    TransactionCreate,
    TransactionRead,
)
from app.services.auth_dependencies import require_tenant_permission
from app.services.feature_flags import FEATURE_INVENTORY, require_feature
from app.services.inventory import (
    ItemCategoryInput,
    ItemInput,
    TransactionInput,
    inv_posting_adapter,
    inventory_balance_service,
    inventory_transaction_service,
    item_category_service,
    item_service,
)

router = APIRouter(
    prefix="/inventory",
    tags=["inventory"],
    dependencies=[
        Depends(require_tenant_auth),
        Depends(require_tenant_permission("inventory:access")),
        Depends(require_feature(FEATURE_INVENTORY)),
    ],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post(
    "/categories", response_model=ItemCategoryRead, status_code=status.HTTP_201_CREATED
)
def create_item_category(
    payload: ItemCategoryCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:categories:create")),
    db: Session = Depends(get_db),
):
    """Create a new item category."""
    input_data = ItemCategoryInput(
        category_code=payload.category_code,
        category_name=payload.category_name,
        inventory_account_id=payload.inventory_account_id,
        cogs_account_id=payload.cogs_account_id,
        revenue_account_id=payload.revenue_account_id,
        inventory_adjustment_account_id=payload.inventory_adjustment_account_id,
        description=payload.description,
        parent_category_id=payload.parent_category_id,
        purchase_variance_account_id=payload.purchase_variance_account_id,
        reorder_point=payload.reorder_point,
        minimum_stock=payload.minimum_stock,
    )
    return item_category_service.create_category(db, organization_id, input_data)


@router.get("/categories/{category_id}", response_model=ItemCategoryRead)
def get_item_category(
    category_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:categories:read")),
    db: Session = Depends(get_db),
):
    """Get an item category by ID."""
    return item_category_service.get(db, str(category_id), organization_id)


@router.get("/categories", response_model=ListResponse[ItemCategoryRead])
def list_item_categories(
    organization_id: UUID = Depends(require_organization_id),
    is_active: bool | None = None,
    search: str | None = Query(default=None, description="Search by code or name"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("inventory:categories:read")),
    db: Session = Depends(get_db),
):
    """List item categories with filters."""
    categories = item_category_service.list(
        db=db,
        organization_id=str(organization_id),
        is_active=is_active,
        search=search,
        limit=limit,
        offset=offset,
    )
    return ListResponse(
        items=categories,
        count=len(categories),
        limit=limit,
        offset=offset,
    )


# =============================================================================
# Inventory Items
# =============================================================================


@router.post(
    "/items", response_model=InventoryItemRead, status_code=status.HTTP_201_CREATED
)
def create_inventory_item(
    payload: InventoryItemCreate,
    organization_id: UUID = Depends(require_organization_id),
    category_id: UUID = Query(..., description="Item category ID"),
    currency_code: str = Query(default=settings.default_functional_currency_code),
    auth: dict = Depends(require_tenant_permission("inventory:items:create")),
    db: Session = Depends(get_db),
):
    """Create a new inventory item."""
    from app.models.inventory.item import CostingMethod

    # Map costing method string to enum
    costing_method = (
        CostingMethod(payload.costing_method)
        if payload.costing_method
        else CostingMethod.WEIGHTED_AVERAGE
    )

    input_data = ItemInput(
        item_code=payload.item_code,
        item_name=payload.item_name,
        category_id=payload.item_category_id or category_id,
        base_uom=payload.unit_of_measure,
        currency_code=currency_code,
        costing_method=costing_method,
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
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:items:read")),
    db: Session = Depends(get_db),
):
    """Get an inventory item by ID."""
    return item_service.get(db, str(item_id), organization_id)


@router.get("/items", response_model=ListResponse[InventoryItemRead])
def list_inventory_items(
    organization_id: UUID = Depends(require_organization_id),
    category_id: UUID | None = Query(
        default=None, description="Filter by item category"
    ),
    is_active: bool | None = None,
    is_purchaseable: bool | None = None,
    is_saleable: bool | None = None,
    search: str | None = Query(
        default=None, description="Search by code, name, or barcode"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("inventory:items:read")),
    db: Session = Depends(get_db),
):
    """List inventory items with filters."""
    items = item_service.list(
        db=db,
        organization_id=str(organization_id),
        category_id=str(category_id) if category_id else None,
        is_active=is_active,
        is_purchaseable=is_purchaseable,
        is_saleable=is_saleable,
        search=search,
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


@router.post(
    "/transactions", response_model=TransactionRead, status_code=status.HTTP_201_CREATED
)
def create_inventory_transaction(
    payload: TransactionCreate,
    organization_id: UUID = Depends(require_organization_id),
    created_by_user_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(..., description="Fiscal period ID"),
    uom: str = Query(default="EACH", description="Unit of measure"),
    currency_code: str = Query(default=settings.default_functional_currency_code),
    auth: dict = Depends(require_tenant_permission("inventory:transactions:create")),
    db: Session = Depends(get_db),
):
    """Create an inventory transaction."""
    from datetime import datetime

    from app.models.inventory.inventory_transaction import TransactionType as TxnType

    # Map transaction type string to enum
    txn_type = TxnType(payload.transaction_type)

    # Convert date to datetime for transaction_date
    txn_datetime = datetime.combine(payload.transaction_date, datetime.min.time())

    input_data = TransactionInput(
        transaction_type=txn_type,
        transaction_date=txn_datetime,
        fiscal_period_id=fiscal_period_id,
        item_id=payload.item_id,
        warehouse_id=payload.warehouse_id,
        location_id=payload.location_id,
        lot_id=payload.lot_id,
        to_warehouse_id=payload.to_warehouse_id,
        to_location_id=payload.to_location_id,
        quantity=payload.quantity,
        unit_cost=payload.unit_cost or Decimal("0"),
        uom=payload.uom or uom,
        currency_code=payload.currency_code or currency_code,
        reason_code=payload.reason_code,
        source_document_type=payload.reference_type,
        source_document_id=payload.reference_id,
        reference=payload.notes,
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
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:transactions:read")),
    db: Session = Depends(get_db),
):
    """Get an inventory transaction by ID."""
    return inventory_transaction_service.get(db, str(transaction_id), organization_id)


@router.get("/transactions", response_model=ListResponse[TransactionRead])
def list_inventory_transactions(
    organization_id: UUID = Depends(require_organization_id),
    item_id: UUID | None = None,
    warehouse_id: UUID | None = None,
    transaction_type: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("inventory:transactions:read")),
    db: Session = Depends(get_db),
):
    """List inventory transactions with filters."""
    transactions = inventory_transaction_service.list(
        db=db,
        organization_id=str(organization_id),
        item_id=str(item_id) if item_id else None,
        warehouse_id=str(warehouse_id) if warehouse_id else None,
        transaction_type=parse_enum(TransactionType, transaction_type),
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
    organization_id: UUID = Depends(require_organization_id),
    posted_by_user_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("inventory:transactions:post")),
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
        entry_number=None,
        message=result.message,
    )


@router.get("/stock/item/{item_id}", response_model=StockBalanceRead)
def get_item_stock_balance(
    item_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    warehouse_id: UUID | None = None,
    auth: dict = Depends(require_tenant_permission("inventory:stock:read")),
    db: Session = Depends(get_db),
):
    """Get stock balance for an item, optionally filtered by warehouse."""
    balance = inventory_balance_service.get_item_balance(
        db=db,
        organization_id=organization_id,
        item_id=item_id,
        warehouse_id=warehouse_id,
    )
    if not balance:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Item not found")
    return StockBalanceRead(
        item_id=balance.item_id,
        item_code=balance.item_code,
        item_name=balance.item_name,
        warehouse_id=balance.warehouse_id,
        warehouse_code=balance.warehouse_code,
        quantity_on_hand=balance.quantity_on_hand,
        quantity_reserved=balance.quantity_reserved,
        quantity_available=balance.quantity_available,
        average_cost=balance.average_cost,
        total_value=balance.total_value,
    )


@router.get(
    "/stock/warehouse/{warehouse_id}", response_model=ListResponse[StockBalanceRead]
)
def get_warehouse_inventory(
    warehouse_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:stock:read")),
    db: Session = Depends(get_db),
):
    """Get all inventory balances for a warehouse."""
    balances = inventory_balance_service.get_warehouse_inventory(
        db=db,
        organization_id=organization_id,
        warehouse_id=warehouse_id,
    )
    items = [
        StockBalanceRead(
            item_id=b.item_id,
            item_code=b.item_code,
            item_name=b.item_name,
            warehouse_id=b.warehouse_id,
            warehouse_code=b.warehouse_code,
            quantity_on_hand=b.quantity_on_hand,
            quantity_reserved=b.quantity_reserved,
            quantity_available=b.quantity_available,
            average_cost=b.average_cost,
            total_value=b.total_value,
        )
        for b in balances
    ]
    return ListResponse(items=items, count=len(items), limit=len(items), offset=0)


@router.get("/stock/low-stock", response_model=ListResponse[LowStockItemRead])
def get_low_stock_items(
    organization_id: UUID = Depends(require_organization_id),
    include_below_minimum: bool = Query(default=True),
    auth: dict = Depends(require_tenant_permission("inventory:stock:read")),
    db: Session = Depends(get_db),
):
    """Get items at or below reorder point."""
    low_stock = inventory_balance_service.get_low_stock_items(
        db=db,
        organization_id=organization_id,
        include_below_minimum=include_below_minimum,
    )
    items = [
        LowStockItemRead(
            item_id=item.item_id,
            item_code=item.item_code,
            item_name=item.item_name,
            quantity_on_hand=item.quantity_on_hand,
            quantity_available=item.quantity_available,
            reorder_point=item.reorder_point,
            reorder_quantity=item.reorder_quantity,
            suggested_order_qty=item.suggested_order_qty,
            default_supplier_id=item.default_supplier_id,
            lead_time_days=item.lead_time_days,
        )
        for item in low_stock
    ]
    return ListResponse(items=items, count=len(items), limit=len(items), offset=0)


@router.post("/stock/allocate")
def allocate_stock(
    item_id: UUID = Query(...),
    quantity: Decimal = Query(...),
    reference_type: str = Query(..., description="e.g., SALES_ORDER"),
    reference_id: UUID = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    warehouse_id: UUID | None = None,
    lot_id: UUID | None = None,
    auth: dict = Depends(require_tenant_permission("inventory:stock:allocate")),
    db: Session = Depends(get_db),
):
    """Allocate (reserve) inventory for a sales order or other document."""
    success = inventory_balance_service.allocate_inventory(
        db=db,
        organization_id=organization_id,
        item_id=item_id,
        quantity=quantity,
        reference_type=reference_type,
        reference_id=reference_id,
        warehouse_id=warehouse_id,
        lot_id=lot_id,
    )
    return {"success": success}


@router.post("/stock/deallocate")
def deallocate_stock(
    item_id: UUID = Query(...),
    quantity: Decimal = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    warehouse_id: UUID | None = None,
    lot_id: UUID | None = None,
    auth: dict = Depends(require_tenant_permission("inventory:stock:allocate")),
    db: Session = Depends(get_db),
):
    """Release an inventory allocation."""
    success = inventory_balance_service.deallocate_inventory(
        db=db,
        organization_id=organization_id,
        item_id=item_id,
        quantity=quantity,
        warehouse_id=warehouse_id,
        lot_id=lot_id,
    )
    return {"success": success}


from app.services.inventory import fifo_valuation_service  # noqa: E402


@router.post("/fifo/add-layer", status_code=status.HTTP_201_CREATED)
def add_fifo_layer(
    payload: AddLayerCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:valuation:create")),
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
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:valuation:update")),
    db: Session = Depends(get_db),
):
    """Consume inventory using FIFO method."""
    return fifo_valuation_service.consume_inventory_fifo(
        db, organization_id, item_id, quantity
    )


@router.post("/fifo/calculate-nrv", response_model=NRVCalculationRead)
def calculate_nrv_write_down(
    item_id: UUID = Query(...),
    warehouse_id: UUID = Query(...),
    fiscal_period_id: UUID = Query(...),
    valuation_date: date = Query(...),
    estimated_selling_price: Decimal = Query(...),
    costs_to_complete: Decimal = Query(default=Decimal("0")),
    selling_costs: Decimal = Query(default=Decimal("0")),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:valuation:calculate")),
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


# Note: Specific routes must come before parameterized routes to avoid matching issues
@router.get("/fifo/valuation-summary")
def get_fifo_valuation_summary(
    organization_id: UUID = Depends(require_organization_id),
    fiscal_period_id: UUID = Query(...),
    auth: dict = Depends(require_tenant_permission("inventory:valuation:read")),
    db: Session = Depends(get_db),
):
    """Get valuation summary for a period."""
    return fifo_valuation_service.get_valuation_summary(
        db, organization_id, fiscal_period_id
    )


@router.get("/fifo/{item_id}", response_model=FIFOInventoryRead)
def get_fifo_inventory(
    item_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:valuation:read")),
    db: Session = Depends(get_db),
):
    """Get current FIFO inventory state for an item."""
    return fifo_valuation_service.get_fifo_inventory(db, organization_id, item_id)


from app.services.inventory import (  # noqa: E402
    LotInput,
    lot_serial_service,
)

logger = logging.getLogger(__name__)


@router.post("/lots", response_model=LotRead, status_code=status.HTTP_201_CREATED)
def create_lot(
    payload: LotCreate,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:lots:create")),
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


# Note: Specific routes must come before parameterized routes to avoid matching issues
@router.get("/lots/expiring", response_model=ListResponse[LotRead])
def get_expiring_lots(
    organization_id: UUID = Depends(require_organization_id),
    days_ahead: int = Query(default=30),
    auth: dict = Depends(require_tenant_permission("inventory:lots:read")),
    db: Session = Depends(get_db),
):
    """Get lots expiring within specified days."""
    lots = lot_serial_service.get_expiring_lots(db, organization_id, days_ahead)
    return ListResponse(items=lots, count=len(lots), limit=len(lots), offset=0)


@router.get("/lots/expired", response_model=ListResponse[LotRead])
def get_expired_lots(
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:lots:read")),
    db: Session = Depends(get_db),
):
    """Get already expired lots."""
    lots = lot_serial_service.get_expired_lots(db, organization_id)
    return ListResponse(items=lots, count=len(lots), limit=len(lots), offset=0)


@router.get("/lots/{lot_id}", response_model=LotRead)
def get_lot(
    lot_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:lots:read")),
    db: Session = Depends(get_db),
):
    """Get a lot by ID."""
    return lot_serial_service.get(db, str(lot_id), organization_id)


@router.get("/lots", response_model=ListResponse[LotRead])
def list_lots(
    organization_id: UUID = Depends(require_organization_id),
    item_id: UUID | None = None,
    is_quarantined: bool | None = None,
    has_expiry: bool | None = None,
    include_zero_quantity: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_tenant_permission("inventory:lots:read")),
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
    reference: str | None = None,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:lots:allocate")),
    db: Session = Depends(get_db),
):
    """Allocate quantity from a lot."""
    return lot_serial_service.allocate_from_lot(
        db, organization_id, lot_id, quantity, reference
    )


@router.post("/lots/{lot_id}/deallocate", response_model=LotRead)
def deallocate_from_lot(
    lot_id: UUID,
    quantity: Decimal = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:lots:allocate")),
    db: Session = Depends(get_db),
):
    """Release allocation from a lot."""
    return lot_serial_service.deallocate_from_lot(db, organization_id, lot_id, quantity)


@router.post("/lots/{lot_id}/consume", response_model=LotRead)
def consume_from_lot(
    lot_id: UUID,
    quantity: Decimal = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:lots:allocate")),
    db: Session = Depends(get_db),
):
    """Consume quantity from a lot."""
    return lot_serial_service.consume_from_lot(db, organization_id, lot_id, quantity)


@router.post("/lots/{lot_id}/quarantine", response_model=LotRead)
def quarantine_lot(
    lot_id: UUID,
    reason: str = Query(...),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:lots:quarantine")),
    db: Session = Depends(get_db),
):
    """Place a lot in quarantine."""
    return lot_serial_service.quarantine_lot(db, organization_id, lot_id, reason)


@router.post("/lots/{lot_id}/release-quarantine", response_model=LotRead)
def release_quarantine(
    lot_id: UUID,
    qc_status: str = Query(default="PASSED"),
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:lots:quarantine")),
    db: Session = Depends(get_db),
):
    """Release a lot from quarantine."""
    return lot_serial_service.release_quarantine(db, organization_id, lot_id, qc_status)


@router.get("/lots/{lot_id}/traceability", response_model=LotTraceabilityRead)
def get_lot_traceability(
    lot_id: UUID,
    organization_id: UUID = Depends(require_organization_id),
    auth: dict = Depends(require_tenant_permission("inventory:lots:read")),
    db: Session = Depends(get_db),
):
    """Get traceability information for a lot."""
    return lot_serial_service.get_traceability(db, organization_id, lot_id)


# Note: /lots/expiring and /lots/expired routes moved to earlier in file for proper route matching
