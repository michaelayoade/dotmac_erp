"""
Feature Flag Service.

Provides functions to check if features are enabled via DomainSetting.
Used for gating access to optional modules (inventory, fixed assets, leases, etc.).
"""

from typing import Callable, Dict, Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models.domain_settings import SettingDomain
from app.services.settings_cache import settings_cache


# Feature flag keys matching SETTINGS_SPECS
FEATURE_MULTI_CURRENCY = "enable_multi_currency"
FEATURE_BUDGETING = "enable_budgeting"
FEATURE_PROJECT_ACCOUNTING = "enable_project_accounting"
FEATURE_BANK_RECONCILIATION = "enable_bank_reconciliation"
FEATURE_RECURRING_TRANSACTIONS = "enable_recurring_transactions"
FEATURE_INVENTORY = "enable_inventory"
FEATURE_FIXED_ASSETS = "enable_fixed_assets"
FEATURE_LEASES = "enable_leases"

# Human-readable feature names for error messages
FEATURE_LABELS = {
    FEATURE_MULTI_CURRENCY: "Multi-Currency",
    FEATURE_BUDGETING: "Budgeting",
    FEATURE_PROJECT_ACCOUNTING: "Project Accounting",
    FEATURE_BANK_RECONCILIATION: "Bank Reconciliation",
    FEATURE_RECURRING_TRANSACTIONS: "Recurring Transactions",
    FEATURE_INVENTORY: "Inventory",
    FEATURE_FIXED_ASSETS: "Fixed Assets",
    FEATURE_LEASES: "Leases",
}

# Cache for defaults derived from settings specs (lazy initialization)
_feature_defaults_cache: Optional[Dict[str, bool]] = None


def _get_feature_defaults() -> Dict[str, bool]:
    """
    Get feature defaults from settings specs (single source of truth).

    Lazy initialization to avoid circular imports at module load time.
    Falls back to hardcoded defaults if specs unavailable.
    """
    global _feature_defaults_cache
    if _feature_defaults_cache is not None:
        return _feature_defaults_cache

    try:
        from app.services.settings_spec import list_specs

        defaults = {}
        for spec in list_specs(SettingDomain.features):
            if spec.key.startswith("enable_"):
                defaults[spec.key] = bool(spec.default) if spec.default is not None else False
        _feature_defaults_cache = defaults
        return defaults
    except ImportError:
        # Fallback if settings_spec not available (e.g., during testing)
        _feature_defaults_cache = {
            FEATURE_MULTI_CURRENCY: True,
            FEATURE_BUDGETING: False,
            FEATURE_PROJECT_ACCOUNTING: False,
            FEATURE_BANK_RECONCILIATION: True,
            FEATURE_RECURRING_TRANSACTIONS: True,
            FEATURE_INVENTORY: True,
            FEATURE_FIXED_ASSETS: True,
            FEATURE_LEASES: False,
        }
        return _feature_defaults_cache


def is_feature_enabled(db: Session, feature_key: str) -> bool:
    """
    Check if a feature is enabled.

    Reads from domain_settings table with features domain.
    Uses settings cache for performance.
    Falls back to spec defaults if not configured.

    Args:
        db: Database session
        feature_key: Feature key (e.g., "enable_inventory")

    Returns:
        True if feature is enabled, False otherwise
    """
    defaults = _get_feature_defaults()
    default = defaults.get(feature_key, False)

    # Use cache - returns None if not found
    cached_value = settings_cache.get_setting_value(
        db, SettingDomain.features, feature_key, default=None
    )

    if cached_value is not None:
        # Cache returns the actual value (already converted to bool)
        return bool(cached_value)

    # Not in cache or DB - use default
    return default


def get_all_features(db: Session) -> dict[str, bool]:
    """
    Get all feature flags and their current values.

    Uses settings cache for performance.

    Returns:
        Dict mapping feature keys to enabled status
    """
    # Start with defaults from specs
    result = dict(_get_feature_defaults())

    # Get all feature settings from cache
    cached_features = settings_cache.get_domain_settings(db, SettingDomain.features)

    # Merge cached values (they override defaults)
    for key, value in cached_features.items():
        result[key] = bool(value)

    return result


def require_feature(feature_key: str) -> Callable:
    """
    FastAPI dependency factory that requires a feature to be enabled.

    Usage:
        @router.get("/inventory/items")
        def list_items(
            _: None = Depends(require_feature(FEATURE_INVENTORY)),
            db: Session = Depends(get_db),
        ):
            ...

    Args:
        feature_key: Feature key to check

    Returns:
        FastAPI dependency function

    Raises:
        HTTPException(403): If feature is not enabled
    """

    def dependency(db: Session = Depends(lambda: SessionLocal())) -> None:
        try:
            if not is_feature_enabled(db, feature_key):
                label = FEATURE_LABELS.get(feature_key, feature_key)
                raise HTTPException(
                    status_code=403,
                    detail=f"The '{label}' feature is not enabled. "
                    f"Enable it in Settings > Feature Flags.",
                )
        finally:
            db.close()

    return dependency


def require_feature_web(feature_key: str, db: Session) -> None:
    """
    Check feature flag for web routes (non-dependency version).

    Use this in web route handlers where you already have a db session.

    Args:
        feature_key: Feature key to check
        db: Database session

    Raises:
        HTTPException(403): If feature is not enabled
    """
    if not is_feature_enabled(db, feature_key):
        label = FEATURE_LABELS.get(feature_key, feature_key)
        raise HTTPException(
            status_code=403,
            detail=f"The '{label}' feature is not enabled. "
            f"Enable it in Settings > Feature Flags.",
        )
