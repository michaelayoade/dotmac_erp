"""
Expense Limits Web Routes.

HTML template routes for expense limit management:
- Limit rules (spending caps)
- Approver limits (approval authority)
- Usage dashboard
- Evaluation audit trail
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.services.expense.limit_web import expense_limit_web_service
from app.web.deps import WebAuthContext, get_db, require_expense_access


router = APIRouter(tags=["expense-limits-web"])


# =============================================================================
# Limit Rules
# =============================================================================


@router.get("/limits", response_class=HTMLResponse)
@router.get("/limits/rules", response_class=HTMLResponse)
def limit_rules_list(
    request: Request,
    scope_type: Optional[str] = None,
    is_active: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """List expense limit rules."""
    return expense_limit_web_service.limit_rules_list_response(
        request=request,
        auth=auth,
        db=db,
        scope_type=scope_type,
        is_active=is_active,
        search=search,
        page=page,
    )


@router.get("/limits/rules/new", response_class=HTMLResponse)
def new_limit_rule(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """New expense limit rule form."""
    return expense_limit_web_service.new_limit_rule_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/limits/rules/new", response_class=HTMLResponse)
async def create_limit_rule(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Create new expense limit rule."""
    return await expense_limit_web_service.create_limit_rule_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.get("/limits/rules/{rule_id}/edit", response_class=HTMLResponse)
def edit_limit_rule(
    request: Request,
    rule_id: UUID,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Edit expense limit rule form."""
    return expense_limit_web_service.edit_limit_rule_form_response(
        request=request,
        rule_id=rule_id,
        auth=auth,
        db=db,
    )


@router.post("/limits/rules/{rule_id}/edit", response_class=HTMLResponse)
async def update_limit_rule(
    request: Request,
    rule_id: UUID,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Update expense limit rule."""
    return await expense_limit_web_service.update_limit_rule_response(
        request=request,
        rule_id=rule_id,
        auth=auth,
        db=db,
    )


@router.post("/limits/rules/{rule_id}/delete", response_class=HTMLResponse)
async def delete_limit_rule(
    request: Request,
    rule_id: UUID,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Delete expense limit rule."""
    return expense_limit_web_service.delete_limit_rule_response(
        rule_id=rule_id,
        auth=auth,
        db=db,
    )


# =============================================================================
# Approver Limits
# =============================================================================


@router.get("/limits/approvers", response_class=HTMLResponse)
def approver_limits_list(
    request: Request,
    scope_type: Optional[str] = None,
    is_active: Optional[str] = None,
    page: int = Query(1, ge=1),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """List expense approver limits."""
    return expense_limit_web_service.approver_limits_list_response(
        request=request,
        auth=auth,
        db=db,
        scope_type=scope_type,
        is_active=is_active,
        page=page,
    )


@router.get("/limits/approvers/new", response_class=HTMLResponse)
def new_approver_limit(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """New expense approver limit form."""
    return expense_limit_web_service.new_approver_limit_form_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/limits/approvers/new", response_class=HTMLResponse)
async def create_approver_limit(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Create new expense approver limit."""
    return await expense_limit_web_service.create_approver_limit_response(
        request=request,
        auth=auth,
        db=db,
    )


@router.post("/limits/approvers/{approver_limit_id}/delete", response_class=HTMLResponse)
async def delete_approver_limit(
    request: Request,
    approver_limit_id: UUID,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Delete expense approver limit."""
    return expense_limit_web_service.delete_approver_limit_response(
        approver_limit_id=approver_limit_id,
        auth=auth,
        db=db,
    )


# =============================================================================
# Usage & Evaluations
# =============================================================================


@router.get("/limits/usage", response_class=HTMLResponse)
def usage_dashboard(
    request: Request,
    employee_id: Optional[str] = None,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Employee expense usage dashboard."""
    return expense_limit_web_service.usage_dashboard_response(
        request=request,
        auth=auth,
        db=db,
        employee_id=employee_id,
    )


@router.get("/limits/evaluations", response_class=HTMLResponse)
def evaluations_list(
    request: Request,
    result: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = Query(1, ge=1),
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """List expense limit evaluations (audit trail)."""
    return expense_limit_web_service.evaluations_list_response(
        request=request,
        auth=auth,
        db=db,
        result=result,
        from_date=from_date,
        to_date=to_date,
        page=page,
    )
