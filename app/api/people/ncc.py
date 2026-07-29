"""NCC regulatory read endpoints for the People/HR module.

Read-only aggregations that feed the NCC year-end return (Section G staff
head-count). Financial sections come from the Finance module.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db_with_org, require_organization_id, require_tenant_auth
from app.services.people.hr.ncc_staff_report import NccStaffReportService

router = APIRouter(
    prefix="/hr/ncc",
    tags=["people-ncc"],
    dependencies=[Depends(require_tenant_auth)],
)


class NccStaffHeadcountResponse(BaseModel):
    """NCC Section G matrix: category -> nationality -> gender -> count."""

    total_active: int
    by_category: dict[str, dict[str, dict[str, int]]]


@router.get("/staff-headcount", response_model=NccStaffHeadcountResponse)
def ncc_staff_headcount(
    organization_id: UUID = Depends(require_organization_id),
    db: Session = Depends(get_db_with_org),
) -> NccStaffHeadcountResponse:
    """Active-staff head-count by NCC category x Nigerian/Expatriate x Male/Female."""
    report = NccStaffReportService(db).build(organization_id)
    return NccStaffHeadcountResponse(**report)
