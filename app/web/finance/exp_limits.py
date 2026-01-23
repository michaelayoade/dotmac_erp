"""
Expense Limits Web Routes.

HTML template routes for expense limit management:
- Limit rules (spending caps)
- Approver limits (approval authority)
- Usage dashboard
- Evaluation audit trail
"""
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.models.expense import LimitActionType, LimitPeriodType, LimitScopeType
from app.services.common import PaginationParams, coerce_uuid
from app.services.expense import ExpenseLimitService
from app.templates import templates
from app.web.deps import get_db, require_expense_access, WebAuthContext, base_context


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
    org_id = coerce_uuid(auth.organization_id)
    service = ExpenseLimitService(db)

    # Parse filters
    scope_enum = None
    if scope_type:
        try:
            scope_enum = LimitScopeType(scope_type.upper())
        except ValueError:
            pass

    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    # Paginate
    per_page = 25
    offset = (page - 1) * per_page

    result = service.list_rules(
        org_id,
        scope_type=scope_enum,
        is_active=active_filter,
        search=search,
        pagination=PaginationParams(offset=offset, limit=per_page),
    )

    # Calculate pagination
    total_pages = (result.total + per_page - 1) // per_page

    context = base_context(request, auth, "Expense Limits", "limits")
    context.update({
        "rules": result.items,
        "total": result.total,
        "page": page,
        "total_pages": total_pages,
        "scope_types": [s.value for s in LimitScopeType],
        "period_types": [p.value for p in LimitPeriodType],
        "action_types": [a.value for a in LimitActionType],
        "filters": {
            "scope_type": scope_type,
            "is_active": is_active,
            "search": search,
        },
    })
    return templates.TemplateResponse(request, "expense/limits/list.html", context)


