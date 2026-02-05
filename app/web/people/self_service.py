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


def _safe_form_text(value: object | None, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    return default


def _safe_form_float(value: object | None) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _coerce_iso_date(value: object | None, field_name: str) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=[
                    {
                        "type": "value_error.date",
                        "loc": ["body", field_name],
                        "msg": "Invalid date format (expected YYYY-MM-DD)",
                        "input": value,
                    }
                ],
            ) from exc
    return None


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
async def my_check_in(
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Check in for the current employee."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    notes = _safe_form_text(form.get("notes")) or None
    latitude = _safe_form_float(form.get("latitude"))
    longitude = _safe_form_float(form.get("longitude"))
    return self_service_web_service.check_in_response(
        auth,
        db,
        notes=notes,
        latitude=latitude,
        longitude=longitude,
    )


@router.post("/attendance/check-out")
async def my_check_out(
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Check out for the current employee."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    notes = _safe_form_text(form.get("notes")) or None
    latitude = _safe_form_float(form.get("latitude"))
    longitude = _safe_form_float(form.get("longitude"))
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


@router.get("/tax-info", response_class=HTMLResponse)
def my_tax_info(
    request: Request,
    success: Optional[str] = None,
    error: Optional[str] = None,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
):
    """Self-service tax, bank, and personal info page."""
    return self_service_web_service.tax_info_response(
        request,
        auth,
        db,
        success=success,
        error=error,
    )


@router.post("/tax-info")
async def update_tax_info(
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Submit a change request for tax, bank, and personal info."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    payload = {
        "phone": _safe_form_text(form.get("phone")) or None,
        "date_of_birth": _coerce_iso_date(form.get("date_of_birth"), "date_of_birth"),
        "gender": _safe_form_text(form.get("gender")) or None,
        "address_line1": _safe_form_text(form.get("address_line1")) or None,
        "address_line2": _safe_form_text(form.get("address_line2")) or None,
        "city": _safe_form_text(form.get("city")) or None,
        "region": _safe_form_text(form.get("region")) or None,
        "postal_code": _safe_form_text(form.get("postal_code")) or None,
        "country_code": _safe_form_text(form.get("country_code")) or None,
        "personal_email": _safe_form_text(form.get("personal_email")) or None,
        "personal_phone": _safe_form_text(form.get("personal_phone")) or None,
        "emergency_contact_name": _safe_form_text(form.get("emergency_contact_name")) or None,
        "emergency_contact_phone": _safe_form_text(form.get("emergency_contact_phone")) or None,
        "bank_name": _safe_form_text(form.get("bank_name")) or None,
        "bank_account_number": _safe_form_text(form.get("bank_account_number")) or None,
        "bank_account_name": _safe_form_text(form.get("bank_account_name")) or None,
        "bank_branch_code": _safe_form_text(form.get("bank_branch_code")) or None,
        "tin": _safe_form_text(form.get("tin")) or None,
        "tax_state": _safe_form_text(form.get("tax_state")) or None,
        "rsa_pin": _safe_form_text(form.get("rsa_pin")) or None,
        "pfa_code": _safe_form_text(form.get("pfa_code")) or None,
        "nhf_number": _safe_form_text(form.get("nhf_number")) or None,
    }

    return self_service_web_service.tax_info_submit_response(
        auth,
        db,
        payload=payload,
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
async def apply_leave(
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
    leave_type_id: Optional[str] = Form(default=None),
    from_date: Optional[date] = Form(default=None),
    to_date: Optional[date] = Form(default=None),
    half_day: Optional[str] = Form(default=None),
    reason: Optional[str] = Form(default=None),
) -> RedirectResponse:
    """Submit a leave application for the current employee."""
    if leave_type_id is None or from_date is None or to_date is None:
        content_type = (request.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            try:
                payload = await request.json()
            except Exception:
                payload = {}
            if leave_type_id is None:
                leave_type_id = payload.get("leave_type_id")
            if from_date is None:
                from_date = _coerce_iso_date(payload.get("from_date"), "from_date")
            if to_date is None:
                to_date = _coerce_iso_date(payload.get("to_date"), "to_date")
            if half_day is None and "half_day" in payload:
                half_day_value = payload.get("half_day")
                if isinstance(half_day_value, bool):
                    half_day = "on" if half_day_value else None
                elif half_day_value is not None:
                    half_day = str(half_day_value)
            if reason is None and "reason" in payload:
                reason = payload.get("reason")
        else:
            form = getattr(request.state, "csrf_form", None)
            if form is None:
                try:
                    form = await request.form()
                except Exception:
                    form = None
            if form is not None:
                if leave_type_id is None:
                    leave_type_id = _safe_form_text(form.get("leave_type_id")) or None
                if from_date is None:
                    from_date = _coerce_iso_date(form.get("from_date"), "from_date")
                if to_date is None:
                    to_date = _coerce_iso_date(form.get("to_date"), "to_date")
                if half_day is None:
                    half_day = _safe_form_text(form.get("half_day")) or None
                if reason is None:
                    reason = _safe_form_text(form.get("reason")) or None

    missing_fields: list[str] = []
    if not leave_type_id:
        missing_fields.append("leave_type_id")
    if not from_date:
        missing_fields.append("from_date")
    if not to_date:
        missing_fields.append("to_date")
    if missing_fields:
        raise HTTPException(
            status_code=422,
            detail=[
                {
                    "type": "missing",
                    "loc": ["body", field],
                    "msg": "Field required",
                    "input": None,
                }
                for field in missing_fields
            ],
        )

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


@router.get("/tickets", response_class=HTMLResponse)
def my_tickets(
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Self-service tickets page."""
    return self_service_web_service.tickets_response(request, auth, db, page=page)


@router.get("/tasks", response_class=HTMLResponse)
def my_tasks(
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    """Self-service tasks page."""
    return self_service_web_service.tasks_response(request, auth, db, page=page)


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

    claim_date_str = _safe_form_text(form.get("claim_date"))
    purpose = _safe_form_text(form.get("purpose"))
    expense_date_str = _safe_form_text(form.get("expense_date"))
    category_id = _safe_form_text(form.get("category_id"))
    description = _safe_form_text(form.get("description"))
    claimed_amount = _safe_form_text(form.get("claimed_amount"))
    recipient_bank_code = _safe_form_text(form.get("recipient_bank_code"))
    recipient_account_number = _safe_form_text(form.get("recipient_account_number"))
    receipt_url = _safe_form_text(form.get("receipt_url"))
    receipt_number = _safe_form_text(form.get("receipt_number"))
    receipt_file = form.get("receipt_file")
    submit_now = form.get("submit_now")
    project_id = _safe_form_text(form.get("project_id"))
    ticket_id = _safe_form_text(form.get("ticket_id"))
    task_id = _safe_form_text(form.get("task_id"))

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

    recipient_bank_code = _safe_form_text(form.get("recipient_bank_code"))
    recipient_account_number = _safe_form_text(form.get("recipient_account_number"))
    if not recipient_bank_code or not recipient_account_number:
        raise HTTPException(status_code=400, detail="Bank code and account number are required")

    # Extract optional project/ticket/task linkage
    project_id_str = _safe_form_text(form.get("project_id"))
    ticket_id_str = _safe_form_text(form.get("ticket_id"))
    task_id_str = _safe_form_text(form.get("task_id"))

    project_id = UUID(project_id_str) if project_id_str else None
    ticket_id = UUID(ticket_id_str) if ticket_id_str else None
    task_id = UUID(task_id_str) if task_id_str else None

    items = []
    for item_id in item_ids:
        remove = form.get(f"remove_item_{item_id}")
        if remove:
            items.append({"item_id": item_id, "remove": True})
            continue

        expense_date_str = _safe_form_text(form.get(f"expense_date_{item_id}"))
        category_id = _safe_form_text(form.get(f"category_id_{item_id}"))
        description = _safe_form_text(form.get(f"description_{item_id}"))
        claimed_amount_str = _safe_form_text(form.get(f"claimed_amount_{item_id}"))
        receipt_number = _safe_form_text(form.get(f"receipt_number_{item_id}"))
        receipt_url = _safe_form_text(form.get(f"receipt_url_{item_id}"))

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
        project_id=project_id,
        ticket_id=ticket_id,
        task_id=task_id,
    )


@router.post("/expenses/claims/{claim_id}/submit")
async def submit_expense_claim(
    claim_id: UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Submit a draft expense claim for approval."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    return self_service_web_service.expense_claim_submit_response(
        auth,
        db,
        claim_id=claim_id,
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


# =============================================================================
# Discipline Self-Service Routes
# =============================================================================


@router.get("/discipline", response_class=HTMLResponse)
def my_discipline_cases(
    request: Request,
    include_closed: bool = Query(default=False),
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
):
    """Self-service discipline cases page - view my disciplinary cases."""
    return self_service_web_service.discipline_cases_response(
        request, auth, db, include_closed=include_closed
    )


@router.get("/discipline/{case_id}", response_class=HTMLResponse)
def my_discipline_case_detail(
    case_id: UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
):
    """View details of a specific disciplinary case."""
    return self_service_web_service.discipline_case_detail_response(
        request, auth, db, case_id=case_id
    )


@router.post("/discipline/{case_id}/respond")
async def submit_discipline_response(
    case_id: UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Submit employee response to a disciplinary query."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    response_text = _safe_form_text(form.get("response_text"))
    if not response_text:
        raise HTTPException(status_code=400, detail="Response text is required")

    return self_service_web_service.discipline_submit_response(
        auth, db, case_id=case_id, response_text=response_text
    )


@router.post("/discipline/{case_id}/appeal")
async def file_discipline_appeal(
    case_id: UUID,
    request: Request,
    auth: WebAuthContext = Depends(require_self_service_access),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """File an appeal against a disciplinary decision."""
    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    appeal_reason = _safe_form_text(form.get("appeal_reason"))
    if not appeal_reason:
        raise HTTPException(status_code=400, detail="Appeal reason is required")

    return self_service_web_service.discipline_file_appeal_response(
        auth, db, case_id=case_id, appeal_reason=appeal_reason
    )
