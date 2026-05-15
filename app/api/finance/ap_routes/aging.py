"""Aging routes for the AP API."""

from datetime import date
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ap_routes.base import router
from app.schemas.finance.ap import APAgingReportRead  # pragma: allowlist secret
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ap import ap_aging_service


@router.get("/aging", response_model=APAgingReportRead)
def get_ap_aging(
    organization_id: UUID = Depends(require_organization_id),
    as_of_date: date = Query(...),
    supplier_id: UUID | None = None,
    auth: dict = Depends(require_tenant_permission("ap:aging:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get AP aging report."""
    return ap_aging_service.build_aging_report(
        db=db,
        organization_id=organization_id,
        as_of_date=as_of_date,
        supplier_id=supplier_id,
    )
