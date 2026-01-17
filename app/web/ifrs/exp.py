"""
Expense Web Routes.

HTML template routes for expense management.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.ifrs.exp.expense import expense_service
from app.services.ifrs.exp.web import expense_web_service
from app.templates import templates
from app.web.deps import get_db, require_web_auth, WebAuthContext, base_context


router = APIRouter(prefix="/expenses", tags=["expenses-web"])


# =============================================================================
# Expense List
# =============================================================================

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def expense_list(
    request: Request,
    status: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Expense list page."""
    context = base_context(request, auth, "Expenses", "expenses")
    context.update(
        expense_web_service.list_context(
            db,
            str(auth.organization_id),
            status=status,
            start_date=start_date,
            end_date=end_date,
        )
    )
    return templates.TemplateResponse(request, "ifrs/exp/list.html", context)


# =============================================================================
# New Expense
# =============================================================================

@router.get("/new", response_class=HTMLResponse)
def new_expense_form(
    request: Request,
    auth: WebAuthContext = Depends(require_web_auth),
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
    return templates.TemplateResponse(request, "ifrs/exp/form.html", context)


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
    auth: WebAuthContext = Depends(require_web_auth),
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
        return RedirectResponse(f"/expenses/{expense.expense_id}", status_code=303)
    except Exception as e:
        db.rollback()
        context = base_context(request, auth, "New Expense", "expenses")
        context.update(
            expense_web_service.form_context(db, str(auth.organization_id))
        )
        context["error"] = str(e)
        return templates.TemplateResponse(request, "ifrs/exp/form.html", context)


# =============================================================================
# Expense Detail
# =============================================================================

@router.get("/{expense_id}", response_class=HTMLResponse)
def expense_detail(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
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
    return templates.TemplateResponse(request, "ifrs/exp/detail.html", context)


# =============================================================================
# Edit Expense
# =============================================================================

@router.get("/{expense_id}/edit", response_class=HTMLResponse)
def edit_expense_form(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
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
    return templates.TemplateResponse(request, "ifrs/exp/form.html", context)


# =============================================================================
# Expense Actions
# =============================================================================

@router.post("/{expense_id}/submit", response_class=HTMLResponse)
def submit_expense(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Submit expense for approval."""
    try:
        expense_service.submit(db, expense_id, str(auth.user_id))
        db.commit()
    except Exception as e:
        db.rollback()
    return RedirectResponse(f"/expenses/{expense_id}", status_code=303)


@router.post("/{expense_id}/approve", response_class=HTMLResponse)
def approve_expense(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Approve expense."""
    try:
        expense_service.approve(db, expense_id, str(auth.user_id))
        db.commit()
    except Exception as e:
        db.rollback()
    return RedirectResponse(f"/expenses/{expense_id}", status_code=303)


@router.post("/{expense_id}/reject", response_class=HTMLResponse)
def reject_expense(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Reject expense."""
    try:
        expense_service.reject(db, expense_id, str(auth.user_id))
        db.commit()
    except Exception as e:
        db.rollback()
    return RedirectResponse(f"/expenses/{expense_id}", status_code=303)


@router.post("/{expense_id}/post", response_class=HTMLResponse)
def post_expense(
    request: Request,
    expense_id: str,
    fiscal_period_id: str = Form(...),
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Post expense to GL."""
    try:
        expense_service.post(db, expense_id, str(auth.user_id), fiscal_period_id)
        db.commit()
    except Exception as e:
        db.rollback()
    return RedirectResponse(f"/expenses/{expense_id}", status_code=303)


@router.post("/{expense_id}/void", response_class=HTMLResponse)
def void_expense(
    request: Request,
    expense_id: str,
    auth: WebAuthContext = Depends(require_web_auth),
    db: Session = Depends(get_db),
):
    """Void expense."""
    try:
        expense_service.void(db, expense_id, str(auth.user_id))
        db.commit()
    except Exception as e:
        db.rollback()
    return RedirectResponse(f"/expenses/{expense_id}", status_code=303)
