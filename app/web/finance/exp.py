"""
Expense Web Routes.

HTML template routes for expense management.
"""
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.expense.web import expense_claims_web_service
from app.services.expense.dashboard_web import expense_dashboard_service
from app.services.finance.exp.expense import expense_service
from app.services.finance.exp.web import expense_web_service
from app.templates import templates
from app.web.deps import get_db, require_expense_access, WebAuthContext, base_context
from app.web.finance.exp_limits import router as limits_router


router = APIRouter(tags=["expense-web"])

# Include limits sub-router
router.include_router(limits_router)


def _safe_return_to(request: Request, fallback: str = "/expense") -> str:
    candidate = request.query_params.get("return_to") or request.query_params.get("next")
    if not candidate:
        candidate = request.headers.get("referer") or request.headers.get("referrer")
    if not candidate:
        return fallback
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        if request.url.hostname and parsed.hostname != request.url.hostname:
            return fallback
        path = parsed.path or ""
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path or fallback
    return candidate if candidate.startswith("/") else fallback


# =============================================================================
# Dashboard
# =============================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def expense_dashboard(
    request: Request,
    period: str = Query("month", description="Period: month, quarter, year, or all"),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense module dashboard page."""
    return expense_dashboard_service.dashboard_response(request, auth, db, period)


# =============================================================================
# Expense List
# =============================================================================

@router.get("/list", response_class=HTMLResponse)
def expense_list(
    request: Request,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense list page."""
    context = base_context(request, auth, "Expenses", "expense")
    context.update(
        expense_web_service.list_context(
            db,
            str(auth.organization_id),
            status=status,
            start_date=start_date,
            end_date=end_date,
        )
    )
    return templates.TemplateResponse(request, "expense/list.html", context)


