"""
Inventory Schemas.

Pydantic schemas for Inventory API endpoints.
"""

from datetime import date
from decimal import Decimal
from typing import Optional
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
    description: Optional[str] = None
    parent_category_id: Optional[UUID] = None
    purchase_variance_account_id: Optional[UUID] = None
    reorder_point: Optional[Decimal] = None
    minimum_stock: Optional[Decimal] = None


class ItemCategoryRead(BaseModel):
    """Item category response."""

    model_config = ConfigDict(from_attributes=True)

    category_id: UUID
    organization_id: UUID
    category_code: str
    category_name: str
    description: Optional[str]
    parent_category_id: Optional[UUID]
    inventory_account_id: UUID
    cogs_account_id: UUID
    revenue_account_id: UUID
    inventory_adjustment_account_id: UUID
    purchase_variance_account_id: Optional[UUID]
    reorder_point: Optional[Decimal]
    minimum_stock: Optional[Decimal]
    is_active: bool


# =============================================================================
# Inventory Items
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
    """Inventory item response (model fields only)."""

    model_config = ConfigDict(from_attributes=True)

    item_id: UUID
    organization_id: UUID
    item_code: str
    item_name: str
    base_uom: str
    costing_method: str
    standard_cost: Optional[Decimal]
    average_cost: Optional[Decimal]
    last_purchase_cost: Optional[Decimal]
    list_price: Optional[Decimal]
    reorder_point: Optional[Decimal]
    reorder_quantity: Optional[Decimal]
    minimum_stock: Optional[Decimal]
    maximum_stock: Optional[Decimal]
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
    standard_cost: Optional[Decimal]
    average_cost: Optional[Decimal]
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
    location_id: Optional[UUID] = None
    lot_id: Optional[UUID] = None
    to_warehouse_id: Optional[UUID] = None
    to_location_id: Optional[UUID] = None
    transaction_type: str = Field(max_length=30)
    transaction_date: date
    quantity: Decimal
    unit_cost: Optional[Decimal] = None
    uom: Optional[str] = None
    currency_code: Optional[str] = None
    reason_code: Optional[str] = None
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
    location_id: Optional[UUID] = None
    lot_id: Optional[UUID] = None
    to_warehouse_id: Optional[UUID] = None
    to_location_id: Optional[UUID] = None
    transaction_type: str
    transaction_date: date
    quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal
    quantity_before: Decimal
    quantity_after: Decimal
    reference: Optional[str] = None
    reason_code: Optional[str] = None


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


# =============================================================================
# Stock Balances
# =============================================================================


class StockBalanceRead(BaseModel):
    """Stock balance response."""

    item_id: UUID
    item_code: str
    item_name: str
    warehouse_id: Optional[UUID]
    warehouse_code: Optional[str]
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
    reorder_quantity: Optional[Decimal]
    suggested_order_qty: Decimal
    default_supplier_id: Optional[UUID]
    lead_time_days: Optional[int]


# =============================================================================
# FIFO Valuation (IAS 2)
# =============================================================================


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
