"""
Inventory (INV) Services.

This module provides services for inventory management including
item master data, warehouses, transactions, and GL posting.
"""

from app.services.inventory.balance import (
    InventoryBalance as ComputedInventoryBalance,
)
from app.services.inventory.balance import (
    InventoryBalanceService,
    ItemStockSummary,
    LowStockItem,
    inventory_balance_service,
)
from app.services.inventory.bom import (
    AssemblyInput,
    AssemblyResult,
    BOMComponentInput,
    BOMInput,
    BOMService,
    bom_service,
)
from app.services.inventory.count import (
    CountInput,
    CountLineInput,
    CountSummary,
    InventoryCountService,
    inventory_count_service,
)
from app.services.inventory.fifo_valuation import (
    ConsumptionResult,
    FIFOInventory,
    FIFOLayer,
    FIFOValuationService,
    NRVCalculation,
    fifo_valuation_service,
)
from app.services.inventory.inv_posting_adapter import (
    INVPostingAdapter,
    INVPostingResult,
    inv_posting_adapter,
)
from app.services.inventory.item import (
    ItemCategoryInput,
    ItemCategoryService,
    ItemInput,
    ItemService,
    item_category_service,
    item_service,
)
from app.services.inventory.lot_serial import (
    LotInput,
    LotSerialService,
    LotTraceability,
    lot_serial_service,
)
from app.services.inventory.price_list import (
    PriceListInput,
    PriceListItemInput,
    PriceListService,
    ResolvedPrice,
    price_list_service,
)
from app.services.inventory.stock_reservation import (
    ReservationConfig,
    ReservationResult,
    StockReservationService,
)
from app.services.inventory.transaction import (
    CostingResult,
    InventoryTransactionService,
    TransactionInput,
    inventory_transaction_service,
)
from app.services.inventory.valuation_reconciliation import (
    ValuationReconciliationResult,
    ValuationReconciliationService,
)
from app.services.inventory.wac_valuation import (
    WACResult,
    WACSnapshot,
    WACValuationService,
)
from app.services.inventory.warehouse import (
    InventoryBalance,
    WarehouseInput,
    WarehouseLocationInput,
    WarehouseService,
    warehouse_service,
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
    # WAC valuation
    "WACValuationService",
    "WACSnapshot",
    "WACResult",
    # Valuation reconciliation
    "ValuationReconciliationService",
    "ValuationReconciliationResult",
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
    # Stock reservation
    "StockReservationService",
    "ReservationResult",
    "ReservationConfig",
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
