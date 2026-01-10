"""
Fixed Assets (FA) Services.

This module provides services for fixed asset management including
asset master data, depreciation, revaluations, and disposals.
"""

from app.services.ifrs.fa.asset import (
    AssetService,
    AssetInput,
    AssetCategoryService,
    AssetCategoryInput,
    asset_service,
    asset_category_service,
)
from app.services.ifrs.fa.depreciation import (
    DepreciationService,
    DepreciationCalculation,
    depreciation_service,
)
from app.services.ifrs.fa.revaluation import (
    AssetRevaluationService,
    RevaluationInput,
    asset_revaluation_service,
)
from app.services.ifrs.fa.disposal import (
    AssetDisposalService,
    DisposalInput,
    asset_disposal_service,
)
from app.services.ifrs.fa.fa_posting_adapter import (
    FAPostingAdapter,
    FAPostingResult,
    fa_posting_adapter,
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
