"""Shared AP API router state."""

from fastapi import APIRouter, Depends

from app.api.deps import require_tenant_auth

router = APIRouter(
    prefix="/ap",
    tags=["accounts-payable"],
    dependencies=[Depends(require_tenant_auth)],
)
