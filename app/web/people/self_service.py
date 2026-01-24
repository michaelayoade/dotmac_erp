"""
Self-service web routes for employees.

Thin wrappers around self-service web service.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.people.self_service_web import self_service_web_service
from app.web.deps import (
    WebAuthContext,
    get_db,
    require_self_service_access,
    require_self_service_expense_approver,
    require_self_service_leave_approver,
    require_hr_access,
)


router = APIRouter(prefix="/self", tags=["people-self-service"])


@router.get("/attendance", response_class=HTMLResponse)
def my_attendance(
    request: Request,
    month: Optional[str] = Query(None, description="Month in YYYY-MM format"),
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
):
    """Self-service attendance page with check-in/out actions."""
    return self_service_web_service.attendance_response(request, auth, db, month=month)


@router.post("/attendance/check-in")
def my_check_in(
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
    notes: Optional[str] = Form(default=None),
    latitude: Optional[float] = Form(default=None),
    longitude: Optional[float] = Form(default=None),
) -> RedirectResponse:
    """Check in for the current employee."""
    return self_service_web_service.check_in_response(
        auth,
        db,
        notes=notes,
        latitude=latitude,
        longitude=longitude,
    )


@router.post("/attendance/check-out")
def my_check_out(
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
    notes: Optional[str] = Form(default=None),
    latitude: Optional[float] = Form(default=None),
    longitude: Optional[float] = Form(default=None),
) -> RedirectResponse:
    """Check out for the current employee."""
    return self_service_web_service.check_out_response(
        auth,
        db,
        notes=notes,
        latitude=latitude,
        longitude=longitude,
    )


@router.get("/leave", response_class=HTMLResponse)
def my_leave(
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
):
    """Self-service leave page."""
    return self_service_web_service.leave_response(request, auth, db)


@router.get("/leave/{application_id}", response_class=HTMLResponse)
def my_leave_detail(
    application_id: UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
):
    """View a leave application for the current employee."""
    return self_service_web_service.leave_detail_response(
        request,
        auth,
        db,
        application_id=application_id,
    )


@router.post("/leave/{application_id}/cancel")
def cancel_leave(
    application_id: UUID,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Cancel a leave application for the current employee."""
    return self_service_web_service.leave_cancel_response(
        auth,
        db,
        application_id=application_id,
    )


@router.post("/leave/apply")
def apply_leave(
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
    leave_type_id: str = Form(...),
    from_date: date = Form(...),
    to_date: date = Form(...),
    half_day: Optional[str] = Form(default=None),
    reason: Optional[str] = Form(default=None),
) -> RedirectResponse:
    """Submit a leave application for the current employee."""
    return self_service_web_service.leave_apply_response(
        auth,
        db,
        leave_type_id=leave_type_id,
        from_date=from_date,
        to_date=to_date,
        half_day=half_day,
        reason=reason,
    )


@router.get("/payslips", response_class=HTMLResponse)
def my_payslips(
    request: Request,
    year: Optional[int] = Query(None, description="Filter by year"),
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
):
    """Self-service payslips page."""
    return self_service_web_service.payslips_response(request, auth, db, year=year, page=page)


@router.get("/payslips/{slip_id}", response_class=HTMLResponse)
def my_payslip_detail(
    slip_id: UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
):
    """View a payslip for the current employee."""
    return self_service_web_service.payslip_detail_response(
        request,
        auth,
        db,
        slip_id=slip_id,
    )


@router.get("/expenses", response_class=HTMLResponse)
def my_expenses(
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
):
    """Self-service expenses page."""
    return self_service_web_service.expenses_response(request, auth, db)


