"""NCC regulatory financial read endpoint (Section F).

One org-scoped call returning the NCC year-end return's financial figures —
revenue, operating costs, assets, liabilities, equity — composed from erp's
existing income-statement / balance-sheet / expense-summary services.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id, require_tenant_auth
from app.services.finance.rpt.ncc_financials import ncc_financials_context

router = APIRouter(
    prefix="/ncc",
    tags=["finance-ncc"],
    dependencies=[Depends(require_tenant_auth)],
)


class NccFinancialsResponse(BaseModel):
    period: dict
    summary: dict
    detail: dict
    note: str


@router.get("/financials", response_model=NccFinancialsResponse)
def ncc_financials(
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    as_of_date: str | None = None,
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
) -> NccFinancialsResponse:
    """NCC Section F financials for the year (or explicit date range / as-at date)."""
    data = ncc_financials_context(
        db,
        organization_id,
        year=year,
        start_date=start_date,
        end_date=end_date,
        as_of_date=as_of_date,
    )
    return NccFinancialsResponse(**data)
