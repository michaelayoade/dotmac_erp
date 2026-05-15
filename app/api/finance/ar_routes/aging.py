"""Aging routes for the AR API."""

from datetime import date
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id
from app.api.finance.ar_routes.base import router
from app.schemas.finance.ar import ARAgingReportRead
from app.services.auth_dependencies import require_tenant_permission
from app.services.finance.ar import ar_aging_service


@router.get("/aging", response_model=ARAgingReportRead)
def get_ar_aging(
    organization_id: UUID = Depends(require_organization_id),
    as_of_date: date = Query(...),
    customer_id: UUID | None = None,
    auth: dict = Depends(require_tenant_permission("ar:aging:read")),
    db: Session = Depends(get_db_with_org),
):
    """Get AR aging report."""
    return ar_aging_service.build_aging_report(
        db=db,
        organization_id=organization_id,
        as_of_date=as_of_date,
        customer_id=customer_id,
    )
