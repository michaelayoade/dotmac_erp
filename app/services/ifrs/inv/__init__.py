"""
Inventory (INV) Services.

This module provides services for inventory management including
item master data, warehouses, transactions, and GL posting.
"""

from app.services.ifrs.inv.item import (
    ItemService,
    ItemInput,
    ItemCategoryService,
    ItemCategoryInput,
    item_service,
    item_category_service,
)
from app.services.ifrs.inv.warehouse import (
    WarehouseService,
    WarehouseInput,
    WarehouseLocationInput,
    InventoryBalance,
    warehouse_service,
)
from app.services.ifrs.inv.transaction import (
    InventoryTransactionService,
    TransactionInput,
    CostingResult,
    inventory_transaction_service,
)
from app.services.ifrs.inv.inv_posting_adapter import (
    INVPostingAdapter,
    INVPostingResult,
    inv_posting_adapter,
)
from app.services.ifrs.inv.fifo_valuation import (
    FIFOValuationService,
    fifo_valuation_service,
    FIFOLayer,
    FIFOInventory,
    ConsumptionResult,
    NRVCalculation,
)
from app.services.ifrs.inv.lot_serial import (
    LotSerialService,
    lot_serial_service,
    LotInput,
    LotTraceability,
)

__all__ = [
    # Item
    "ItemService",
    "ItemInput",
    "item_service",
    # Category
    "ItemCategoryService",
    "ItemCategoryInput",
    "item_category_service",
    # Warehouse
    "WarehouseService",
    "WarehouseInput",
    "WarehouseLocationInput",
    "InventoryBalance",
    "warehouse_service",
    # Transaction
    "InventoryTransactionService",
    "TransactionInput",
    "CostingResult",
    "inventory_transaction_service",
    # Posting
    "INVPostingAdapter",
    "INVPostingResult",
    "inv_posting_adapter",
    # FIFO Valuation
    "FIFOValuationService",
    "fifo_valuation_service",
    "FIFOLayer",
    "FIFOInventory",
    "ConsumptionResult",
    "NRVCalculation",
    # Lot/Serial
    "LotSerialService",
    "lot_serial_service",
    "LotInput",
    "LotTraceability",
]
