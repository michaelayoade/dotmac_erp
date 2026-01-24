from uuid import UUID

from fastapi import Depends, HTTPException

from app.services.auth_dependencies import (
    require_audit_auth,
    require_permission,
    require_role,
    require_user_auth,
    require_tenant_auth,
    require_tenant_role,
    require_tenant_permission,
    require_admin_bypass,
    require_web_session,
    optional_web_session,
)
from app.services.feature_flags import (
    require_feature,
    is_feature_enabled,
    FEATURE_INVENTORY,
    FEATURE_FIXED_ASSETS,
    FEATURE_LEASES,
    FEATURE_BUDGETING,
    FEATURE_MULTI_CURRENCY,
    FEATURE_PROJECT_ACCOUNTING,
    FEATURE_BANK_RECONCILIATION,
    FEATURE_RECURRING_TRANSACTIONS,
)

__all__ = [
    "require_audit_auth",
    "require_permission",
    "require_role",
    "require_user_auth",
    "require_tenant_auth",
    "require_tenant_role",
    "require_tenant_permission",
    "require_organization_id",
    "require_admin_bypass",
    "require_web_session",
    "optional_web_session",
    "require_feature",
    "is_feature_enabled",
    "FEATURE_INVENTORY",
    "FEATURE_FIXED_ASSETS",
    "FEATURE_LEASES",
    "FEATURE_BUDGETING",
    "FEATURE_MULTI_CURRENCY",
    "FEATURE_PROJECT_ACCOUNTING",
    "FEATURE_BANK_RECONCILIATION",
    "FEATURE_RECURRING_TRANSACTIONS",
]


def require_organization_id(auth: dict = Depends(require_tenant_auth)) -> UUID:
    """Return the authenticated user's organization_id as a UUID."""
    organization_id = auth.get("organization_id")
    if not organization_id:
        raise HTTPException(status_code=403, detail="Organization access required")
    return UUID(organization_id)
