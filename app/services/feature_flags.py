"""
Feature Flags — re-exports from the unified feature_flag_service.

All feature flag logic lives in ``app.services.feature_flag_service``.
This module exists solely as a convenience import path.
"""

# Re-export everything consumers need
from app.services.feature_flag_service import (  # noqa: F401
    FeatureFlagService,
    is_feature_enabled,
    require_feature,
    require_feature_web,
)

# ── Feature flag key constants ───────────────────────────────────────
FEATURE_MULTI_CURRENCY = "enable_multi_currency"
FEATURE_BUDGETING = "enable_budgeting"
FEATURE_PROJECT_ACCOUNTING = "enable_project_accounting"
FEATURE_BANK_RECONCILIATION = "enable_bank_reconciliation"
FEATURE_RECURRING_TRANSACTIONS = "enable_recurring_transactions"
FEATURE_INVENTORY = "enable_inventory"
FEATURE_FIXED_ASSETS = "enable_fixed_assets"
FEATURE_LEASES = "enable_leases"
FEATURE_PROCUREMENT = "enable_procurement"
FEATURE_IPSAS = "enable_ipsas"
FEATURE_FUND_ACCOUNTING = "enable_fund_accounting"
FEATURE_STOCK_RESERVATION = "enable_stock_reservation"
FEATURE_SERVICE_HOOKS = "enable_service_hooks"
