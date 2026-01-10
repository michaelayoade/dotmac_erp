"""
Fixed Assets (FA) Schema Models - IAS 16, IAS 36, IAS 38.
"""
from app.models.ifrs.fa.asset_category import AssetCategory
from app.models.ifrs.fa.asset import Asset, AssetStatus
from app.models.ifrs.fa.asset_component import AssetComponent
from app.models.ifrs.fa.depreciation_run import DepreciationRun, DepreciationRunStatus
from app.models.ifrs.fa.depreciation_schedule import DepreciationSchedule
from app.models.ifrs.fa.asset_revaluation import AssetRevaluation
from app.models.ifrs.fa.cash_generating_unit import CashGeneratingUnit
from app.models.ifrs.fa.asset_impairment import AssetImpairment
from app.models.ifrs.fa.asset_disposal import AssetDisposal, DisposalType

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
