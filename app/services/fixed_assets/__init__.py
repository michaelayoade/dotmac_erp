"""
Fixed Assets (FA) Services.

This module provides services for fixed asset management including
asset master data, depreciation, revaluations, and disposals.
"""

from app.services.fixed_assets.asset import (
    AssetCategoryInput,
    AssetCategoryService,
    AssetInput,
    AssetService,
    asset_category_service,
    asset_service,
)
from app.services.fixed_assets.depreciation import (
    DepreciationCalculation,
    DepreciationService,
    depreciation_service,
)
from app.services.fixed_assets.disposal import (
    AssetDisposalService,
    DisposalInput,
    asset_disposal_service,
)
from app.services.fixed_assets.fa_posting_adapter import (
    FAPostingAdapter,
    FAPostingResult,
    fa_posting_adapter,
)
from app.services.fixed_assets.revaluation import (
    AssetRevaluationService,
    RevaluationInput,
    asset_revaluation_service,
)

__all__ = [
    # Asset
    "AssetService",
    "AssetInput",
    "asset_service",
    # Category
    "AssetCategoryService",
    "AssetCategoryInput",
    "asset_category_service",
    # Depreciation
    "DepreciationService",
    "DepreciationCalculation",
    "depreciation_service",
    # Revaluation
    "AssetRevaluationService",
    "RevaluationInput",
    "asset_revaluation_service",
    # Disposal
    "AssetDisposalService",
    "DisposalInput",
    "asset_disposal_service",
    # Posting
    "FAPostingAdapter",
    "FAPostingResult",
    "fa_posting_adapter",
]