@router.post("/expenses/claims")
async def create_expense_claim(
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Create an expense claim with a single line item."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    claim_date_str = (form.get("claim_date") or "").strip()
    purpose = (form.get("purpose") or "").strip()
    expense_date_str = (form.get("expense_date") or "").strip()
    category_id = (form.get("category_id") or "").strip()
    description = (form.get("description") or "").strip()
    claimed_amount = (form.get("claimed_amount") or "").strip()
    recipient_bank_code = (form.get("recipient_bank_code") or "").strip()
    recipient_account_number = (form.get("recipient_account_number") or "").strip()
    receipt_url = (form.get("receipt_url") or "").strip()
    receipt_number = (form.get("receipt_number") or "").strip()
    receipt_file = form.get("receipt_file")
    submit_now = form.get("submit_now")
    project_id = (form.get("project_id") or "").strip()
    ticket_id = (form.get("ticket_id") or "").strip()
    task_id = (form.get("task_id") or "").strip()

    if not all([claim_date_str, purpose, expense_date_str, category_id, description, claimed_amount, recipient_bank_code, recipient_account_number]):
        raise HTTPException(status_code=400, detail="Missing required fields")

    claim_date = date.fromisoformat(claim_date_str) if claim_date_str else None
    expense_date = date.fromisoformat(expense_date_str) if expense_date_str else None

    if not claim_date or not expense_date:
        raise HTTPException(status_code=400, detail="Invalid dates submitted")

    return self_service_web_service.expense_claim_create_response(
        auth,
        db,
        claim_date=claim_date,
        purpose=purpose,
        expense_date=expense_date,
        category_id=category_id,
        description=description,
        claimed_amount=claimed_amount,
        recipient_bank_code=recipient_bank_code or None,
        recipient_account_number=recipient_account_number or None,
        receipt_url=receipt_url or None,
        receipt_number=receipt_number or None,
        receipt_file=receipt_file,
        submit_now=submit_now,
        project_id=project_id or None,
        ticket_id=ticket_id or None,
        task_id=task_id or None,
    )


@router.get("/expenses/claims/{claim_id}/edit", response_class=HTMLResponse)
def edit_expense_claim(
    claim_id: UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
):
    """Edit a draft expense claim."""
    return self_service_web_service.expense_claim_edit_response(
        request,
        auth,
        db,
        claim_id=claim_id,
    )


@router.post("/expenses/claims/{claim_id}/edit")
async def update_expense_claim(
    claim_id: UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Update items on a draft expense claim."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    item_ids = form.getlist("item_id")
    if not item_ids:
        raise HTTPException(status_code=400, detail="No claim items submitted")

    recipient_bank_code = (form.get("recipient_bank_code") or "").strip()
    recipient_account_number = (form.get("recipient_account_number") or "").strip()
    if not recipient_bank_code or not recipient_account_number:
        raise HTTPException(status_code=400, detail="Bank code and account number are required")

    items = []
    for item_id in item_ids:
        remove = form.get(f"remove_item_{item_id}")
        if remove:
            items.append({"item_id": item_id, "remove": True})
            continue

        expense_date_str = (form.get(f"expense_date_{item_id}") or "").strip()
        category_id = (form.get(f"category_id_{item_id}") or "").strip()
        description = (form.get(f"description_{item_id}") or "").strip()
        claimed_amount_str = (form.get(f"claimed_amount_{item_id}") or "").strip()
        receipt_number = (form.get(f"receipt_number_{item_id}") or "").strip()
        receipt_url = (form.get(f"receipt_url_{item_id}") or "").strip()

        if not all([expense_date_str, category_id, description, claimed_amount_str]):
            raise HTTPException(status_code=400, detail="Missing required item fields")

        try:
            expense_date = date.fromisoformat(expense_date_str)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid expense date") from exc

        try:
            claimed_amount = Decimal(claimed_amount_str)
        except (InvalidOperation, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid claimed amount") from exc

        items.append(
            {
                "item_id": item_id,
                "expense_date": expense_date,
                "category_id": category_id,
                "description": description,
                "claimed_amount": claimed_amount,
                "receipt_number": receipt_number or None,
                "receipt_url": receipt_url or None,
            }
        )

    return self_service_web_service.expense_claim_update_response(
        auth,
        db,
        claim_id=claim_id,
        items=items,
        recipient_bank_code=recipient_bank_code or None,
        recipient_account_number=recipient_account_number or None,
    )


@router.get("/team/leave", response_class=HTMLResponse)
def team_leave_requests(
    request: Request,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_self_service_leave_approver),
    db: Session = Depends(get_db),
):
    """Team leave approvals for direct reports."""
    return self_service_web_service.team_leave_response(
        request,
        auth,
        db,
        status=status,
        page=page,
    )


@router.post("/team/leave/{application_id}/approve")
def approve_team_leave(
    application_id: UUID,
    auth: WebAuthContext = Depends(require_self_service_leave_approver),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Approve a direct report leave request."""
    return self_service_web_service.team_leave_approve_response(
        auth,
        db,
        application_id=application_id,
    )


@router.post("/team/leave/{application_id}/reject")
def reject_team_leave(
    application_id: UUID,
    auth: WebAuthContext = Depends(require_self_service_leave_approver),
    db: Session = Depends(get_db),
    reason: Optional[str] = Form(default=None),
) -> RedirectResponse:
    """Reject a direct report leave request."""
    return self_service_web_service.team_leave_reject_response(
        auth,
        db,
        application_id=application_id,
        reason=reason,
    )


@router.get("/team/expenses", response_class=HTMLResponse)
def team_expense_requests(
    request: Request,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_self_service_expense_approver),
    db: Session = Depends(get_db),
):
    """Team expense approvals for direct reports."""
    return self_service_web_service.team_expenses_response(
        request,
        auth,
        db,
        status=status,
        page=page,
    )


@router.post("/team/expenses/{claim_id}/approve")
def approve_team_expense(
    claim_id: UUID,
    auth: WebAuthContext = Depends(require_self_service_expense_approver),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Approve a direct report expense claim."""
    return self_service_web_service.team_expense_approve_response(
        auth,
        db,
        claim_id=claim_id,
    )


@router.post("/team/expenses/{claim_id}/reject")
def reject_team_expense(
    claim_id: UUID,
    auth: WebAuthContext = Depends(require_self_service_expense_approver),
    db: Session = Depends(get_db),
    reason: Optional[str] = Form(default=None),
) -> RedirectResponse:
    """Reject a direct report expense claim."""
    return self_service_web_service.team_expense_reject_response(
        auth,
        db,
        claim_id=claim_id,
        reason=reason,
    )
