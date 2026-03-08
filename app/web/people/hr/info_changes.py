"""HR info change request routes."""

from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.people.hr.info_change_request import (
    InfoChangeStatus,
    InfoChangeType,
)
from app.services.people.hr.info_change_service import InfoChangeService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context, get_db, require_hr_access

router = APIRouter(tags=["hr-info-changes"])


@router.get("/info-changes", response_class=HTMLResponse)
def info_change_requests(
    request: Request,
    status: str | None = Query(default="PENDING"),
    change_type: str | None = Query(default=None),
    employee_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """List info change requests for HR review."""
    org_id = (
        auth.organization_id
        if isinstance(auth.organization_id, UUID)
        else UUID(auth.organization_id)
    )
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

    svc = InfoChangeService(db)
    offset = (page - 1) * limit
    requests_list = svc.list_requests(
        org_id,
        status=parsed_status,
        change_type=parsed_change_type,
        employee_id=parsed_employee_id,
        limit=limit + 1,
        offset=offset,
    )
    has_more = len(requests_list) > limit
    requests_list = requests_list[:limit]
    # Compute total from count query
    from sqlalchemy import func, select as sa_select

    from app.models.people.hr.info_change_request import EmployeeInfoChangeRequest

    count_stmt = sa_select(func.count()).select_from(EmployeeInfoChangeRequest).where(
        EmployeeInfoChangeRequest.organization_id == org_id
    )
    if parsed_status:
        count_stmt = count_stmt.where(EmployeeInfoChangeRequest.status == parsed_status)
    if parsed_change_type:
        count_stmt = count_stmt.where(EmployeeInfoChangeRequest.change_type == parsed_change_type)
    if parsed_employee_id:
        count_stmt = count_stmt.where(EmployeeInfoChangeRequest.employee_id == parsed_employee_id)
    total_count = db.scalar(count_stmt) or 0
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = base_context(request, auth, "Info Change Requests", "info-changes", db=db)
    context.update(
        {
            "requests": requests_list,
            "statuses": [s.value for s in InfoChangeStatus],
            "types": [t.value for t in InfoChangeType],
            "status": parsed_status.value if parsed_status else "",
            "change_type": parsed_change_type.value if parsed_change_type else "",
            "employee_id": employee_id or "",
            "limit": limit,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
        }
    )
    return templates.TemplateResponse(
        request, "people/hr/info_change_requests.html", context
    )


@router.get("/info-changes/{request_id}", response_class=HTMLResponse)
def info_change_request_detail(
    request: Request,
    request_id: UUID,
    success: str | None = None,
    error: str | None = None,
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
):
    """Detail view for a specific info change request."""
    org_id = (
        auth.organization_id
        if isinstance(auth.organization_id, UUID)
        else UUID(auth.organization_id)
    )
    svc = InfoChangeService(db)
    req = svc.get_request_detail(org_id, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    context = base_context(request, auth, "Info Change Request", "info-changes", db=db)
    context.update(
        {
            "request_item": req,
            "success": success,
            "error": error,
        }
    )
    return templates.TemplateResponse(
        request, "people/hr/info_change_request_detail.html", context
    )


@router.post("/info-changes/{request_id}/approve")
def approve_info_change_request(
    request_id: UUID,
    reviewer_notes: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Approve a change request."""
    org_id = (
        auth.organization_id
        if isinstance(auth.organization_id, UUID)
        else UUID(auth.organization_id)
    )
    person_id = (
        auth.person_id if isinstance(auth.person_id, UUID) else UUID(auth.person_id)
    )
    svc = InfoChangeService(db)
    try:
        svc.approve_request(
            org_id, request_id, reviewer_id=person_id, reviewer_notes=reviewer_notes
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        message = quote(str(exc))
        if "not found" in str(exc).lower():
            return RedirectResponse(
                url=f"/people/hr/info-changes?error={message}",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/people/hr/info-changes/{request_id}?error={message}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        raise
    return RedirectResponse(
        url=f"/people/hr/info-changes/{request_id}?success=Approved",
        status_code=303,
    )


@router.post("/info-changes/{request_id}/reject")
def reject_info_change_request(
    request_id: UUID,
    reviewer_notes: str | None = Form(default=None),
    auth: WebAuthContext = Depends(require_hr_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Reject a change request."""
    org_id = (
        auth.organization_id
        if isinstance(auth.organization_id, UUID)
        else UUID(auth.organization_id)
    )
    person_id = (
        auth.person_id if isinstance(auth.person_id, UUID) else UUID(auth.person_id)
    )
    svc = InfoChangeService(db)
    try:
        svc.reject_request(
            org_id, request_id, reviewer_id=person_id, reviewer_notes=reviewer_notes
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        message = quote(str(exc))
        if "not found" in str(exc).lower():
            return RedirectResponse(
                url=f"/people/hr/info-changes?error={message}",
                status_code=303,
            )
        return RedirectResponse(
            url=f"/people/hr/info-changes/{request_id}?error={message}",
            status_code=303,
        )
    except Exception:
        db.rollback()
        raise
    return RedirectResponse(
        url=f"/people/hr/info-changes/{request_id}?success=Rejected",
        status_code=303,
    )