@router.get("/limits/rules/new", response_class=HTMLResponse)
def new_limit_rule(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """New expense limit rule form."""
    org_id = coerce_uuid(auth.organization_id)

    # Get scope options (grades, departments, designations)
    scope_options = _get_scope_options(db, org_id)

    context = base_context(request, auth, "New Expense Limit Rule", "limits")
    context.update({
        "rule": None,
        "scope_types": [s.value for s in LimitScopeType],
        "period_types": [p.value for p in LimitPeriodType],
        "action_types": [a.value for a in LimitActionType],
        "scope_options": scope_options,
        "errors": {},
    })
    return templates.TemplateResponse(request, "expense/limits/rule_form.html", context)


@router.post("/limits/rules/new", response_class=HTMLResponse)
async def create_limit_rule(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Create new expense limit rule."""
    org_id = coerce_uuid(auth.organization_id)
    service = ExpenseLimitService(db)

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    rule_code = (form.get("rule_code") or "").strip()
    rule_name = (form.get("rule_name") or "").strip()
    description = (form.get("description") or "").strip()
    scope_type = (form.get("scope_type") or "").strip()
    scope_id = (form.get("scope_id") or "").strip()
    period_type = (form.get("period_type") or "").strip()
    limit_amount = (form.get("limit_amount") or "").strip()
    action_type = (form.get("action_type") or "").strip()
    priority = (form.get("priority") or "100").strip()
    effective_from = (form.get("effective_from") or "").strip()
    effective_to = (form.get("effective_to") or "").strip()
    is_active = (form.get("is_active") or "") in {"1", "true", "on", "yes"}

    errors = {}
    if not rule_code:
        errors["rule_code"] = "Required"
    if not rule_name:
        errors["rule_name"] = "Required"
    if not scope_type:
        errors["scope_type"] = "Required"
    if not period_type:
        errors["period_type"] = "Required"
    if not limit_amount:
        errors["limit_amount"] = "Required"
    if not action_type:
        errors["action_type"] = "Required"
    if not effective_from:
        errors["effective_from"] = "Required"

    limit_amount_value = None
    if limit_amount:
        try:
            limit_amount_value = Decimal(limit_amount)
        except Exception:
            errors["limit_amount"] = "Invalid amount"

    priority_value = 100
    if priority:
        try:
            priority_value = int(priority)
        except Exception:
            errors["priority"] = "Invalid number"

    effective_from_date = None
    if effective_from:
        try:
            effective_from_date = date.fromisoformat(effective_from)
        except Exception:
            errors["effective_from"] = "Invalid date"

    effective_to_date = None
    if effective_to:
        try:
            effective_to_date = date.fromisoformat(effective_to)
        except Exception:
            errors["effective_to"] = "Invalid date"

    scope_options = _get_scope_options(db, org_id)

    if errors:
        context = base_context(request, auth, "New Expense Limit Rule", "limits")
        context.update({
            "rule": {
                "rule_code": rule_code,
                "rule_name": rule_name,
                "description": description,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "period_type": period_type,
                "limit_amount": limit_amount,
                "action_type": action_type,
                "priority": priority,
                "effective_from": effective_from,
                "effective_to": effective_to,
                "is_active": is_active,
            },
            "scope_types": [s.value for s in LimitScopeType],
            "period_types": [p.value for p in LimitPeriodType],
            "action_types": [a.value for a in LimitActionType],
            "scope_options": scope_options,
            "errors": errors,
        })
        return templates.TemplateResponse(request, "expense/limits/rule_form.html", context)

    try:
        service.create_rule(
            org_id,
            rule_code=rule_code,
            rule_name=rule_name,
            description=description or None,
            scope_type=LimitScopeType(scope_type.upper()),
            scope_id=coerce_uuid(scope_id) if scope_id else None,
            period_type=LimitPeriodType(period_type.upper()),
            limit_amount=limit_amount_value,
            action_type=LimitActionType(action_type.upper()),
            priority=priority_value,
            effective_from=effective_from_date,
            effective_to=effective_to_date,
            is_active=is_active,
        )
        db.commit()
        return RedirectResponse(url="/expense/limits/rules", status_code=303)
    except Exception as e:
        db.rollback()
        errors["_form"] = str(e)
        context = base_context(request, auth, "New Expense Limit Rule", "limits")
        context.update({
            "rule": {
                "rule_code": rule_code,
                "rule_name": rule_name,
                "description": description,
                "scope_type": scope_type,
                "scope_id": scope_id,
                "period_type": period_type,
                "limit_amount": limit_amount,
                "action_type": action_type,
                "priority": priority,
                "effective_from": effective_from,
                "effective_to": effective_to,
                "is_active": is_active,
            },
            "scope_types": [s.value for s in LimitScopeType],
            "period_types": [p.value for p in LimitPeriodType],
            "action_types": [a.value for a in LimitActionType],
            "scope_options": scope_options,
            "errors": errors,
        })
        return templates.TemplateResponse(request, "expense/limits/rule_form.html", context)


@router.get("/limits/rules/{rule_id}/edit", response_class=HTMLResponse)
def edit_limit_rule(
    request: Request,
    rule_id: UUID,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Edit expense limit rule form."""
    org_id = coerce_uuid(auth.organization_id)
    service = ExpenseLimitService(db)

    try:
        rule = service.get_rule(org_id, rule_id)
    except Exception:
        return RedirectResponse(url="/expense/limits/rules", status_code=303)

    scope_options = _get_scope_options(db, org_id)

    context = base_context(request, auth, f"Edit Rule: {rule.rule_code}", "limits")
    context.update({
        "rule": rule,
        "scope_types": [s.value for s in LimitScopeType],
        "period_types": [p.value for p in LimitPeriodType],
        "action_types": [a.value for a in LimitActionType],
        "scope_options": scope_options,
        "errors": {},
    })
    return templates.TemplateResponse(request, "expense/limits/rule_form.html", context)


@router.post("/limits/rules/{rule_id}/edit", response_class=HTMLResponse)
async def update_limit_rule(
    request: Request,
    rule_id: UUID,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Update expense limit rule."""
    org_id = coerce_uuid(auth.organization_id)
    service = ExpenseLimitService(db)

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    rule_name = (form.get("rule_name") or "").strip()
    description = (form.get("description") or "").strip()
    limit_amount = (form.get("limit_amount") or "").strip()
    action_type = (form.get("action_type") or "").strip()
    priority = (form.get("priority") or "100").strip()
    effective_to = (form.get("effective_to") or "").strip()
    is_active = (form.get("is_active") or "") in {"1", "true", "on", "yes"}

    errors = {}
    update_data = {}

    if rule_name:
        update_data["rule_name"] = rule_name
    if description:
        update_data["description"] = description
    if limit_amount:
        try:
            update_data["limit_amount"] = Decimal(limit_amount)
        except Exception:
            errors["limit_amount"] = "Invalid amount"
    if action_type:
        try:
            update_data["action_type"] = LimitActionType(action_type.upper())
        except Exception:
            errors["action_type"] = "Invalid action type"
    if priority:
        try:
            update_data["priority"] = int(priority)
        except Exception:
            errors["priority"] = "Invalid number"
    if effective_to:
        try:
            update_data["effective_to"] = date.fromisoformat(effective_to)
        except Exception:
            errors["effective_to"] = "Invalid date"

    update_data["is_active"] = is_active

    scope_options = _get_scope_options(db, org_id)

    if errors:
        rule = service.get_rule(org_id, rule_id)
        context = base_context(request, auth, f"Edit Rule: {rule.rule_code}", "limits")
        context.update({
            "rule": rule,
            "scope_types": [s.value for s in LimitScopeType],
            "period_types": [p.value for p in LimitPeriodType],
            "action_types": [a.value for a in LimitActionType],
            "scope_options": scope_options,
            "errors": errors,
        })
        return templates.TemplateResponse(request, "expense/limits/rule_form.html", context)

    try:
        service.update_rule(org_id, rule_id, **update_data)
        db.commit()
        return RedirectResponse(url="/expense/limits/rules", status_code=303)
    except Exception as e:
        db.rollback()
        rule = service.get_rule(org_id, rule_id)
        errors["_form"] = str(e)
        context = base_context(request, auth, f"Edit Rule: {rule.rule_code}", "limits")
        context.update({
            "rule": rule,
            "scope_types": [s.value for s in LimitScopeType],
            "period_types": [p.value for p in LimitPeriodType],
            "action_types": [a.value for a in LimitActionType],
            "scope_options": scope_options,
            "errors": errors,
        })
        return templates.TemplateResponse(request, "expense/limits/rule_form.html", context)


@router.post("/limits/rules/{rule_id}/delete", response_class=HTMLResponse)
async def delete_limit_rule(
    request: Request,
    rule_id: UUID,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Delete expense limit rule."""
    org_id = coerce_uuid(auth.organization_id)
    service = ExpenseLimitService(db)

    try:
        service.delete_rule(org_id, rule_id)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url="/expense/limits/rules", status_code=303)


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
    org_id = coerce_uuid(auth.organization_id)
    service = ExpenseLimitService(db)

    # Parse filters
    active_filter = None
    if is_active == "true":
        active_filter = True
    elif is_active == "false":
        active_filter = False

    # Paginate
    per_page = 25
    offset = (page - 1) * per_page

    result = service.list_approver_limits(
        org_id,
        scope_type=scope_type,
        is_active=active_filter,
        pagination=PaginationParams(offset=offset, limit=per_page),
    )

    total_pages = (result.total + per_page - 1) // per_page

    context = base_context(request, auth, "Approver Limits", "limits")
    context.update({
        "approver_limits": result.items,
        "total": result.total,
        "page": page,
        "total_pages": total_pages,
        "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
        "filters": {
            "scope_type": scope_type,
            "is_active": is_active,
        },
    })
    return templates.TemplateResponse(request, "expense/limits/approvers.html", context)


@router.get("/limits/approvers/new", response_class=HTMLResponse)
def new_approver_limit(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """New expense approver limit form."""
    org_id = coerce_uuid(auth.organization_id)
    scope_options = _get_scope_options(db, org_id)

    context = base_context(request, auth, "New Approver Limit", "limits")
    context.update({
        "approver_limit": None,
        "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
        "scope_options": scope_options,
        "errors": {},
    })
    return templates.TemplateResponse(request, "expense/limits/approver_form.html", context)


@router.post("/limits/approvers/new", response_class=HTMLResponse)
async def create_approver_limit(
    request: Request,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Create new expense approver limit."""
    org_id = coerce_uuid(auth.organization_id)
    service = ExpenseLimitService(db)

    form = getattr(request.state, "csrf_form", None)
    if form is None:
        form = await request.form()

    scope_type = (form.get("scope_type") or "").strip()
    scope_id = (form.get("scope_id") or "").strip()
    max_approval_amount = (form.get("max_approval_amount") or "").strip()
    can_approve_own = (form.get("can_approve_own_expenses") or "") in {"1", "true", "on", "yes"}
    is_active = (form.get("is_active") or "") in {"1", "true", "on", "yes"}

    errors = {}
    if not scope_type:
        errors["scope_type"] = "Required"
    if not max_approval_amount:
        errors["max_approval_amount"] = "Required"

    max_amount_value = None
    if max_approval_amount:
        try:
            max_amount_value = Decimal(max_approval_amount)
        except Exception:
            errors["max_approval_amount"] = "Invalid amount"

    scope_options = _get_scope_options(db, org_id)

    if errors:
        context = base_context(request, auth, "New Approver Limit", "limits")
        context.update({
            "approver_limit": {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "max_approval_amount": max_approval_amount,
                "can_approve_own_expenses": can_approve_own,
                "is_active": is_active,
            },
            "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
            "scope_options": scope_options,
            "errors": errors,
        })
        return templates.TemplateResponse(request, "expense/limits/approver_form.html", context)

    try:
        service.create_approver_limit(
            org_id,
            scope_type=scope_type,
            scope_id=coerce_uuid(scope_id) if scope_id else None,
            max_approval_amount=max_amount_value,
            can_approve_own_expenses=can_approve_own,
            is_active=is_active,
        )
        db.commit()
        return RedirectResponse(url="/expense/limits/approvers", status_code=303)
    except Exception as e:
        db.rollback()
        errors["_form"] = str(e)
        context = base_context(request, auth, "New Approver Limit", "limits")
        context.update({
            "approver_limit": {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "max_approval_amount": max_approval_amount,
                "can_approve_own_expenses": can_approve_own,
                "is_active": is_active,
            },
            "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
            "scope_options": scope_options,
            "errors": errors,
        })
        return templates.TemplateResponse(request, "expense/limits/approver_form.html", context)


@router.post("/limits/approvers/{approver_limit_id}/delete", response_class=HTMLResponse)
async def delete_approver_limit(
    request: Request,
    approver_limit_id: UUID,
    auth: WebAuthContext = Depends(require_expense_access),
    db: Session = Depends(get_db),
):
    """Delete expense approver limit."""
    org_id = coerce_uuid(auth.organization_id)
    service = ExpenseLimitService(db)

    try:
        service.delete_approver_limit(org_id, approver_limit_id)
        db.commit()
    except Exception:
        db.rollback()

    return RedirectResponse(url="/expense/limits/approvers", status_code=303)


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
    org_id = coerce_uuid(auth.organization_id)
    service = ExpenseLimitService(db)

    # Get employee list for selection
    from app.models.people.hr.employee import Employee

    employees = list(
        db.query(Employee)
        .filter(Employee.organization_id == org_id, Employee.is_active == True)
        .order_by(Employee.first_name)
        .limit(200)
        .all()
    )

    usage_summary = None
    if employee_id:
        try:
            emp_uuid = coerce_uuid(employee_id)
            usage_summary = service.get_employee_usage_summary(org_id, emp_uuid)
        except Exception:
            pass

    context = base_context(request, auth, "Expense Usage", "limits")
    context.update({
        "employees": employees,
        "selected_employee_id": employee_id,
        "usage_summary": usage_summary,
    })
    return templates.TemplateResponse(request, "expense/limits/usage.html", context)


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
    from app.models.expense import LimitResultType

    org_id = coerce_uuid(auth.organization_id)
    service = ExpenseLimitService(db)

    # Parse filters
    result_enum = None
    if result:
        try:
            result_enum = LimitResultType(result.upper())
        except ValueError:
            pass

    from_date_parsed = None
    if from_date:
        try:
            from_date_parsed = date.fromisoformat(from_date)
        except Exception:
            pass

    to_date_parsed = None
    if to_date:
        try:
            to_date_parsed = date.fromisoformat(to_date)
        except Exception:
            pass

    # Paginate
    per_page = 25
    offset = (page - 1) * per_page

    evaluations = service.list_evaluations(
        org_id,
        result=result_enum,
        from_date=from_date_parsed,
        to_date=to_date_parsed,
        pagination=PaginationParams(offset=offset, limit=per_page),
    )

    total_pages = (evaluations.total + per_page - 1) // per_page

    context = base_context(request, auth, "Limit Evaluations", "limits")
    context.update({
        "evaluations": evaluations.items,
        "total": evaluations.total,
        "page": page,
        "total_pages": total_pages,
        "result_types": [r.value for r in LimitResultType],
        "filters": {
            "result": result,
            "from_date": from_date,
            "to_date": to_date,
        },
    })
    return templates.TemplateResponse(request, "expense/limits/evaluations.html", context)


# =============================================================================
# Helper Functions
# =============================================================================


def _get_scope_options(db: Session, org_id: UUID) -> dict:
    """Get scope options for dropdowns (grades, departments, designations, employees)."""
    from app.models.people.hr.employee import Employee
    from app.models.people.hr.employee_grade import EmployeeGrade
    from app.models.people.hr.designation import Designation
    from app.models.people.hr.department import Department

    grades = list(
        db.query(EmployeeGrade)
        .filter(EmployeeGrade.organization_id == org_id, EmployeeGrade.is_active == True)
        .order_by(EmployeeGrade.rank.desc())
        .all()
    )

    departments = list(
        db.query(Department)
        .filter(Department.organization_id == org_id, Department.is_active == True)
        .order_by(Department.name)
        .all()
    )

    designations = list(
        db.query(Designation)
        .filter(Designation.organization_id == org_id, Designation.is_active == True)
        .order_by(Designation.name)
        .all()
    )

    employees = list(
        db.query(Employee)
        .filter(Employee.organization_id == org_id, Employee.is_active == True)
        .order_by(Employee.first_name)
        .limit(100)
        .all()
    )

    return {
        "grades": grades,
        "departments": departments,
        "designations": designations,
        "employees": employees,
    }
