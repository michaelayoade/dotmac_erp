"""Shared AR API router state."""

from fastapi import APIRouter, Depends

from app.api.deps import require_tenant_auth

router = APIRouter(
    prefix="/ar",
    tags=["accounts-receivable"],
    dependencies=[Depends(require_tenant_auth)],
)
