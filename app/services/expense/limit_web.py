"""Expense limit web service helpers."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.expense import LimitActionType, LimitPeriodType, LimitScopeType
from app.models.expense.limit_rule import (
    ExpenseApproverLimit,
)
from app.services.common import PaginationParams, coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.expense import ExpenseLimitService
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def _safe_form_text(value: object | None, default: str = "") -> str:
    if isinstance(value, str):
        return value.strip()
    return default


class ExpenseLimitWebService:
    """Service layer for expense limit web routes."""

    def limit_rules_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        scope_type: str | None = None,
        is_active: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
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

        active_filters = build_active_filters(
            params={"scope_type": scope_type, "is_active": is_active},
        )
        context = base_context(request, auth, "Expense Limits", "limits")
        context.update(
            {
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
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(request, "expense/limits/list.html", context)

    def new_limit_rule_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New expense limit rule form."""
        org_id = coerce_uuid(auth.organization_id)

        # Get scope options (grades, departments, designations)
        scope_options = self._get_scope_options(db, org_id)

        context = base_context(request, auth, "New Expense Limit Rule", "limits")
        context.update(
            {
                "rule": None,
                "scope_types": [s.value for s in LimitScopeType],
                "period_types": [p.value for p in LimitPeriodType],
                "action_types": [a.value for a in LimitActionType],
                "scope_options": scope_options,
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "expense/limits/rule_form.html", context
        )

    async def create_limit_rule_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new expense limit rule."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        rule_code = _safe_form_text(form.get("rule_code"))
        rule_name = _safe_form_text(form.get("rule_name"))
        description = _safe_form_text(form.get("description"))
        scope_type = _safe_form_text(form.get("scope_type"))
        scope_id = _safe_form_text(form.get("scope_id"))
        period_type = _safe_form_text(form.get("period_type"))
        limit_amount = _safe_form_text(form.get("limit_amount"))
        action_type = _safe_form_text(form.get("action_type"))
        priority = _safe_form_text(form.get("priority"), "100")
        effective_from = _safe_form_text(form.get("effective_from"))
        effective_to = _safe_form_text(form.get("effective_to"))
        is_active = _safe_form_text(form.get("is_active")) in {"1", "true", "on", "yes"}

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

        if limit_amount_value is None:
            errors["limit_amount"] = errors.get("limit_amount") or "Required"
        if effective_from_date is None:
            errors["effective_from"] = errors.get("effective_from") or "Required"

        scope_options = self._get_scope_options(db, org_id)

        if errors:
            context = base_context(request, auth, "New Expense Limit Rule", "limits")
            context.update(
                {
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
                }
            )
            return templates.TemplateResponse(
                request, "expense/limits/rule_form.html", context
            )

        try:
            if limit_amount_value is None or effective_from_date is None:
                raise ValueError("Missing required form values")
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
            return RedirectResponse(
                url="/expense/limits/rules?success=Record+saved+successfully",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            errors["_form"] = str(e)
            context = base_context(request, auth, "New Expense Limit Rule", "limits")
            context.update(
                {
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
                }
            )
            return templates.TemplateResponse(
                request, "expense/limits/rule_form.html", context
            )

    def edit_limit_rule_form_response(
        self,
        request: Request,
        rule_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Edit expense limit rule form."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)

        try:
            rule = service.get_rule(org_id, rule_id)
        except Exception:
            return RedirectResponse(url="/expense/limits/rules", status_code=303)

        scope_options = self._get_scope_options(db, org_id)

        context = base_context(request, auth, f"Edit Rule: {rule.rule_code}", "limits")
        context.update(
            {
                "rule": rule,
                "scope_types": [s.value for s in LimitScopeType],
                "period_types": [p.value for p in LimitPeriodType],
                "action_types": [a.value for a in LimitActionType],
                "scope_options": scope_options,
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "expense/limits/rule_form.html", context
        )

    async def update_limit_rule_response(
        self,
        request: Request,
        rule_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Update expense limit rule."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        rule_name = _safe_form_text(form.get("rule_name"))
        description = _safe_form_text(form.get("description"))
        limit_amount = _safe_form_text(form.get("limit_amount"))
        action_type = _safe_form_text(form.get("action_type"))
        priority = _safe_form_text(form.get("priority"), "100")
        effective_to = _safe_form_text(form.get("effective_to"))
        is_active = _safe_form_text(form.get("is_active")) in {"1", "true", "on", "yes"}

        errors = {}
        update_data: dict[str, object] = {}

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

        scope_options = self._get_scope_options(db, org_id)

        if errors:
            rule = service.get_rule(org_id, rule_id)
            context = base_context(
                request, auth, f"Edit Rule: {rule.rule_code}", "limits"
            )
            context.update(
                {
                    "rule": rule,
                    "scope_types": [s.value for s in LimitScopeType],
                    "period_types": [p.value for p in LimitPeriodType],
                    "action_types": [a.value for a in LimitActionType],
                    "scope_options": scope_options,
                    "errors": errors,
                }
            )
            return templates.TemplateResponse(
                request, "expense/limits/rule_form.html", context
            )

        try:
            service.update_rule(org_id, rule_id, **update_data)
            db.commit()
            return RedirectResponse(
                url="/expense/limits/rules?success=Record+saved+successfully",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            rule = service.get_rule(org_id, rule_id)
            errors["_form"] = str(e)
            context = base_context(
                request, auth, f"Edit Rule: {rule.rule_code}", "limits"
            )
            context.update(
                {
                    "rule": rule,
                    "scope_types": [s.value for s in LimitScopeType],
                    "period_types": [p.value for p in LimitPeriodType],
                    "action_types": [a.value for a in LimitActionType],
                    "scope_options": scope_options,
                    "errors": errors,
                }
            )
            return templates.TemplateResponse(
                request, "expense/limits/rule_form.html", context
            )

    def delete_limit_rule_response(
        self,
        rule_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Delete expense limit rule."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)

        try:
            service.delete_rule(org_id, rule_id)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url="/expense/limits/rules?success=Record+deleted+successfully",
            status_code=303,
        )

    def approver_limits_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        scope_type: str | None = None,
        is_active: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
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
        scope_labels = self._build_approver_scope_labels(db, org_id, result.items)
        context.update(
            {
                "approver_limits": result.items,
                "approver_scope_labels": scope_labels,
                "total": result.total,
                "page": page,
                "total_pages": total_pages,
                "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
                "filters": {
                    "scope_type": scope_type,
                    "is_active": is_active,
                },
            }
        )
        return templates.TemplateResponse(
            request, "expense/limits/approvers.html", context
        )

    def new_approver_limit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """New expense approver limit form."""
        org_id = coerce_uuid(auth.organization_id)
        scope_options = self._get_scope_options(db, org_id)

        context = base_context(request, auth, "New Approver Limit", "limits")
        context.update(
            {
                "approver_limit": None,
                "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
                "scope_options": scope_options,
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "expense/limits/approver_form.html", context
        )

    def approver_limit_detail_response(
        self,
        request: Request,
        approver_limit_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """View expense approver limit details."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)
        limit = service.get_approver_limit(org_id, approver_limit_id)

        scope_labels = self._build_approver_scope_labels(db, org_id, [limit])
        scope_label = scope_labels.get(str(limit.approver_limit_id))

        scoped_employees, is_truncated = self._list_scope_approvers(
            db=db,
            org_id=org_id,
            limit=limit,
            max_items=100,
        )

        # Budget adjustments
        adjustments = service.list_budget_adjustments(org_id, approver_limit_id)

        # Current month usage stats
        current_month_usage = self._build_current_month_usage(
            db, org_id, limit, service
        )

        context = base_context(request, auth, "Approver Limit Details", "limits")
        context.update(
            {
                "approver_limit": limit,
                "scope_label": scope_label,
                "scoped_approvers": scoped_employees,
                "scoped_approvers_truncated": is_truncated,
                "adjustments": adjustments,
                "current_month_usage": current_month_usage,
            }
        )
        return templates.TemplateResponse(
            request, "expense/limits/approver_detail.html", context
        )

    def edit_approver_limit_form_response(
        self,
        request: Request,
        approver_limit_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Edit expense approver limit form."""
        from app.models.people.hr.designation import Designation
        from app.models.people.hr.employee import Employee
        from app.models.people.hr.employee_grade import EmployeeGrade
        from app.models.rbac import Role

        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)
        limit = service.get_approver_limit(org_id, approver_limit_id)
        scope_options = self._get_scope_options(db, org_id)

        scope_label = None
        if limit.scope_type == "EMPLOYEE" and limit.scope_id:
            employee = db.get(Employee, limit.scope_id)
            if employee and employee.person:
                scope_label = employee.person.name or ""
                if employee.employee_code:
                    scope_label = (
                        f"{scope_label} ({employee.employee_code})"
                        if scope_label
                        else employee.employee_code
                    )
        elif limit.scope_type == "GRADE" and limit.scope_id:
            grade = db.get(EmployeeGrade, limit.scope_id)
            scope_label = grade.grade_name if grade else None
        elif limit.scope_type == "DESIGNATION" and limit.scope_id:
            designation = db.get(Designation, limit.scope_id)
            scope_label = designation.designation_name if designation else None
        elif limit.scope_type == "ROLE" and limit.scope_id:
            role = db.get(Role, limit.scope_id)
            scope_label = role.name if role else None

        context = base_context(request, auth, "Edit Approver Limit", "limits")
        context.update(
            {
                "approver_limit": limit,
                "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
                "scope_options": scope_options,
                "scope_label": scope_label,
                "errors": {},
            }
        )
        return templates.TemplateResponse(
            request, "expense/limits/approver_form.html", context
        )

    async def create_approver_limit_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Create new expense approver limit."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        scope_type = _safe_form_text(form.get("scope_type"))
        scope_id = _safe_form_text(form.get("scope_id"))
        max_approval_amount = _safe_form_text(form.get("max_approval_amount"))
        monthly_approval_budget = _safe_form_text(form.get("monthly_approval_budget"))
        can_approve_own = _safe_form_text(form.get("can_approve_own_expenses")) in {
            "1",
            "true",
            "on",
            "yes",
        }
        is_active = _safe_form_text(form.get("is_active")) in {"1", "true", "on", "yes"}

        errors: dict[str, str] = {}
        if not scope_type:
            errors["scope_type"] = "Required"
        if not max_approval_amount:
            errors["max_approval_amount"] = "Required"

        max_amount_value: Decimal | None = None
        if max_approval_amount:
            try:
                max_amount_value = Decimal(max_approval_amount)
            except (ValueError, ArithmeticError):
                errors["max_approval_amount"] = "Invalid amount"
        if max_amount_value is None:
            errors["max_approval_amount"] = (
                errors.get("max_approval_amount") or "Required"
            )

        monthly_budget_value: Decimal | None = None
        if monthly_approval_budget:
            try:
                monthly_budget_value = Decimal(monthly_approval_budget)
            except (ValueError, ArithmeticError):
                errors["monthly_approval_budget"] = "Invalid amount"

        scope_options = self._get_scope_options(db, org_id)

        form_data = {
            "scope_type": scope_type,
            "scope_id": scope_id,
            "max_approval_amount": max_approval_amount,
            "monthly_approval_budget": monthly_approval_budget,
            "can_approve_own_expenses": can_approve_own,
            "is_active": is_active,
        }

        if errors:
            context = base_context(request, auth, "New Approver Limit", "limits")
            context.update(
                {
                    "approver_limit": form_data,
                    "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
                    "scope_options": scope_options,
                    "errors": errors,
                }
            )
            return templates.TemplateResponse(
                request, "expense/limits/approver_form.html", context
            )

        try:
            if max_amount_value is None:
                raise ValueError("Missing approval amount")
            service.create_approver_limit(
                org_id,
                scope_type=scope_type,
                scope_id=coerce_uuid(scope_id) if scope_id else None,
                max_approval_amount=max_amount_value,
                monthly_approval_budget=monthly_budget_value,
                can_approve_own_expenses=can_approve_own,
                is_active=is_active,
            )
            db.commit()
            return RedirectResponse(
                url="/expense/limits/approvers?success=Record+saved+successfully",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            errors["_form"] = str(e)
            context = base_context(request, auth, "New Approver Limit", "limits")
            context.update(
                {
                    "approver_limit": form_data,
                    "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
                    "scope_options": scope_options,
                    "errors": errors,
                }
            )
            return templates.TemplateResponse(
                request, "expense/limits/approver_form.html", context
            )

    async def update_approver_limit_response(
        self,
        request: Request,
        approver_limit_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        """Update expense approver limit."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        scope_type = _safe_form_text(form.get("scope_type"))
        scope_id = _safe_form_text(form.get("scope_id"))
        max_approval_amount = _safe_form_text(form.get("max_approval_amount"))
        monthly_approval_budget = _safe_form_text(form.get("monthly_approval_budget"))
        can_approve_own = _safe_form_text(form.get("can_approve_own_expenses")) in {
            "1",
            "true",
            "on",
            "yes",
        }
        is_active = _safe_form_text(form.get("is_active")) in {"1", "true", "on", "yes"}

        errors: dict[str, str] = {}
        if not scope_type:
            errors["scope_type"] = "Required"
        if not max_approval_amount:
            errors["max_approval_amount"] = "Required"

        max_amount_value: Decimal | None = None
        if max_approval_amount:
            try:
                max_amount_value = Decimal(max_approval_amount)
            except (ValueError, ArithmeticError):
                errors["max_approval_amount"] = "Invalid amount"
        if max_amount_value is None:
            errors["max_approval_amount"] = (
                errors.get("max_approval_amount") or "Required"
            )

        monthly_budget_value: Decimal | None = None
        if monthly_approval_budget:
            try:
                monthly_budget_value = Decimal(monthly_approval_budget)
            except (ValueError, ArithmeticError):
                errors["monthly_approval_budget"] = "Invalid amount"

        scope_options = self._get_scope_options(db, org_id)

        form_data = {
            "approver_limit_id": approver_limit_id,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "max_approval_amount": max_approval_amount,
            "monthly_approval_budget": monthly_approval_budget,
            "can_approve_own_expenses": can_approve_own,
            "is_active": is_active,
        }

        if errors:
            context = base_context(request, auth, "Edit Approver Limit", "limits")
            context.update(
                {
                    "approver_limit": form_data,
                    "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
                    "scope_options": scope_options,
                    "scope_label": None,
                    "errors": errors,
                }
            )
            return templates.TemplateResponse(
                request, "expense/limits/approver_form.html", context
            )

        try:
            if max_amount_value is None:
                raise ValueError("Missing approval amount")
            service.update_approver_limit(
                org_id,
                approver_limit_id,
                scope_type=scope_type,
                scope_id=coerce_uuid(scope_id) if scope_id else None,
                max_approval_amount=max_amount_value,
                monthly_approval_budget=monthly_budget_value,
                can_approve_own_expenses=can_approve_own,
                is_active=is_active,
            )
            db.commit()
            return RedirectResponse(
                url="/expense/limits/approvers?success=Record+saved+successfully",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            errors["_form"] = str(e)
            context = base_context(request, auth, "Edit Approver Limit", "limits")
            context.update(
                {
                    "approver_limit": form_data,
                    "scope_types": ["EMPLOYEE", "GRADE", "DESIGNATION", "ROLE"],
                    "scope_options": scope_options,
                    "scope_label": None,
                    "errors": errors,
                }
            )
            return templates.TemplateResponse(
                request, "expense/limits/approver_form.html", context
            )

    async def adjust_budget_response(
        self,
        request: Request,
        approver_limit_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Create a budget adjustment for a specific month."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        month_str = _safe_form_text(form.get("adjustment_month"))
        amount_str = _safe_form_text(form.get("additional_amount"))
        reason = _safe_form_text(form.get("reason"))

        detail_url = f"/expense/limits/approvers/{approver_limit_id}"

        # Validate
        errors: list[str] = []
        month_date: date | None = None
        if month_str:
            try:
                # <input type="month"> sends "YYYY-MM"; normalise to 1st
                if len(month_str) == 7 and month_str[4] == "-":
                    month_date = date.fromisoformat(month_str + "-01")
                else:
                    month_date = date.fromisoformat(month_str)
            except ValueError:
                errors.append("Invalid month")
        else:
            errors.append("Month is required")

        amount_value: Decimal | None = None
        if amount_str:
            try:
                amount_value = Decimal(amount_str)
                if amount_value == Decimal("0"):
                    errors.append("Amount must not be zero")
            except (ValueError, ArithmeticError):
                errors.append("Invalid amount")
        else:
            errors.append("Amount is required")

        if not reason:
            errors.append("Reason is required")

        if errors:
            error_msg = "; ".join(errors)
            return RedirectResponse(
                url=f"{detail_url}?error={error_msg}",
                status_code=303,
            )

        try:
            if month_date is None or amount_value is None:
                raise ValueError("Missing required form values")
            service.create_budget_adjustment(
                org_id,
                approver_limit_id,
                month=month_date,
                additional_amount=amount_value,
                reason=reason,
                adjusted_by_id=coerce_uuid(auth.person_id),
            )
            db.commit()
            return RedirectResponse(
                url=f"{detail_url}?success=Budget+adjustment+saved",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("Failed to create budget adjustment")
            return RedirectResponse(
                url=f"{detail_url}?error={e}",
                status_code=303,
            )

    def delete_budget_adjustment_response(
        self,
        approver_limit_id: UUID,
        adjustment_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Delete a budget adjustment."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)

        detail_url = f"/expense/limits/approvers/{approver_limit_id}"

        try:
            service.delete_budget_adjustment(org_id, adjustment_id)
            db.commit()
            return RedirectResponse(
                url=f"{detail_url}?success=Adjustment+deleted",
                status_code=303,
            )
        except Exception as e:
            db.rollback()
            logger.exception("Failed to delete budget adjustment")
            return RedirectResponse(
                url=f"{detail_url}?error={e}",
                status_code=303,
            )

    def delete_approver_limit_response(
        self,
        approver_limit_id: UUID,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        """Delete expense approver limit."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)

        try:
            service.delete_approver_limit(org_id, approver_limit_id)
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(
            url="/expense/limits/approvers?success=Record+deleted+successfully",
            status_code=303,
        )

    def usage_dashboard_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        employee_id: str | None = None,
    ) -> HTMLResponse:
        """Employee expense usage dashboard."""
        org_id = coerce_uuid(auth.organization_id)
        service = ExpenseLimitService(db)

        # Get employee list for selection
        from app.models.people.hr.employee import Employee, EmployeeStatus
        from app.models.person import Person

        employees = db.scalars(
            select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .where(
                Employee.organization_id == org_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .order_by(Person.first_name, Person.last_name)
            .limit(200)
        ).all()

        usage_summary = None
        if employee_id:
            try:
                emp_uuid = coerce_uuid(employee_id)
                usage_summary = service.get_employee_usage_summary(org_id, emp_uuid)
            except Exception:
                logger.exception("Ignored exception")

        context = base_context(request, auth, "Expense Usage", "limits")
        context.update(
            {
                "employees": employees,
                "selected_employee_id": employee_id,
                "usage_summary": usage_summary,
            }
        )
        return templates.TemplateResponse(request, "expense/limits/usage.html", context)

    def evaluations_list_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        result: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        page: int = 1,
    ) -> HTMLResponse:
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
                logger.exception("Ignored exception")

        to_date_parsed = None
        if to_date:
            try:
                to_date_parsed = date.fromisoformat(to_date)
            except Exception:
                logger.exception("Ignored exception")

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
        active_filters = build_active_filters(
            params={"result": result, "from_date": from_date, "to_date": to_date},
            labels={"from_date": "From", "to_date": "To"},
        )
        context.update(
            {
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
                "active_filters": active_filters,
            }
        )
        return templates.TemplateResponse(
            request, "expense/limits/evaluations.html", context
        )

    @staticmethod
    def _build_current_month_usage(
        db: Session,
        org_id: UUID,
        limit: ExpenseApproverLimit,
        service: ExpenseLimitService,
    ) -> dict[str, object] | None:
        """Build current month usage stats for the approver limit detail page.

        Returns None when no monthly budget is configured (unlimited).
        """

        base_budget = limit.monthly_approval_budget
        if base_budget is None:
            return None

        today = date.today()

        adjustment = service.get_budget_adjustment_for_month(
            limit.approver_limit_id, today
        )
        effective_budget = base_budget + adjustment

        month_label = today.strftime("%B %Y")

        return {
            "month_label": month_label,
            "base_budget": base_budget,
            "adjustment": adjustment,
            "effective_budget": effective_budget,
        }

    @staticmethod
    def _list_scope_approvers(
        db: Session,
        org_id: UUID,
        limit: ExpenseApproverLimit,
        max_items: int = 100,
    ) -> tuple[list[dict[str, str]], bool]:
        """List approvers that match the configured scope target."""
        from app.models.people.hr.employee import Employee, EmployeeStatus
        from app.models.person import Person
        from app.models.rbac import PersonRole

        stmt = (
            select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .options(
                joinedload(Employee.person),
                joinedload(Employee.grade),
                joinedload(Employee.designation),
            )
            .where(Employee.organization_id == org_id)
        )

        scope_type = limit.scope_type
        scope_id = limit.scope_id

        if scope_type == "EMPLOYEE":
            if scope_id:
                stmt = stmt.where(Employee.employee_id == scope_id)
            else:
                stmt = stmt.where(Employee.status == EmployeeStatus.ACTIVE)
        elif scope_type == "GRADE":
            stmt = stmt.where(Employee.status == EmployeeStatus.ACTIVE)
            if scope_id:
                stmt = stmt.where(Employee.grade_id == scope_id)
        elif scope_type == "DESIGNATION":
            stmt = stmt.where(Employee.status == EmployeeStatus.ACTIVE)
            if scope_id:
                stmt = stmt.where(Employee.designation_id == scope_id)
        elif scope_type == "ROLE":
            stmt = stmt.join(PersonRole, PersonRole.person_id == Employee.person_id)
            stmt = stmt.where(Employee.status == EmployeeStatus.ACTIVE)
            if scope_id:
                stmt = stmt.where(PersonRole.role_id == scope_id)
        else:
            return [], False

        stmt = stmt.order_by(Person.first_name, Person.last_name).limit(max_items + 1)
        employees = list(db.scalars(stmt).unique().all())
        is_truncated = len(employees) > max_items
        if is_truncated:
            employees = employees[:max_items]

        rows: list[dict[str, str]] = []
        for employee in employees:
            rows.append(
                {
                    "employee_id": str(employee.employee_id),
                    "name": employee.person.name if employee.person else "Unknown",
                    "employee_code": employee.employee_code or "-",
                    "designation": (
                        employee.designation.designation_name
                        if employee.designation
                        else "-"
                    ),
                    "grade": employee.grade.grade_name if employee.grade else "-",
                    "status": employee.status.value,
                }
            )

        return rows, is_truncated

    @staticmethod
    def _build_approver_scope_labels(
        db: Session, org_id: UUID, limits: Sequence[ExpenseApproverLimit]
    ) -> dict[str, str]:
        """Resolve human-readable labels for approver limit scope targets."""
        from app.models.people.hr.designation import Designation
        from app.models.people.hr.employee import Employee
        from app.models.people.hr.employee_grade import EmployeeGrade
        from app.models.person import Person
        from app.models.rbac import Role

        labels: dict[str, str] = {}
        employee_ids: set[UUID] = set()
        grade_ids: set[UUID] = set()
        designation_ids: set[UUID] = set()
        role_ids: set[UUID] = set()

        for limit in limits:
            scope_id = getattr(limit, "scope_id", None)
            if not scope_id:
                continue

            scope_type = getattr(limit, "scope_type", "")
            if scope_type == "EMPLOYEE":
                employee_ids.add(scope_id)
            elif scope_type == "GRADE":
                grade_ids.add(scope_id)
            elif scope_type == "DESIGNATION":
                designation_ids.add(scope_id)
            elif scope_type == "ROLE":
                role_ids.add(scope_id)

        employee_map: dict[UUID, str] = {}
        if employee_ids:
            employees = list(
                db.scalars(
                    select(Employee)
                    .join(Person, Person.id == Employee.person_id)
                    .where(
                        Employee.organization_id == org_id,
                        Employee.employee_id.in_(employee_ids),
                    )
                ).all()
            )
            for employee in employees:
                name = employee.person.name if employee.person else ""
                employee_label = name
                if employee.employee_code:
                    employee_label = (
                        f"{name} ({employee.employee_code})"
                        if name
                        else employee.employee_code
                    )
                employee_map[employee.employee_id] = employee_label or str(
                    employee.employee_id
                )

        grade_map: dict[UUID, str] = {}
        if grade_ids:
            grades = list(
                db.scalars(
                    select(EmployeeGrade).where(
                        EmployeeGrade.organization_id == org_id,
                        EmployeeGrade.grade_id.in_(grade_ids),
                    )
                ).all()
            )
            for grade in grades:
                grade_map[grade.grade_id] = grade.grade_name

        designation_map: dict[UUID, str] = {}
        if designation_ids:
            designations = list(
                db.scalars(
                    select(Designation).where(
                        Designation.organization_id == org_id,
                        Designation.designation_id.in_(designation_ids),
                    )
                ).all()
            )
            for designation in designations:
                designation_map[designation.designation_id] = (
                    designation.designation_name
                )

        role_map: dict[UUID, str] = {}
        if role_ids:
            roles = list(db.scalars(select(Role).where(Role.id.in_(role_ids))).all())
            for role in roles:
                role_map[role.id] = role.name

        for limit in limits:
            scope_id = limit.scope_id
            if not scope_id:
                continue

            scope_type = limit.scope_type
            label: str | None = None
            if scope_type == "EMPLOYEE":
                label = employee_map.get(scope_id)
            elif scope_type == "GRADE":
                label = grade_map.get(scope_id)
            elif scope_type == "DESIGNATION":
                label = designation_map.get(scope_id)
            elif scope_type == "ROLE":
                label = role_map.get(scope_id)

            labels[str(limit.approver_limit_id)] = label or str(scope_id)

        return labels

    @staticmethod
    def _get_scope_options(db: Session, org_id: UUID) -> dict:
        """Get scope options for dropdowns (grades, departments, designations, employees)."""
        from app.models.people.hr.department import Department
        from app.models.people.hr.designation import Designation
        from app.models.people.hr.employee import Employee, EmployeeStatus
        from app.models.people.hr.employee_grade import EmployeeGrade
        from app.models.person import Person
        from app.models.rbac import Role

        grades = db.scalars(
            select(EmployeeGrade)
            .where(
                EmployeeGrade.organization_id == org_id, EmployeeGrade.is_active == True
            )
            .order_by(EmployeeGrade.rank.desc())
        ).all()

        departments = db.scalars(
            select(Department)
            .where(Department.organization_id == org_id, Department.is_active == True)
            .order_by(Department.department_name)
        ).all()

        designations = db.scalars(
            select(Designation)
            .where(Designation.organization_id == org_id, Designation.is_active == True)
            .order_by(Designation.designation_name)
        ).all()

        employees = db.scalars(
            select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .where(
                Employee.organization_id == org_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .order_by(Person.first_name, Person.last_name)
            .limit(100)
        ).all()

        roles = db.scalars(
            select(Role).where(Role.is_active == True).order_by(Role.name)
        ).all()

        return {
            "grades": grades,
            "departments": departments,
            "designations": designations,
            "employees": employees,
            "roles": roles,
        }

    @staticmethod
    def employee_typeahead(
        db: Session,
        organization_id: str,
        query: str,
        limit: int = 8,
    ) -> dict:
        """Search active employees for approver limit typeahead fields."""
        from sqlalchemy import select as sa_select
        from sqlalchemy.orm import joinedload as jl

        from app.models.people.hr.employee import Employee, EmployeeStatus
        from app.models.person import Person
        from app.services.common import coerce_uuid

        org_id = coerce_uuid(organization_id)
        search_term = f"%{query.strip()}%"
        stmt = (
            sa_select(Employee)
            .join(Person, Person.id == Employee.person_id)
            .options(jl(Employee.person))
            .where(
                Employee.organization_id == org_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .where(
                (Person.first_name.ilike(search_term))
                | (Person.last_name.ilike(search_term))
                | (Person.email.ilike(search_term))
                | (Employee.employee_code.ilike(search_term))
            )
            .order_by(Person.first_name.asc(), Person.last_name.asc())
            .limit(limit)
        )
        employees = list(db.scalars(stmt).unique().all())
        items = []
        for employee in employees:
            name = employee.person.name if employee.person else ""
            label = name
            if employee.employee_code:
                label = (
                    f"{name} ({employee.employee_code})"
                    if name
                    else employee.employee_code
                )
            items.append(
                {
                    "ref": str(employee.employee_id),
                    "label": label,
                    "name": name,
                    "employee_code": employee.employee_code or "",
                }
            )
        return {"items": items}


expense_limit_web_service = ExpenseLimitWebService()
