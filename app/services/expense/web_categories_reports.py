"""Expense category and report web responses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from decimal import Decimal

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, func, select

from app.models.expense import ExpenseClaim, ExpenseClaimAction, ExpenseClaimActionStatus, ExpenseClaimActionType
from app.models.people.hr.employee import Employee
from app.services.common import PaginationParams, coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.expense.expense_service import ExpenseService, ExpenseServiceError
from app.services.expense.limit_service import ExpenseLimitService
from app.templates import templates
from app.web.deps import base_context


class ExpenseCategoriesReportsWebMixin:
    @staticmethod
    def categories_list_response(request: Request, auth, db, search: str | None, is_active: str | None, page: int) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        is_active_value: bool | None = None
        if isinstance(is_active, str):
            lowered = is_active.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                is_active_value = True
            elif lowered in {"false", "0", "no", "off"}:
                is_active_value = False

        pagination = PaginationParams.from_page(page, 20)
        result = svc.list_categories(org_id, search=search, is_active=is_active_value, pagination=pagination)

        context = base_context(request, auth, "Expense Categories", "categories")
        context.update(
            {
                "categories": result.items,
                "search": search or "",
                "is_active": is_active_value,
                "page": page,
                "total_pages": result.total_pages,
                "total": result.total,
                "limit": pagination.limit,
                "has_prev": result.has_prev,
                "has_next": result.has_next,
            }
        )
        return templates.TemplateResponse(request, "expense/categories.html", context)

    @staticmethod
    def new_category_form_response(request: Request, auth, db) -> HTMLResponse:
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        org_id = coerce_uuid(auth.organization_id)
        expense_accounts = db.scalars(
            select(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
        ).all()

        context = base_context(request, auth, "New Expense Category", "categories")
        context.update({"category": None, "expense_accounts": expense_accounts, "errors": {}})
        return templates.TemplateResponse(request, "expense/category_form.html", context)

    @classmethod
    async def create_category_response(cls, request: Request, auth, db):
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        form = getattr(request.state, "csrf_form", None) or await request.form()
        category_code = cls._form_str(form, "category_code")
        category_name = cls._form_str(form, "category_name")
        description = cls._form_str(form, "description")
        expense_account_id = cls._form_str(form, "expense_account_id")
        max_amount = cls._form_str(form, "max_amount_per_claim")
        requires_receipt = cls._form_str(form, "requires_receipt") in {"1", "true", "on", "yes"}
        is_active = cls._form_str(form, "is_active") in {"1", "true", "on", "yes"}

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
            expense_accounts = db.scalars(
                select(Account)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .where(
                    Account.organization_id == org_id,
                    AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                    Account.is_active.is_(True),
                    AccountCategory.is_active.is_(True),
                )
                .order_by(Account.account_code)
            ).all()
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
            svc.create_category(
                org_id,
                category_code=category_code,
                category_name=category_name,
                description=description or None,
                expense_account_id=coerce_uuid(expense_account_id) if expense_account_id else None,
                max_amount_per_claim=max_amount_value,
                requires_receipt=requires_receipt if requires_receipt else False,
                is_active=is_active if is_active else False,
            )
            db.flush()
        except ExpenseServiceError as exc:
            db.rollback()
            expense_accounts = db.scalars(
                select(Account)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .where(
                    Account.organization_id == org_id,
                    AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                    Account.is_active.is_(True),
                    AccountCategory.is_active.is_(True),
                )
                .order_by(Account.account_code)
            ).all()
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

        return RedirectResponse(url="/expense/categories?success=Record+saved+successfully", status_code=303)

    @staticmethod
    def edit_category_form_response(request: Request, auth, db, category_id: str) -> HTMLResponse:
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        category = svc.get_category(org_id, coerce_uuid(category_id))
        expense_accounts = db.scalars(
            select(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
            .order_by(Account.account_code)
        ).all()

        context = base_context(request, auth, "Edit Expense Category", "categories")
        context.update({"category": category, "expense_accounts": expense_accounts, "errors": {}})
        return templates.TemplateResponse(request, "expense/category_form.html", context)

    @classmethod
    async def update_category_response(cls, request: Request, auth, db, category_id: str):
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        form = getattr(request.state, "csrf_form", None) or await request.form()
        category_code = cls._form_str(form, "category_code")
        category_name = cls._form_str(form, "category_name")
        description = cls._form_str(form, "description")
        expense_account_id = cls._form_str(form, "expense_account_id")
        max_amount = cls._form_str(form, "max_amount_per_claim")
        requires_receipt = cls._form_str(form, "requires_receipt") in {"1", "true", "on", "yes"}
        is_active = cls._form_str(form, "is_active") in {"1", "true", "on", "yes"}

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
            expense_accounts = db.scalars(
                select(Account)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .where(
                    Account.organization_id == org_id,
                    AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                    Account.is_active.is_(True),
                    AccountCategory.is_active.is_(True),
                )
                .order_by(Account.account_code)
            ).all()
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
        db.flush()
        return RedirectResponse(url="/expense/categories?success=Record+saved+successfully", status_code=303)

    @staticmethod
    def delete_category_response(category_id: str, auth, db) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        ExpenseService(db).update_category(org_id, coerce_uuid(category_id), is_active=False)
        db.flush()
        return RedirectResponse(url="/expense/categories?success=Record+deleted+successfully", status_code=303)

    @staticmethod
    def expense_summary_report_response(request: Request, auth, db, start_date: str | None, end_date: str | None) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        report_data = ExpenseService(db).get_expense_summary_report(
            org_id,
            start_date=date_type.fromisoformat(start_date) if start_date else None,
            end_date=date_type.fromisoformat(end_date) if end_date else None,
        )
        context = base_context(request, auth, "Expense Summary Report", "expense")
        context.update(
            {
                "report": report_data,
                "start_date": start_date or report_data["start_date"].isoformat(),
                "end_date": end_date or report_data["end_date"].isoformat(),
            }
        )
        return templates.TemplateResponse(request, "expense/reports/summary.html", context)

    @staticmethod
    def expense_by_category_report_response(request: Request, auth, db, start_date: str | None, end_date: str | None) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        report_data = ExpenseService(db).get_expense_by_category_report(
            org_id,
            start_date=date_type.fromisoformat(start_date) if start_date else None,
            end_date=date_type.fromisoformat(end_date) if end_date else None,
        )
        context = base_context(request, auth, "Expense by Category Report", "expense")
        context.update(
            {
                "report": report_data,
                "start_date": start_date or report_data["start_date"].isoformat(),
                "end_date": end_date or report_data["end_date"].isoformat(),
            }
        )
        return templates.TemplateResponse(request, "expense/reports/by_category.html", context)

    @staticmethod
    def expense_by_employee_report_response(request: Request, auth, db, start_date: str | None, end_date: str | None, department_id: str | None) -> HTMLResponse:
        from app.services.people.hr import DepartmentFilters, OrganizationService

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        org_svc = OrganizationService(db, org_id)
        parsed_dept = coerce_uuid(department_id) if department_id else None
        report_data = svc.get_expense_by_employee_report(
            org_id,
            start_date=date_type.fromisoformat(start_date) if start_date else None,
            end_date=date_type.fromisoformat(end_date) if end_date else None,
            department_id=parsed_dept,
        )
        departments = org_svc.list_departments(DepartmentFilters(is_active=True), PaginationParams(limit=200)).items
        context = base_context(request, auth, "Expense by Employee Report", "expense")
        context.update(
            {
                "report": report_data,
                "departments": departments,
                "start_date": start_date or report_data["start_date"].isoformat(),
                "end_date": end_date or report_data["end_date"].isoformat(),
                "department_id": department_id,
            }
        )
        return templates.TemplateResponse(request, "expense/reports/by_employee.html", context)

    @staticmethod
    def expense_trends_report_response(request: Request, auth, db, months: int) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        report_data = ExpenseService(db).get_expense_trends_report(org_id, months=months)
        context = base_context(request, auth, "Expense Trends Report", "expense")
        context.update({"report": report_data, "months": months})
        return templates.TemplateResponse(request, "expense/reports/trends.html", context)

    @staticmethod
    def my_approvals_report_response(request: Request, auth, db, start_date: str | None, end_date: str | None) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        approver_id = coerce_uuid(auth.employee_id) if auth.employee_id else None
        svc = ExpenseService(db)
        limit_svc = ExpenseLimitService(db)

        parsed_start = date_type.fromisoformat(start_date) if start_date else None
        parsed_end = date_type.fromisoformat(end_date) if end_date else None

        report_data = (
            svc.get_my_approvals_report(org_id, approver_id=approver_id, start_date=parsed_start, end_date=parsed_end)
            if approver_id
            else {
                "start_date": parsed_start or date_type.today().replace(day=1),
                "end_date": parsed_end or date_type.today(),
                "decisions": [],
                "approved_count": 0,
                "rejected_count": 0,
                "approved_total": Decimal("0"),
                "rejected_total": Decimal("0"),
            }
        )

        weekly_balance = None
        if approver_id:
            approver = db.get(Employee, approver_id)
            if approver is not None:
                budget_info = limit_svc._get_approver_weekly_budget(org_id, approver)
                if budget_info is not None:
                    budget_amount, limit_id = budget_info
                    now = datetime.now(UTC)
                    week_start = limit_svc._start_of_week_utc(now)
                    week_end = week_start + timedelta(days=6)
                    latest_reset = limit_svc.get_latest_weekly_reset(
                        org_id,
                        approver_id=approver_id,
                        approver_limit_id=limit_id,
                        from_datetime=week_start,
                    )
                    usage_start = latest_reset.reset_at if latest_reset is not None else week_start
                    used_amount = db.scalar(
                        select(func.coalesce(func.sum(ExpenseClaim.total_approved_amount), Decimal("0")))
                        .select_from(ExpenseClaim)
                        .join(
                            ExpenseClaimAction,
                            and_(
                                ExpenseClaimAction.claim_id == ExpenseClaim.claim_id,
                                ExpenseClaimAction.action_type == ExpenseClaimActionType.APPROVE,
                                ExpenseClaimAction.status == ExpenseClaimActionStatus.COMPLETED,
                            ),
                        )
                        .where(
                            ExpenseClaim.organization_id == org_id,
                            ExpenseClaim.status.in_([ExpenseClaimStatus.APPROVED, ExpenseClaimStatus.PAID]),
                            ExpenseClaimAction.created_at >= usage_start,
                            ExpenseClaimAction.created_at <= now,
                            ExpenseClaim.approver_id == approver_id,
                        )
                    ) or Decimal("0")
                    weekly_balance = {
                        "week_label": f"{week_start.date().isoformat()} - {week_end.date().isoformat()}",
                        "budget": budget_amount,
                        "used": used_amount,
                        "remaining": budget_amount - used_amount,
                        "last_reset_at": latest_reset.reset_at if latest_reset else None,
                    }

        context = base_context(request, auth, "My Approvals Report", "reports-my-approvals")
        context.update(
            {
                "report": report_data,
                "weekly_balance": weekly_balance,
                "start_date": start_date or report_data["start_date"].isoformat(),
                "end_date": end_date or report_data["end_date"].isoformat(),
            }
        )
        return templates.TemplateResponse(request, "expense/reports/my_approvals.html", context)
