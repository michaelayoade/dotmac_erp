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
]


def require_organization_id(auth: dict = Depends(require_tenant_auth)) -> UUID:
    """Return the authenticated user's organization_id as a UUID."""
    organization_id = auth.get("organization_id")
    if not organization_id:
        raise HTTPException(status_code=403, detail="Organization access required")
    return UUID(organization_id)
