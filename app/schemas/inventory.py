"""
Inventory Schemas.

Pydantic schemas for Inventory API endpoints.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# Item Categories
# =============================================================================


class ItemCategoryCreate(BaseModel):
    """Create item category request."""

    category_code: str = Field(max_length=30)
    category_name: str = Field(max_length=100)
    inventory_account_id: UUID
    cogs_account_id: UUID
    revenue_account_id: UUID
    inventory_adjustment_account_id: UUID
    description: str | None = None
    parent_category_id: UUID | None = None
    purchase_variance_account_id: UUID | None = None
    reorder_point: Decimal | None = None
    minimum_stock: Decimal | None = None


class ItemCategoryRead(BaseModel):
    """Item category response."""

    model_config = ConfigDict(from_attributes=True)

    category_id: UUID
    organization_id: UUID
    category_code: str
    category_name: str
    description: str | None
    parent_category_id: UUID | None
    inventory_account_id: UUID
    cogs_account_id: UUID
    revenue_account_id: UUID
    inventory_adjustment_account_id: UUID
    purchase_variance_account_id: UUID | None
    reorder_point: Decimal | None
    minimum_stock: Decimal | None
    is_active: bool


# =============================================================================
# Inventory Items
# =============================================================================


class InventoryItemCreate(BaseModel):
    """Create inventory item request."""

    item_code: str = Field(max_length=30)
    item_name: str = Field(max_length=200)
    item_category_id: UUID | None = None
    unit_of_measure: str = Field(max_length=20)
    costing_method: str = "WEIGHTED_AVERAGE"
    standard_cost: Decimal | None = None
    reorder_point: Decimal | None = None
    reorder_quantity: Decimal | None = None
    inventory_account_id: UUID | None = None
    cogs_account_id: UUID | None = None
    description: str | None = None


class InventoryItemRead(BaseModel):
    """Inventory item response (model fields only)."""

    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    organization_id: UUID
    item_code: str
    item_name: str
    base_uom: str
    costing_method: str
    standard_cost: Decimal | None
    average_cost: Decimal | None
    last_purchase_cost: Decimal | None
    list_price: Decimal | None
    reorder_point: Decimal | None
    reorder_quantity: Decimal | None
    minimum_stock: Decimal | None
    maximum_stock: Decimal | None
    track_inventory: bool
    track_lots: bool
    track_serial_numbers: bool
    is_active: bool
    is_purchaseable: bool
    is_saleable: bool


class InventoryItemWithBalanceRead(BaseModel):
    """Inventory item with computed stock levels."""

    item_id: UUID
    organization_id: UUID
    item_code: str
    item_name: str
    base_uom: str
    costing_method: str
    standard_cost: Decimal | None
    average_cost: Decimal | None
    quantity_on_hand: Decimal
    quantity_reserved: Decimal
    quantity_available: Decimal
    total_value: Decimal
    is_active: bool


# =============================================================================
# Transactions
# =============================================================================


class TransactionCreate(BaseModel):
    """Create inventory transaction request."""

    item_id: UUID
    warehouse_id: UUID
    location_id: UUID | None = None
    lot_id: UUID | None = None
    to_warehouse_id: UUID | None = None
    to_location_id: UUID | None = None
    transaction_type: str = Field(max_length=30)
    transaction_date: date
    quantity: Decimal
    unit_cost: Decimal | None = None
    uom: str | None = None
    currency_code: str | None = None
    reason_code: str | None = None
    reference_type: str | None = None
    reference_id: UUID | None = None
    notes: str | None = None


class TransactionRead(BaseModel):
    """Inventory transaction response."""

    model_config = ConfigDict(from_attributes=True)

    transaction_id: UUID
    organization_id: UUID
    item_id: UUID
    warehouse_id: UUID
    location_id: UUID | None = None
    lot_id: UUID | None = None
    to_warehouse_id: UUID | None = None
    to_location_id: UUID | None = None
    transaction_type: str
    transaction_date: date
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    quantity_before: Decimal
    quantity_after: Decimal
    reference: str | None = None
    reason_code: str | None = None


# =============================================================================
# Costing & Valuation
# =============================================================================


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


class ValuationReconciliationRead(BaseModel):
    """Inventory valuation reconciliation snapshot."""

    fiscal_period_id: UUID
    inventory_total: Decimal
    gl_total: Decimal
    difference: Decimal
    is_balanced: bool


# =============================================================================
# Stock Balances
# =============================================================================


class StockBalanceRead(BaseModel):
    """Stock balance response."""

    item_id: UUID
    item_code: str
    item_name: str
    warehouse_id: UUID | None
    warehouse_code: str | None
    quantity_on_hand: Decimal
    quantity_reserved: Decimal
    quantity_available: Decimal
    average_cost: Decimal
    total_value: Decimal


class LowStockItemRead(BaseModel):
    """Low stock alert item."""

    item_id: UUID
    item_code: str
    item_name: str
    quantity_on_hand: Decimal
    quantity_available: Decimal
    reorder_point: Decimal
    reorder_quantity: Decimal | None
    suggested_order_qty: Decimal
    default_supplier_id: UUID | None
    lead_time_days: int | None


# =============================================================================
# FIFO Valuation (IAS 2)
# =============================================================================


class FIFOLayerRead(BaseModel):
    """FIFO layer response."""

    layer_date: date
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    lot_id: UUID | None = None
    reference: str | None = None


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
    lot_id: UUID | None = None
    reference: str | None = None


# =============================================================================
# Lot/Serial Tracking
# =============================================================================


class LotCreate(BaseModel):
    """Create lot input."""

    item_id: UUID
    lot_number: str = Field(max_length=50)
    received_date: date
    unit_cost: Decimal
    initial_quantity: Decimal
    manufacture_date: date | None = None
    expiry_date: date | None = None
    supplier_id: UUID | None = None
    supplier_lot_number: str | None = None
    purchase_order_id: UUID | None = None
    certificate_of_analysis: str | None = None


class LotRead(BaseModel):
    """Lot response."""

    model_config = ConfigDict(from_attributes=True)
    lot_id: UUID
    item_id: UUID
    lot_number: str
    received_date: date
    expiry_date: date | None
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
    supplier_lot: str | None
    received_date: date
    expiry_date: date | None
    total_received: Decimal
    total_remaining: Decimal
    total_consumed: Decimal


__all__ = [
    "ItemCategoryCreate",
    "ItemCategoryRead",
    "InventoryItemCreate",
    "InventoryItemRead",
    "InventoryItemWithBalanceRead",
    "TransactionCreate",
    "TransactionRead",
    "CostingResultRead",
    "InventoryValuationRead",
    "ValuationReconciliationRead",
    "StockBalanceRead",
    "LowStockItemRead",
    "FIFOLayerRead",
    "FIFOInventoryRead",
    "ConsumptionResultRead",
    "NRVCalculationRead",
    "AddLayerCreate",
    "LotCreate",
    "LotRead",
    "LotTraceabilityRead",
]
