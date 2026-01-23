"""
Expense Web Routes.

HTML template routes for expense management.
"""
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.services.expense.dashboard_web import expense_dashboard_service
from app.services.finance.exp.expense import expense_service
from app.services.expense.expense_service import ExpenseService, ExpenseServiceError
from app.services.common import PaginationParams, coerce_uuid
from app.services.finance.exp.web import expense_web_service
from app.templates import templates
from app.web.deps import get_db, require_expense_access, WebAuthContext, base_context
from app.web.finance.exp_limits import router as limits_router


router = APIRouter(tags=["expense-web"])

# Include limits sub-router
router.include_router(limits_router)


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
    context = base_context(request, auth, "Expense Claims", "claims")
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
    is_active: Optional[bool] = None,
    page: int = Query(default=1, ge=1),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense categories page."""
    org_id = coerce_uuid(auth.organization_id)
    svc = ExpenseService(db)

    pagination = PaginationParams.from_page(page, 20)
    result = svc.list_categories(
        org_id,
        search=search,
        is_active=is_active,
        pagination=pagination,
    )

    context = base_context(request, auth, "Expense Categories", "categories")
    context.update(
        {
            "categories": result.items,
            "search": search or "",
            "is_active": is_active,
            "page": page,
            "total_pages": result.total_pages,
            "total": result.total,
            "limit": pagination.limit,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        }
    )
    return templates.TemplateResponse(request, "expense/categories.html", context)


@router.get("/categories/new", response_class=HTMLResponse)
def new_expense_category_form(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """New expense category form."""
    from app.models.finance.gl.account import Account
    from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

    org_id = coerce_uuid(auth.organization_id)

    expense_accounts = (
        db.query(Account)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .filter(
            Account.organization_id == org_id,
            AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
            Account.is_active.is_(True),
            AccountCategory.is_active.is_(True),
        )
        .order_by(Account.account_code)
        .all()
    )

    context = base_context(request, auth, "New Expense Category", "categories")
    context.update(
        {
            "category": None,
            "expense_accounts": expense_accounts,
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "expense/category_form.html", context)


@router.post("/categories/new", response_class=HTMLResponse)
async def create_expense_category(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Create new expense category."""
    from app.models.finance.gl.account import Account
    from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    category_code = (form.get("category_code") or "").strip()
    category_name = (form.get("category_name") or "").strip()
    description = (form.get("description") or "").strip()
    expense_account_id = (form.get("expense_account_id") or "").strip()
    max_amount = (form.get("max_amount_per_claim") or "").strip()
    requires_receipt = (form.get("requires_receipt") or "").strip() in {"1", "true", "on", "yes"}
    is_active = (form.get("is_active") or "").strip() in {"1", "true", "on", "yes"}

    errors = {}
    if not category_code:
        errors["category_code"] = "Required"
    if not category_name:
        errors["category_name"] = "Required"

    max_amount_value = None
    if max_amount:
        try:
            max_amount_value = Decimal(max_amount)
        except Exception:
            errors["max_amount_per_claim"] = "Invalid amount"

    org_id = coerce_uuid(auth.organization_id)
    svc = ExpenseService(db)

    if errors:
        expense_accounts = (
            db.query(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .filter(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )
        context = base_context(request, auth, "New Expense Category", "categories")
        context.update(
            {
                "category": {
                    "category_code": category_code,
                    "category_name": category_name,
                    "description": description,
                    "expense_account_id": expense_account_id,
                    "max_amount_per_claim": max_amount,
                    "requires_receipt": requires_receipt,
                    "is_active": is_active,
                },
                "expense_accounts": expense_accounts,
                "errors": errors,
            }
        )
        return templates.TemplateResponse(request, "expense/category_form.html", context)

    try:
        category = svc.create_category(
            org_id,
            category_code=category_code,
            category_name=category_name,
            description=description or None,
            expense_account_id=coerce_uuid(expense_account_id) if expense_account_id else None,
            max_amount_per_claim=max_amount_value,
            requires_receipt=requires_receipt if requires_receipt else False,
            is_active=is_active if is_active else False,
        )
        db.commit()
    except ExpenseServiceError as exc:
        db.rollback()
        expense_accounts = (
            db.query(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .filter(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )
        context = base_context(request, auth, "New Expense Category", "categories")
        context.update(
            {
                "category": {
                    "category_code": category_code,
                    "category_name": category_name,
                    "description": description,
                    "expense_account_id": expense_account_id,
                    "max_amount_per_claim": max_amount,
                    "requires_receipt": requires_receipt,
                    "is_active": is_active,
                },
                "expense_accounts": expense_accounts,
                "errors": {"_": str(exc)},
            }
        )
        return templates.TemplateResponse(request, "expense/category_form.html", context)

    return RedirectResponse(url="/expense/categories", status_code=303)


@router.get("/categories/{category_id}/edit", response_class=HTMLResponse)
def edit_expense_category_form(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Edit expense category form."""
    from app.models.finance.gl.account import Account
    from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

    org_id = coerce_uuid(auth.organization_id)
    svc = ExpenseService(db)
    category = svc.get_category(org_id, coerce_uuid(category_id))

    expense_accounts = (
        db.query(Account)
        .join(AccountCategory, Account.category_id == AccountCategory.category_id)
        .filter(
            Account.organization_id == org_id,
            AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
            Account.is_active.is_(True),
            AccountCategory.is_active.is_(True),
        )
        .order_by(Account.account_code)
        .all()
    )

    context = base_context(request, auth, "Edit Expense Category", "categories")
    context.update(
        {
            "category": category,
            "expense_accounts": expense_accounts,
            "errors": {},
        }
    )
    return templates.TemplateResponse(request, "expense/category_form.html", context)


@router.post("/categories/{category_id}/edit", response_class=HTMLResponse)
async def update_expense_category(
    request: Request,
    category_id: str,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Update expense category."""
    from app.models.finance.gl.account import Account
    from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    category_code = (form.get("category_code") or "").strip()
    category_name = (form.get("category_name") or "").strip()
    description = (form.get("description") or "").strip()
    expense_account_id = (form.get("expense_account_id") or "").strip()
    max_amount = (form.get("max_amount_per_claim") or "").strip()
    requires_receipt = (form.get("requires_receipt") or "").strip() in {"1", "true", "on", "yes"}
    is_active = (form.get("is_active") or "").strip() in {"1", "true", "on", "yes"}

    errors = {}
    if not category_code:
        errors["category_code"] = "Required"
    if not category_name:
        errors["category_name"] = "Required"

    max_amount_value = None
    if max_amount:
        try:
            max_amount_value = Decimal(max_amount)
        except Exception:
            errors["max_amount_per_claim"] = "Invalid amount"

    org_id = coerce_uuid(auth.organization_id)
    svc = ExpenseService(db)

    if errors:
        expense_accounts = (
            db.query(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .filter(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
            .all()
        )
        context = base_context(request, auth, "Edit Expense Category", "categories")
        context.update(
            {
                "category": {
                    "category_id": category_id,
                    "category_code": category_code,
                    "category_name": category_name,
                    "description": description,
                    "expense_account_id": expense_account_id,
                    "max_amount_per_claim": max_amount,
                    "requires_receipt": requires_receipt,
                    "is_active": is_active,
                },
                "expense_accounts": expense_accounts,
                "errors": errors,
            }
        )
        return templates.TemplateResponse(request, "expense/category_form.html", context)

    svc.update_category(
        org_id,
        coerce_uuid(category_id),
        category_code=category_code,
        category_name=category_name,
        description=description or None,
        expense_account_id=coerce_uuid(expense_account_id) if expense_account_id else None,
        max_amount_per_claim=max_amount_value,
        requires_receipt=requires_receipt,
        is_active=is_active,
    )
    db.commit()

    return RedirectResponse(url="/expense/categories", status_code=303)


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
        expense_service.submit(db, expense_id, str(auth.user_id))
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
        expense_service.approve(db, expense_id, str(auth.user_id))
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
        expense_service.reject(db, expense_id, str(auth.user_id))
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
        expense_service.post(db, expense_id, str(auth.user_id), fiscal_period_id)
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
    from datetime import date
    from app.services.expense.expense_service import ExpenseClaimService
    from app.services.common import coerce_uuid

    org_id = coerce_uuid(auth.organization_id)
    svc = ExpenseClaimService(db)

    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None

    report_data = svc.get_expense_summary_report(
        org_id,
        start_date=parsed_start,
        end_date=parsed_end,
    )

    context = base_context(request, auth, "Expense Summary Report", "expense")
    context.update({
        "report": report_data,
        "start_date": start_date or report_data["start_date"].isoformat(),
        "end_date": end_date or report_data["end_date"].isoformat(),
    })
    return templates.TemplateResponse(request, "expense/reports/summary.html", context)


@router.get("/reports/by-category", response_class=HTMLResponse)
def expense_by_category_report(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense by category report page."""
    from datetime import date
    from app.services.expense.expense_service import ExpenseClaimService
    from app.services.common import coerce_uuid

    org_id = coerce_uuid(auth.organization_id)
    svc = ExpenseClaimService(db)

    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None

    report_data = svc.get_expense_by_category_report(
        org_id,
        start_date=parsed_start,
        end_date=parsed_end,
    )

    context = base_context(request, auth, "Expense by Category Report", "expense")
    context.update({
        "report": report_data,
        "start_date": start_date or report_data["start_date"].isoformat(),
        "end_date": end_date or report_data["end_date"].isoformat(),
    })
    return templates.TemplateResponse(request, "expense/reports/by_category.html", context)


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
    from datetime import date
    from app.services.expense.expense_service import ExpenseClaimService
    from app.services.common import coerce_uuid
    from app.services.people.hr import OrganizationService, DepartmentFilters, PaginationParams

    org_id = coerce_uuid(auth.organization_id)
    svc = ExpenseClaimService(db)
    org_svc = OrganizationService(db, org_id)

    parsed_start = date.fromisoformat(start_date) if start_date else None
    parsed_end = date.fromisoformat(end_date) if end_date else None
    parsed_dept = coerce_uuid(department_id) if department_id else None

    report_data = svc.get_expense_by_employee_report(
        org_id,
        start_date=parsed_start,
        end_date=parsed_end,
        department_id=parsed_dept,
    )

    departments = org_svc.list_departments(
        DepartmentFilters(is_active=True),
        PaginationParams(limit=200),
    ).items

    context = base_context(request, auth, "Expense by Employee Report", "expense")
    context.update({
        "report": report_data,
        "departments": departments,
        "start_date": start_date or report_data["start_date"].isoformat(),
        "end_date": end_date or report_data["end_date"].isoformat(),
        "department_id": department_id,
    })
    return templates.TemplateResponse(request, "expense/reports/by_employee.html", context)


@router.get("/reports/trends", response_class=HTMLResponse)
def expense_trends_report(
    request: Request,
    months: int = Query(default=12, ge=3, le=24),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Expense trends report page."""
    from app.services.expense.expense_service import ExpenseClaimService
    from app.services.common import coerce_uuid

    org_id = coerce_uuid(auth.organization_id)
    svc = ExpenseClaimService(db)

    report_data = svc.get_expense_trends_report(org_id, months=months)

    context = base_context(request, auth, "Expense Trends Report", "expense")
    context.update({
        "report": report_data,
        "months": months,
    })
    return templates.TemplateResponse(request, "expense/reports/trends.html", context)
