"""
Fixed Assets (FA) Schema Models - IAS 16, IAS 36, IAS 38.
"""

from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_category import AssetCategory
from app.models.fixed_assets.asset_component import AssetComponent
from app.models.fixed_assets.asset_disposal import AssetDisposal, DisposalType
from app.models.fixed_assets.asset_impairment import AssetImpairment
from app.models.fixed_assets.asset_revaluation import AssetRevaluation
from app.models.fixed_assets.cash_generating_unit import CashGeneratingUnit
from app.models.fixed_assets.depreciation_run import (
    DepreciationRun,
    DepreciationRunStatus,
)
from app.models.fixed_assets.depreciation_schedule import DepreciationSchedule

__all__ = [
    "AssetCategory",
    "Asset",
    "AssetStatus",
    "AssetComponent",
    "DepreciationRun",
    "DepreciationRunStatus",
    "DepreciationSchedule",
    "AssetRevaluation",
    "CashGeneratingUnit",
    "AssetImpairment",
    "AssetDisposal",
    "DisposalType",
]
