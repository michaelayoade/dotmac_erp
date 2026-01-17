"""
ERPNext to DotMac Books Field Mappings.

Configuration for transforming ERPNext DocTypes to DotMac Books models.
"""
from .accounts import AccountMapping, AccountCategoryMapping
from .items import ItemMapping, ItemCategoryMapping
from .assets import AssetMapping, AssetCategoryMapping
from .contacts import CustomerMapping, SupplierMapping
from .warehouses import WarehouseMapping

__all__ = [
    "AccountMapping",
    "AccountCategoryMapping",
    "ItemMapping",
    "ItemCategoryMapping",
    "AssetMapping",
    "AssetCategoryMapping",
    "CustomerMapping",
    "SupplierMapping",
    "WarehouseMapping",
]
