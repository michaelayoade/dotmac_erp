"""
Inventory (INV) Services.

This module provides services for inventory management including
item master data, warehouses, transactions, and GL posting.
"""

from app.services.inventory.item import (
    ItemService,
    ItemInput,
    ItemCategoryService,
    ItemCategoryInput,
    item_service,
    item_category_service,
)
from app.services.inventory.warehouse import (
    WarehouseService,
    WarehouseInput,
    WarehouseLocationInput,
    InventoryBalance,
    warehouse_service,
)
from app.services.inventory.transaction import (
    InventoryTransactionService,
    TransactionInput,
    CostingResult,
    inventory_transaction_service,
)
from app.services.inventory.inv_posting_adapter import (
    INVPostingAdapter,
    INVPostingResult,
    inv_posting_adapter,
)
from app.services.inventory.fifo_valuation import (
    FIFOValuationService,
    fifo_valuation_service,
    FIFOLayer,
    FIFOInventory,
    ConsumptionResult,
    NRVCalculation,
)
from app.services.inventory.lot_serial import (
    LotSerialService,
    lot_serial_service,
    LotInput,
    LotTraceability,
)
from app.services.inventory.balance import (
    InventoryBalanceService,
    inventory_balance_service,
    InventoryBalance as ComputedInventoryBalance,
    ItemStockSummary,
    LowStockItem,
)
from app.services.inventory.price_list import (
    PriceListService,
    price_list_service,
    PriceListInput,
    PriceListItemInput,
    ResolvedPrice,
)
from app.services.inventory.count import (
    InventoryCountService,
    inventory_count_service,
    CountInput,
    CountLineInput,
    CountSummary,
)
from app.services.inventory.bom import (
    BOMService,
    bom_service,
    BOMInput,
    BOMComponentInput,
    AssemblyInput,
    AssemblyResult,
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
    # Balance
    "InventoryBalanceService",
    "inventory_balance_service",
    "ComputedInventoryBalance",
    "ItemStockSummary",
    "LowStockItem",
    # Price List
    "PriceListService",
    "price_list_service",
    "PriceListInput",
    "PriceListItemInput",
    "ResolvedPrice",
    # Inventory Count
    "InventoryCountService",
    "inventory_count_service",
    "CountInput",
    "CountLineInput",
    "CountSummary",
    # BOM
    "BOMService",
    "bom_service",
    "BOMInput",
    "BOMComponentInput",
    "AssemblyInput",
    "AssemblyResult",
]