@router.get("/claims", response_class=HTMLResponse)
def expense_claims_dashboard(
    request: Request,
    period: str = Query("month", description="Period: month, quarter, year, or all"),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense claims dashboard with charts."""
    return expense_dashboard_service.claims_dashboard_response(request, auth, db, period)


@router.get("/claims/new")
def expense_claim_new_redirect(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
):
    """Redirect expense claim creation to self-service claims form."""
    return RedirectResponse("/people/self/expenses", status_code=302)


@router.get("/claims/list", response_class=HTMLResponse)
def expense_claims_list(
    request: Request,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense claims list page."""
    return expense_claims_web_service.claims_list_response(
        request=request,
        auth=auth,
        db=db,
        status=status,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/claims/{claim_id}", response_class=HTMLResponse)
def expense_claim_detail(
    request: Request,
    claim_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense claim detail page."""
    return expense_claims_web_service.claim_detail_response(
        request=request,
        auth=auth,
        db=db,
        claim_id=claim_id,
    )


@router.post("/claims/{claim_id}/submit")
def submit_expense_claim(
    claim_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Submit an expense claim for approval."""
    return expense_claims_web_service.submit_claim_response(
        claim_id=claim_id,
        auth=auth,
        db=db,
    )


@router.post("/claims/{claim_id}/approve")
def approve_expense_claim(
    claim_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Approve an expense claim."""
    return expense_claims_web_service.approve_claim_response(
        claim_id=claim_id,
        auth=auth,
        db=db,
    )


@router.post("/claims/{claim_id}/reject")
def reject_expense_claim(
    claim_id: str,
    reason: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Reject an expense claim."""
    return expense_claims_web_service.reject_claim_response(
        claim_id=claim_id,
        reason=reason,
        auth=auth,
        db=db,
    )


@router.get("/advances", response_class=HTMLResponse)
def expense_advances(
    request: Request,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Cash advances list page."""
    context = base_context(request, auth, "Cash Advances", "advances")
    context.update(
        expense_web_service.list_context(
            db,
            str(auth.organization_id),
            status=status,
            start_date=start_date,
            end_date=end_date,
        )
    )
    return templates.TemplateResponse(request, "expense/list.html", context)


@router.get("/cards", response_class=HTMLResponse)
def expense_cards(
    request: Request,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Corporate cards list page."""
    context = base_context(request, auth, "Corporate Cards", "cards")
    context.update(
        expense_web_service.list_context(
            db,
            str(auth.organization_id),
            status=status,
            start_date=start_date,
            end_date=end_date,
        )
    )
    return templates.TemplateResponse(request, "expense/list.html", context)


@router.get("/categories", response_class=HTMLResponse)
def expense_categories(
    request: Request,
    search: Optional[str] = None,
    is_active: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense categories page."""
    return expense_claims_web_service.categories_list_response(
        request=request,
        auth=auth,
        db=db,
        search=search,
        is_active=is_active,
        page=page,
    )


@router.get("/categories/new", response_class=HTMLResponse)
def new_expense_category_form(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """New expense category form."""
    return expense_claims_web_service.new_category_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/categories/new", response_class=HTMLResponse)
async def create_expense_category(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Create new expense category."""
    return await expense_claims_web_service.create_category_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse)
def edit_expense_category_form(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Edit expense category form."""
    return expense_claims_web_service.edit_category_form_response(
        request=request,
        auth=auth,
        db=db,
        category_id=category_id,
    )


@router.post("/categories/{category_id}/edit", response_class=HTMLResponse)
async def update_expense_category(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Update expense category."""
    return await expense_claims_web_service.update_category_response(
        request=request,
        auth=auth,
        db=db,
        category_id=category_id,
    )


@router.post("/categories/{category_id}/delete", response_class=HTMLResponse)
def delete_expense_category(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Soft delete (deactivate) an expense category."""
    return expense_claims_web_service.delete_category_response(
        category_id=category_id,
        auth=auth,
        db=db,
    )


# =============================================================================
# New Expense
# =============================================================================

@router.get("/new", response_class=HTMLResponse)
def new_expense_form(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """New expense form."""
    context = base_context(request, auth, "New Expense", "expenses")
    context.update(
        expense_web_service.form_context(
            db,
            str(auth.organization_id),
        )
    )
    context["return_to"] = _safe_return_to(request)
    return templates.TemplateResponse(request, "expense/form.html", context)


@router.post("/new", response_class=HTMLResponse)
def create_expense(
    request: Request,
    expense_date: str = Form(...),
    expense_account_id: str = Form(...),
    amount: str = Form(...),
    description: str = Form(...),
    payment_method: str = Form(...),
    payment_account_id: Optional[str] = Form(None),
    tax_code_id: Optional[str] = Form(None),
    tax_amount: str = Form("0"),
    currency_code: Optional[str] = Form(None),
    payee: Optional[str] = Form(None),
    receipt_reference: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    cost_center_id: Optional[str] = Form(None),
    business_unit_id: Optional[str] = Form(None),
    return_to: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Create new expense."""
    try:
        expense = expense_web_service.create_expense_from_form(
            db=db,
            organization_id=auth.organization_id,
            user_id=auth.user_id,
            expense_date=expense_date,
            expense_account_id=expense_account_id,
            amount=amount,
            description=description,
            payment_method=payment_method,
            payment_account_id=payment_account_id,
            tax_code_id=tax_code_id,
            tax_amount=tax_amount,
            currency_code=currency_code,
            payee=payee,
            receipt_reference=receipt_reference,
            notes=notes,
            project_id=project_id,
            cost_center_id=cost_center_id,
            business_unit_id=business_unit_id,
        )
        db.commit()
        return RedirectResponse(f"/expense/{expense.expense_id}", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "New Expense", "expenses")
        context.update(
            expense_web_service.form_context(db, str(auth.organization_id))
        )
        context["return_to"] = return_to or _safe_return_to(request)
        context["error"] = str(e)
        return templates.TemplateResponse(request, "expense/form.html", context)


# =============================================================================
# Expense Detail
# =============================================================================

@router.get("/{expense_id}", response_class=HTMLResponse)
def expense_detail(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense detail page."""
    context = base_context(request, auth, "Expense Detail", "expenses")
    context.update(
        expense_web_service.detail_context(
            db,
            str(auth.organization_id),
            expense_id,
        )
    )
    return templates.TemplateResponse(request, "expense/detail.html", context)


# =============================================================================
# Edit Expense
# =============================================================================

@router.get("/{expense_id}/edit", response_class=HTMLResponse)
def edit_expense_form(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Edit expense form."""
    context = base_context(request, auth, "Edit Expense", "expenses")
    context.update(
        expense_web_service.form_context(
            db,
            str(auth.organization_id),
            expense_id=expense_id,
        )
    )
    context["return_to"] = _safe_return_to(request, f"/expense/{expense_id}")
    return templates.TemplateResponse(request, "expense/form.html", context)


# =============================================================================
# Expense Actions
# =============================================================================

@router.post("/{expense_id}/submit", response_class=HTMLResponse)
def submit_expense(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Submit expense for approval."""
    try:
        expense_service.submit(db, str(auth.organization_id), expense_id, str(auth.user_id))
        db.commit()
    except Exception as e:
        db.rollback()
    return RedirectResponse(f"/expense/{expense_id}", status_code=303)


@router.post("/{expense_id}/approve", response_class=HTMLResponse)
def approve_expense(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Approve expense."""
    try:
        expense_service.approve(db, str(auth.organization_id), expense_id, str(auth.user_id))
        db.commit()
    except Exception as e:
        db.rollback()
    return RedirectResponse(f"/expense/{expense_id}", status_code=303)


@router.post("/{expense_id}/reject", response_class=HTMLResponse)
def reject_expense(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Reject expense."""
    try:
        expense_service.reject(db, str(auth.organization_id), expense_id, str(auth.user_id))
        db.commit()
    except Exception as e:
        db.rollback()
    return RedirectResponse(f"/expense/{expense_id}", status_code=303)


@router.post("/{expense_id}/post", response_class=HTMLResponse)
def post_expense(
    request: Request,
    expense_id: str,
    fiscal_period_id: str = Form(...),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Post expense to GL."""
    try:
        expense_service.post(db, str(auth.organization_id), expense_id, str(auth.user_id), fiscal_period_id)
        db.commit()
    except Exception as e:
        db.rollback()
    return RedirectResponse(f"/expense/{expense_id}", status_code=303)


@router.post("/{expense_id}/void", response_class=HTMLResponse)
def void_expense(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Void expense."""
    try:
        expense_service.void(db, expense_id, str(auth.user_id))
        db.commit()
    except Exception as e:
        db.rollback()
    return RedirectResponse(f"/expense/{expense_id}", status_code=303)


# =============================================================================
# Expense Reports
# =============================================================================


@router.get("/reports/summary", response_class=HTMLResponse)
def expense_summary_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense summary report page."""
    return expense_claims_web_service.expense_summary_report_response(
        request=request,
        auth=auth,
        db=db,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/reports/by-category", response_class=HTMLResponse)
def expense_by_category_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense by category report page."""
    return expense_claims_web_service.expense_by_category_report_response(
        request=request,
        auth=auth,
        db=db,
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/reports/by-employee", response_class=HTMLResponse)
def expense_by_employee_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    department_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense by employee report page."""
    return expense_claims_web_service.expense_by_employee_report_response(
        request=request,
        auth=auth,
        db=db,
        start_date=start_date,
        end_date=end_date,
        department_id=department_id,
    )


@router.get("/reports/trends", response_class=HTMLResponse)
def expense_trends_report(
    request: Request,
    months: int = Query(default=12, ge=3, le=24),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense trends report page."""
    return expense_claims_web_service.expense_trends_report_response(
        request=request,
        auth=auth,
        db=db,
        months=months,
    )


# =============================================================================
# Cash Advance Management
# =============================================================================

@router.get("/advances/list", response_class=HTMLResponse)
def cash_advances_list(
    request: Request,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Cash advances list page."""
    return expense_claims_web_service.cash_advances_list_response(
        request=request,
        auth=auth,
        db=db,
        status=status,
        page=page,
    )


@router.get("/advances/{advance_id}", response_class=HTMLResponse)
def cash_advance_detail(
    request: Request,
    advance_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Cash advance detail page with disburse/settle actions."""
    return expense_claims_web_service.cash_advance_detail_response(
        request=request,
        auth=auth,
        db=db,
        advance_id=advance_id,
    )


@router.post("/advances/{advance_id}/disburse", response_class=HTMLResponse)
def disburse_cash_advance(
    request: Request,
    advance_id: str,
    bank_account_id: str = Form(...),
    payment_mode: str = Form("BANK_TRANSFER"),
    payment_reference: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Disburse cash advance with GL posting."""
    return expense_claims_web_service.disburse_cash_advance_response(
        advance_id=advance_id,
        bank_account_id=bank_account_id,
        payment_mode=payment_mode,
        payment_reference=payment_reference,
        auth=auth,
        db=db,
    )


@router.post("/advances/{advance_id}/settle", response_class=HTMLResponse)
def settle_cash_advance(
    request: Request,
    advance_id: str,
    claim_id: str = Form(...),
    settlement_amount: Optional[str] = Form(None),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Settle cash advance against expense claim."""
    return expense_claims_web_service.settle_cash_advance_response(
        advance_id=advance_id,
        claim_id=claim_id,
        settlement_amount=settlement_amount,
        auth=auth,
        db=db,
    )
