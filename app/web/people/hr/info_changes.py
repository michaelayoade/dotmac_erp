"""HR info change request routes."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import select

from app.models.people.hr.info_change_request import (
    EmployeeInfoChangeRequest,
    InfoChangeStatus,
    InfoChangeType,
)
from app.services.people.hr.info_change_service import InfoChangeService
from app.templates import templates
from app.web.deps import base_context, get_db, require_hr_access, WebAuthContext


router = APIRouter(tags=["hr-info-changes"])


@router.get("/info-changes", response_class=HTMLResponse)
def info_change_requests(
    request: Request,
    status: Optional[str] = Query(default="PENDING"),
    change_type: Optional[str] = Query(default=None),
    employee_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List info change requests for HR review."""
    org_id = auth.organization_id if isinstance(auth.organization_id, UUID) else UUID(auth.organization_id)
    parsed_status = None
    if status:
        try:
            parsed_status = InfoChangeStatus(status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid status") from exc

    parsed_change_type = None
    if change_type:
        try:
            parsed_change_type = InfoChangeType(change_type)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid change type") from exc

    parsed_employee_id = UUID(employee_id) if employee_id else None

    stmt = (
        select(EmployeeInfoChangeRequest)
        .options(joinedload(EmployeeInfoChangeRequest.employee))
        .where(EmployeeInfoChangeRequest.organization_id == org_id)
        .order_by(EmployeeInfoChangeRequest.created_at.desc())
        .limit(limit)
    )

    if parsed_employee_id:
        stmt = stmt.where(EmployeeInfoChangeRequest.employee_id == parsed_employee_id)
    if parsed_status:
        stmt = stmt.where(EmployeeInfoChangeRequest.status == parsed_status)
    if parsed_change_type:
        stmt = stmt.where(EmployeeInfoChangeRequest.change_type == parsed_change_type)

    requests = list(db.scalars(stmt).all())

    context = base_context(request, auth, "Info Change Requests", "info-changes", db=db)
    context.update({
        "requests": requests,
        "statuses": [s.value for s in InfoChangeStatus],
        "types": [t.value for t in InfoChangeType],
        "status": parsed_status.value if parsed_status else "",
        "change_type": parsed_change_type.value if parsed_change_type else "",
        "employee_id": employee_id or "",
        "limit": limit,
    })
    return templates.TemplateResponse(request, "people/hr/info_change_requests.html", context)


@router.get("/info-changes/{request_id}", response_class=HTMLResponse)
def info_change_request_detail(
    request: Request,
    request_id: UUID,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Detail view for a specific info change request."""
    org_id = auth.organization_id if isinstance(auth.organization_id, UUID) else UUID(auth.organization_id)
    req = db.scalar(
        select(EmployeeInfoChangeRequest)
        .options(joinedload(EmployeeInfoChangeRequest.employee))
        .where(
            EmployeeInfoChangeRequest.request_id == request_id,
            EmployeeInfoChangeRequest.organization_id == org_id,
        )
    )
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    context = base_context(request, auth, "Info Change Request", "info-changes", db=db)
    context.update({
        "request_item": req,
        "success": success,
        "error": error,
    })
    return templates.TemplateResponse(request, "people/hr/info_change_request_detail.html", context)


@router.post("/info-changes/{request_id}/approve")
def approve_info_change_request(
    request_id: UUID,
    reviewer_notes: Optional[str] = Form(default=None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Approve a change request."""
    org_id = auth.organization_id if isinstance(auth.organization_id, UUID) else UUID(auth.organization_id)
    person_id = auth.person_id if isinstance(auth.person_id, UUID) else UUID(auth.person_id)
    svc = InfoChangeService(db)
    svc.approve_request(org_id, request_id, reviewer_id=person_id, reviewer_notes=reviewer_notes)
    db.commit()
    return RedirectResponse(
        url=f"/people/hr/info-changes/{request_id}?success=Approved",
        status_code=303,
    )


@router.post("/info-changes/{request_id}/reject")
def reject_info_change_request(
    request_id: UUID,
    reviewer_notes: Optional[str] = Form(default=None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Reject a change request."""
    org_id = auth.organization_id if isinstance(auth.organization_id, UUID) else UUID(auth.organization_id)
    person_id = auth.person_id if isinstance(auth.person_id, UUID) else UUID(auth.person_id)
    svc = InfoChangeService(db)
    svc.reject_request(org_id, request_id, reviewer_id=person_id, reviewer_notes=reviewer_notes)
    db.commit()
    return RedirectResponse(
        url=f"/people/hr/info-changes/{request_id}?success=Rejected",
        status_code=303,
    )
