"""Expense category and report web responses."""

from __future__ import annotations

import csv
import io
from datetime import date as date_type
from decimal import Decimal
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select

from app.services.common import PaginationParams, coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.expense.expense_service import ExpenseService, ExpenseServiceError
from app.services.expense.limit_service import ExpenseLimitService
from app.services.expense.web_common import ExpenseWebCommonMixin
from app.templates import templates
from app.web.deps import base_context


class ExpenseCategoriesReportsWebMixin(ExpenseWebCommonMixin):
    @staticmethod
    def _report_csv_text(value: object) -> str:
        """Return a CSV-safe text cell, including spreadsheet formula escaping."""
        text = "" if value is None else str(value)
        if text.startswith(("=", "+", "-", "@", "\t", "\r")):
            return f"'{text}"
        return text

    @staticmethod
    def _report_csv_response(filename: str, rows: list[list[object]]) -> Response:
        output = io.StringIO(newline="")
        csv.writer(output).writerows(rows)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @staticmethod
    def _safe_report_uuid(value: str | None) -> UUID | None:
        if not value:
            return None
        try:
            parsed = coerce_uuid(value, raise_http=False)
            return parsed if isinstance(parsed, UUID) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _load_allowed_expense_accounts(db, org_id):
        """Load GL expense accounts filtered by admin-configured allowed list."""
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        allowed_ids = ExpenseWebCommonMixin.get_allowed_expense_account_ids(db, org_id)
        stmt = (
            select(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .where(
                Account.organization_id == org_id,
                AccountCategory.ifrs_category == IFRSCategory.EXPENSES,
                Account.is_active.is_(True),
                AccountCategory.is_active.is_(True),
            )
        )
        if allowed_ids is not None:
            stmt = stmt.where(Account.account_id.in_(allowed_ids))
        return list(db.scalars(stmt.order_by(Account.account_code)).all())

    @staticmethod
    def _safe_iso_date(value: str | None) -> date_type | None:
        if not value:
            return None
        try:
            return date_type.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def categories_list_response(
        request: Request, auth, db, search: str | None, is_active: str | None, page: int
    ) -> HTMLResponse:
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
        result = svc.list_categories(
            org_id, search=search, is_active=is_active_value, pagination=pagination
        )

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
                "active_filters": build_active_filters(
                    params={"search": search, "is_active": is_active},
                    labels={"search": "Search"},
                    options={
                        "is_active": {
                            "true": "Active",
                            "false": "Inactive",
                        }
                    },
                ),
            }
        )
        return templates.TemplateResponse(request, "expense/categories.html", context)

    @staticmethod
    def new_category_form_response(request: Request, auth, db) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        expense_accounts = (
            ExpenseCategoriesReportsWebMixin._load_allowed_expense_accounts(db, org_id)
        )

        context = base_context(request, auth, "New Expense Category", "categories")
        context.update(
            {"category": None, "expense_accounts": expense_accounts, "errors": {}}
        )
        return templates.TemplateResponse(
            request, "expense/category_form.html", context
        )

    @classmethod
    async def create_category_response(cls, request: Request, auth, db):

        form = getattr(request.state, "csrf_form", None) or await request.form()
        category_code = cls._form_str(form, "category_code")
        category_name = cls._form_str(form, "category_name")
        description = cls._form_str(form, "description")
        expense_account_id = cls._form_str(form, "expense_account_id")
        max_amount = cls._form_str(form, "max_amount_per_claim")
        requires_receipt = cls._form_str(form, "requires_receipt") in {
            "1",
            "true",
            "on",
            "yes",
        }
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
            expense_accounts = cls._load_allowed_expense_accounts(db, org_id)
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
            return templates.TemplateResponse(
                request, "expense/category_form.html", context
            )

        try:
            svc.create_category(
                org_id,
                category_code=category_code,
                category_name=category_name,
                description=description or None,
                expense_account_id=coerce_uuid(expense_account_id)
                if expense_account_id
                else None,
                max_amount_per_claim=max_amount_value,
                requires_receipt=requires_receipt if requires_receipt else False,
                is_active=is_active if is_active else False,
            )
            db.flush()
        except ExpenseServiceError as exc:
            db.rollback()
            expense_accounts = cls._load_allowed_expense_accounts(db, org_id)
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
            return templates.TemplateResponse(
                request, "expense/category_form.html", context
            )

        return RedirectResponse(
            url="/expense/categories?success=Record+saved+successfully", status_code=303
        )

    @staticmethod
    def edit_category_form_response(
        request: Request, auth, db, category_id: str
    ) -> HTMLResponse:

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        category = svc.get_category(org_id, coerce_uuid(category_id))
        expense_accounts = (
            ExpenseCategoriesReportsWebMixin._load_allowed_expense_accounts(db, org_id)
        )

        context = base_context(request, auth, "Edit Expense Category", "categories")
        context.update(
            {"category": category, "expense_accounts": expense_accounts, "errors": {}}
        )
        return templates.TemplateResponse(
            request, "expense/category_form.html", context
        )

    @classmethod
    async def update_category_response(
        cls, request: Request, auth, db, category_id: str
    ):

        form = getattr(request.state, "csrf_form", None) or await request.form()
        category_code = cls._form_str(form, "category_code")
        category_name = cls._form_str(form, "category_name")
        description = cls._form_str(form, "description")
        expense_account_id = cls._form_str(form, "expense_account_id")
        max_amount = cls._form_str(form, "max_amount_per_claim")
        requires_receipt = cls._form_str(form, "requires_receipt") in {
            "1",
            "true",
            "on",
            "yes",
        }
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
            expense_accounts = cls._load_allowed_expense_accounts(db, org_id)
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
            return templates.TemplateResponse(
                request, "expense/category_form.html", context
            )

        svc.update_category(
            org_id,
            coerce_uuid(category_id),
            category_code=category_code,
            category_name=category_name,
            description=description or None,
            expense_account_id=coerce_uuid(expense_account_id)
            if expense_account_id
            else None,
            max_amount_per_claim=max_amount_value,
            requires_receipt=requires_receipt,
            is_active=is_active,
        )
        db.flush()
        return RedirectResponse(
            url="/expense/categories?success=Record+saved+successfully", status_code=303
        )

    @staticmethod
    def delete_category_response(category_id: str, auth, db) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        ExpenseService(db).update_category(
            org_id, coerce_uuid(category_id), is_active=False
        )
        db.flush()
        return RedirectResponse(
            url="/expense/categories?success=Record+deleted+successfully",
            status_code=303,
        )

    @staticmethod
    def expense_summary_report_response(
        request: Request, auth, db, start_date: str | None, end_date: str | None
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        parsed_start = ExpenseCategoriesReportsWebMixin._safe_iso_date(start_date)
        parsed_end = ExpenseCategoriesReportsWebMixin._safe_iso_date(end_date)
        report_data = ExpenseService(db).get_expense_summary_report(
            org_id,
            start_date=parsed_start,
            end_date=parsed_end,
        )
        context = base_context(request, auth, "Expense Summary Report", "expense")
        context.update(
            {
                "report": report_data,
                "start_date": (
                    parsed_start.isoformat()
                    if parsed_start
                    else report_data["start_date"].isoformat()
                ),
                "end_date": (
                    parsed_end.isoformat()
                    if parsed_end
                    else report_data["end_date"].isoformat()
                ),
                "active_filters": build_active_filters(
                    params={
                        "start_date": parsed_start.isoformat()
                        if parsed_start
                        else None,
                        "end_date": parsed_end.isoformat() if parsed_end else None,
                    },
                    labels={"start_date": "From", "end_date": "To"},
                ),
            }
        )
        return templates.TemplateResponse(
            request, "expense/reports/summary.html", context
        )

    @staticmethod
    def expense_summary_export_response(
        auth,
        db,
        start_date: str | None,
        end_date: str | None,
    ) -> Response:
        """Export the filtered summary metrics and status breakdown as CSV."""
        org_id = coerce_uuid(auth.organization_id)
        parsed_start = ExpenseCategoriesReportsWebMixin._safe_iso_date(start_date)
        parsed_end = ExpenseCategoriesReportsWebMixin._safe_iso_date(end_date)
        report_data = ExpenseService(db).get_expense_summary_report(
            org_id,
            start_date=parsed_start,
            end_date=parsed_end,
        )
        effective_start = report_data["start_date"].isoformat()
        effective_end = report_data["end_date"].isoformat()

        rows: list[list[object]] = [
            ["Metric", "Value", "", ""],
            ["Start Date", effective_start, "", ""],
            ["End Date", effective_end, "", ""],
            ["Total Claims", report_data["total_claims"], "", ""],
            ["Total Claimed", f"{report_data['total_claimed']:.2f}", "", ""],
            ["Approved Claims", report_data["approved_count"], "", ""],
            ["Reimbursed Amount", f"{report_data['approved_amount']:.2f}", "", ""],
            ["Rejected Claims", report_data["rejected_count"], "", ""],
            [],
            ["Status", "Claims", "Amount", "Percent of Total"],
        ]
        for item in report_data["status_breakdown"]:
            percentage = (
                item["amount"] / report_data["total_claimed"] * 100
                if report_data["total_claimed"] > 0
                else 0
            )
            rows.append(
                [
                    ExpenseCategoriesReportsWebMixin._report_csv_text(item["status"]),
                    item["count"],
                    f"{item['amount']:.2f}",
                    f"{percentage:.1f}",
                ]
            )
        rows.append(
            [
                "TOTAL",
                report_data["total_claims"],
                f"{report_data['total_claimed']:.2f}",
                "100.0" if report_data["status_breakdown"] else "0.0",
            ]
        )
        filename = f"expense_summary_{effective_start}_to_{effective_end}.csv"
        return ExpenseCategoriesReportsWebMixin._report_csv_response(filename, rows)

    @staticmethod
    def expense_by_category_report_response(
        request: Request, auth, db, start_date: str | None, end_date: str | None
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        parsed_start = ExpenseCategoriesReportsWebMixin._safe_iso_date(start_date)
        parsed_end = ExpenseCategoriesReportsWebMixin._safe_iso_date(end_date)
        report_data = ExpenseService(db).get_expense_by_category_report(
            org_id,
            start_date=parsed_start,
            end_date=parsed_end,
        )
        context = base_context(request, auth, "Expense by Category Report", "expense")
        context.update(
            {
                "report": report_data,
                "start_date": (
                    parsed_start.isoformat()
                    if parsed_start
                    else report_data["start_date"].isoformat()
                ),
                "end_date": (
                    parsed_end.isoformat()
                    if parsed_end
                    else report_data["end_date"].isoformat()
                ),
                "active_filters": build_active_filters(
                    params={
                        "start_date": parsed_start.isoformat()
                        if parsed_start
                        else None,
                        "end_date": parsed_end.isoformat() if parsed_end else None,
                    },
                    labels={"start_date": "From", "end_date": "To"},
                ),
            }
        )
        return templates.TemplateResponse(
            request, "expense/reports/by_category.html", context
        )

    @staticmethod
    def expense_by_category_export_response(
        auth,
        db,
        start_date: str | None,
        end_date: str | None,
    ) -> Response:
        """Export the same filtered category rows and totals shown in the report."""
        org_id = coerce_uuid(auth.organization_id)
        parsed_start = ExpenseCategoriesReportsWebMixin._safe_iso_date(start_date)
        parsed_end = ExpenseCategoriesReportsWebMixin._safe_iso_date(end_date)
        report_data = ExpenseService(db).get_expense_by_category_report(
            org_id,
            start_date=parsed_start,
            end_date=parsed_end,
        )

        rows: list[list[object]] = [
            [
                "Category Code",
                "Category Name",
                "Items",
                "Claimed Amount",
                "Reimbursed Amount",
                "Percent of Total",
            ]
        ]
        for category in report_data["categories"]:
            rows.append(
                [
                    ExpenseCategoriesReportsWebMixin._report_csv_text(
                        category["category_code"]
                    ),
                    ExpenseCategoriesReportsWebMixin._report_csv_text(
                        category["category_name"]
                    ),
                    category["item_count"],
                    f"{category['claimed_amount']:.2f}",
                    f"{category['approved_amount']:.2f}",
                    f"{category['percentage']:.1f}",
                ]
            )

        rows.append(
            [
                "TOTAL",
                "",
                sum(category["item_count"] for category in report_data["categories"]),
                f"{report_data['total_claimed']:.2f}",
                f"{report_data['total_approved']:.2f}",
                "100.0" if report_data["categories"] else "0.0",
            ]
        )

        effective_start = report_data["start_date"].isoformat()
        effective_end = report_data["end_date"].isoformat()
        filename = f"expense_by_category_{effective_start}_to_{effective_end}.csv"
        return ExpenseCategoriesReportsWebMixin._report_csv_response(filename, rows)

    @staticmethod
    def expense_by_employee_report_response(
        request: Request,
        auth,
        db,
        start_date: str | None,
        end_date: str | None,
        department_id: str | None,
    ) -> HTMLResponse:
        from app.services.people.hr import DepartmentFilters, OrganizationService

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        org_svc = OrganizationService(db, org_id)
        parsed_start = ExpenseCategoriesReportsWebMixin._safe_iso_date(start_date)
        parsed_end = ExpenseCategoriesReportsWebMixin._safe_iso_date(end_date)
        parsed_dept = ExpenseCategoriesReportsWebMixin._safe_report_uuid(department_id)
        report_data = svc.get_expense_by_employee_report(
            org_id,
            start_date=parsed_start,
            end_date=parsed_end,
            department_id=parsed_dept,
        )
        departments = org_svc.list_departments(
            DepartmentFilters(is_active=True), PaginationParams(limit=200)
        ).items
        context = base_context(request, auth, "Expense by Employee Report", "expense")
        context.update(
            {
                "report": report_data,
                "departments": departments,
                "start_date": (
                    parsed_start.isoformat()
                    if parsed_start
                    else report_data["start_date"].isoformat()
                ),
                "end_date": (
                    parsed_end.isoformat()
                    if parsed_end
                    else report_data["end_date"].isoformat()
                ),
                "department_id": str(parsed_dept) if parsed_dept else None,
            }
        )
        return templates.TemplateResponse(
            request, "expense/reports/by_employee.html", context
        )

    @staticmethod
    def expense_by_employee_export_response(
        auth,
        db,
        start_date: str | None,
        end_date: str | None,
        department_id: str | None,
    ) -> Response:
        """Export the filtered employee breakdown and totals as CSV."""
        org_id = coerce_uuid(auth.organization_id)
        parsed_start = ExpenseCategoriesReportsWebMixin._safe_iso_date(start_date)
        parsed_end = ExpenseCategoriesReportsWebMixin._safe_iso_date(end_date)
        parsed_dept = ExpenseCategoriesReportsWebMixin._safe_report_uuid(department_id)
        report_data = ExpenseService(db).get_expense_by_employee_report(
            org_id,
            start_date=parsed_start,
            end_date=parsed_end,
            department_id=parsed_dept,
        )

        rows: list[list[object]] = [
            [
                "Employee",
                "Department",
                "Claims",
                "Claimed Amount",
                "Reimbursed Amount",
                "Approval Rate",
            ]
        ]
        for employee in report_data["employees"]:
            approval_rate = (
                employee["approved_amount"] / employee["claimed_amount"] * 100
                if employee["claimed_amount"] > 0
                else 0
            )
            rows.append(
                [
                    ExpenseCategoriesReportsWebMixin._report_csv_text(
                        employee["employee_name"]
                    ),
                    ExpenseCategoriesReportsWebMixin._report_csv_text(
                        employee["department_name"]
                    ),
                    employee["claim_count"],
                    f"{employee['claimed_amount']:.2f}",
                    f"{employee['approved_amount']:.2f}",
                    f"{approval_rate:.0f}",
                ]
            )

        overall_rate = (
            report_data["total_approved"] / report_data["total_claimed"] * 100
            if report_data["total_claimed"] > 0
            else 0
        )
        rows.append(
            [
                "TOTAL",
                "",
                sum(employee["claim_count"] for employee in report_data["employees"]),
                f"{report_data['total_claimed']:.2f}",
                f"{report_data['total_approved']:.2f}",
                f"{overall_rate:.0f}",
            ]
        )

        effective_start = report_data["start_date"].isoformat()
        effective_end = report_data["end_date"].isoformat()
        department_suffix = f"_department_{parsed_dept}" if parsed_dept else ""
        filename = (
            f"expense_by_employee_{effective_start}_to_{effective_end}"
            f"{department_suffix}.csv"
        )
        return ExpenseCategoriesReportsWebMixin._report_csv_response(filename, rows)

    @staticmethod
    def expense_trends_report_response(
        request: Request,
        auth,
        db,
        months: int,
        start_date: str | None,
        end_date: str | None,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        parsed_start = ExpenseCategoriesReportsWebMixin._safe_iso_date(start_date)
        parsed_end = ExpenseCategoriesReportsWebMixin._safe_iso_date(end_date)
        report_data = ExpenseService(db).get_expense_trends_report(
            org_id,
            months=months,
            start_date=parsed_start,
            end_date=parsed_end,
        )
        context = base_context(request, auth, "Expense Trends Report", "expense")
        context.update(
            {
                "report": report_data,
                "start_date": report_data["start_date"].isoformat(),
                "end_date": report_data["end_date"].isoformat(),
                "active_filters": build_active_filters(
                    params={
                        "start_date": parsed_start.isoformat()
                        if parsed_start
                        else None,
                        "end_date": parsed_end.isoformat() if parsed_end else None,
                    },
                    labels={"start_date": "From", "end_date": "To"},
                ),
            }
        )
        return templates.TemplateResponse(
            request, "expense/reports/trends.html", context
        )

    @staticmethod
    def expense_trends_export_response(
        auth,
        db,
        months: int,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> Response:
        """Export the selected monthly trend window and totals as CSV."""
        org_id = coerce_uuid(auth.organization_id)
        parsed_start = ExpenseCategoriesReportsWebMixin._safe_iso_date(start_date)
        parsed_end = ExpenseCategoriesReportsWebMixin._safe_iso_date(end_date)
        report_data = ExpenseService(db).get_expense_trends_report(
            org_id,
            months=months,
            start_date=parsed_start,
            end_date=parsed_end,
        )
        rows: list[list[object]] = [
            [
                "Month",
                "Claims",
                "Claimed Amount",
                "Reimbursed Amount",
                "Variance vs Average",
            ]
        ]
        for month in report_data["months"]:
            variance = (
                (month["claimed_amount"] - report_data["average_monthly"])
                / report_data["average_monthly"]
                * 100
                if report_data["average_monthly"] > 0
                else 0
            )
            rows.append(
                [
                    month["month_label"],
                    month["claim_count"],
                    f"{month['claimed_amount']:.2f}",
                    f"{month['approved_amount']:.2f}",
                    f"{variance:.0f}",
                ]
            )
        rows.extend(
            [
                [
                    "TOTAL",
                    sum(month["claim_count"] for month in report_data["months"]),
                    f"{report_data['total_claimed']:.2f}",
                    f"{report_data['total_approved']:.2f}",
                    "",
                ],
                [
                    "AVERAGE MONTHLY",
                    "",
                    f"{report_data['average_monthly']:.2f}",
                    "",
                    "",
                ],
            ]
        )
        effective_start = report_data["start_date"].isoformat()
        effective_end = report_data["end_date"].isoformat()
        filename = f"expense_trends_{effective_start}_to_{effective_end}.csv"
        return ExpenseCategoriesReportsWebMixin._report_csv_response(filename, rows)

    @staticmethod
    def my_approvals_report_response(
        request: Request, auth, db, start_date: str | None, end_date: str | None
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        approver_id = coerce_uuid(auth.employee_id) if auth.employee_id else None
        svc = ExpenseService(db)
        limit_svc = ExpenseLimitService(db)

        parsed_start = ExpenseCategoriesReportsWebMixin._safe_iso_date(start_date)
        parsed_end = ExpenseCategoriesReportsWebMixin._safe_iso_date(end_date)

        report_data = (
            svc.get_my_approvals_report(
                org_id,
                approver_id=approver_id,
                start_date=parsed_start,
                end_date=parsed_end,
            )
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
            budget_balance = limit_svc.get_approver_weekly_budget_balance(
                org_id,
                approver_id,
            )
            if budget_balance is not None:
                weekly_balance = {
                    "usage_label": budget_balance.usage_label,
                    "budget": budget_balance.budget,
                    "used": budget_balance.used,
                    "remaining": budget_balance.remaining,
                    "last_reset_at": budget_balance.last_reset_at,
                }

        context = base_context(
            request, auth, "My Approvals Report", "reports-my-approvals"
        )
        context.update(
            {
                "report": report_data,
                "weekly_balance": weekly_balance,
                "start_date": (
                    parsed_start.isoformat()
                    if parsed_start
                    else report_data["start_date"].isoformat()
                ),
                "end_date": (
                    parsed_end.isoformat()
                    if parsed_end
                    else report_data["end_date"].isoformat()
                ),
                "active_filters": build_active_filters(
                    params={
                        "start_date": parsed_start.isoformat()
                        if parsed_start
                        else None,
                        "end_date": parsed_end.isoformat() if parsed_end else None,
                    },
                    labels={"start_date": "From", "end_date": "To"},
                ),
            }
        )
        return templates.TemplateResponse(
            request, "expense/reports/my_approvals.html", context
        )
