"""
Expense claims and category web view service.

Provides view-focused data and operations for expense claim-related web routes.
"""

from __future__ import annotations

import logging
from datetime import date as date_type
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.domain_settings import SettingDomain
from app.models.expense.expense_claim import ExpenseClaim, ExpenseClaimItem, ExpenseClaimStatus
from app.models.people.hr.employee import Employee
from app.services.common import PaginationParams, coerce_uuid
from app.services.expense.expense_service import (
    ExpenseService,
    ExpenseServiceError,
    ExpenseClaimStatusError,
)
from app.services.settings_spec import resolve_value
from app.templates import templates
from app.web.deps import WebAuthContext, base_context


class ExpenseClaimsWebService:
    """Web service methods for expense claims, categories, and reports."""

    @staticmethod
    def _form_str(form: Any, key: str) -> str:
        value = form.get(key)
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def claims_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str],
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        status_value = None
        if status:
            try:
                status_value = ExpenseClaimStatus(status)
            except ValueError:
                status_value = None

        start = date_type.fromisoformat(start_date) if start_date else None
        end = date_type.fromisoformat(end_date) if end_date else None

        query = (
            db.query(ExpenseClaim)
            .options(joinedload(ExpenseClaim.employee))
            .filter(ExpenseClaim.organization_id == org_id)
        )
        if status_value:
            query = query.filter(ExpenseClaim.status == status_value)
        if start:
            query = query.filter(ExpenseClaim.claim_date >= start)
        if end:
            query = query.filter(ExpenseClaim.claim_date <= end)

        claims = query.order_by(ExpenseClaim.claim_date.desc()).limit(100).all()

        status_rows = (
            db.query(ExpenseClaim.status, func.count())
            .filter(ExpenseClaim.organization_id == org_id)
            .group_by(ExpenseClaim.status)
            .all()
        )
        status_counts: dict[ExpenseClaimStatus | None, int] = {
            row[0]: row[1] for row in status_rows
        }
        counts = {s.value if s else "UNKNOWN": c for s, c in status_counts.items()}

        context = base_context(request, auth, "Expense Claims", "claims")
        context.update(
            {
                "claims": claims,
                "statuses": [s.value for s in ExpenseClaimStatus],
                "status_counts": counts,
                "filter_status": status or "",
                "filter_start_date": start_date or "",
                "filter_end_date": end_date or "",
            }
        )
        return templates.TemplateResponse(request, "expense/claims_list.html", context)

    @staticmethod
    def claim_detail_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        claim_id: str,
    ) -> HTMLResponse | RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        claim = (
            db.query(ExpenseClaim)
            .options(
                joinedload(ExpenseClaim.items).joinedload(ExpenseClaimItem.category),
                joinedload(ExpenseClaim.employee),
            )
            .filter(ExpenseClaim.organization_id == org_id)
            .filter(ExpenseClaim.claim_id == claim_uuid)
            .first()
        )
        if not claim:
            return RedirectResponse("/expense/claims/list", status_code=302)

        can_approve = auth.has_any_permission(
            [
                "expense:claims:approve:tier1",
                "expense:claims:approve:tier2",
                "expense:claims:approve:tier3",
            ]
        )
        can_submit = (auth.is_admin or can_approve) and claim.status == ExpenseClaimStatus.DRAFT
        can_act = can_approve and claim.status in {
            ExpenseClaimStatus.SUBMITTED,
            ExpenseClaimStatus.PENDING_APPROVAL,
        }
        paystack_enabled = resolve_value(db, SettingDomain.payments, "paystack_enabled")
        transfers_enabled = resolve_value(db, SettingDomain.payments, "paystack_transfers_enabled")
        can_paystack = (
            (auth.is_admin or auth.has_module_access("finance"))
            and bool(paystack_enabled)
            and bool(transfers_enabled)
            and claim.status == ExpenseClaimStatus.APPROVED
        )

        context = base_context(request, auth, f"Claim {claim.claim_number}", "claims")
        context.update(
            {
                "claim": claim,
                "can_submit": can_submit,
                "can_act": can_act,
                "can_paystack": can_paystack,
                "action": request.query_params.get("action"),
                "error": request.query_params.get("error"),
            }
        )
        return templates.TemplateResponse(request, "expense/claim_detail.html", context)

    @staticmethod
    def submit_claim_response(
        claim_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        if not (
            auth.is_admin
            or auth.has_any_permission(
                [
                    "expense:claims:approve:tier1",
                    "expense:claims:approve:tier2",
                    "expense:claims:approve:tier3",
                ]
            )
        ):
            return RedirectResponse("/expense/claims/list?error=permission", status_code=302)

        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        svc = ExpenseService(db)

        try:
            svc.submit_claim(org_id, claim_uuid)
            db.commit()
        except ExpenseClaimStatusError:
            return RedirectResponse(f"/expense/claims/{claim_id}?error=invalid_status", status_code=303)
        except ExpenseServiceError as exc:
            message = quote(str(exc))
            return RedirectResponse(f"/expense/claims/{claim_id}?error={message}", status_code=303)
        except Exception:
            logging.getLogger(__name__).exception(
                "Expense claim submit failed",
                extra={"claim_id": claim_id},
            )
            return RedirectResponse(f"/expense/claims/{claim_id}?error=submit_failed", status_code=303)

        return RedirectResponse(f"/expense/claims/{claim_id}?action=submitted", status_code=303)

    @staticmethod
    def approve_claim_response(
        claim_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        if not auth.has_any_permission(
            [
                "expense:claims:approve:tier1",
                "expense:claims:approve:tier2",
                "expense:claims:approve:tier3",
            ]
        ):
            return RedirectResponse("/expense/claims/list?error=permission", status_code=302)

        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        approver = (
            db.query(Employee)
            .filter(Employee.organization_id == org_id)
            .filter(Employee.person_id == auth.person_id)
            .first()
        )
        approver_id = approver.employee_id if approver else None

        svc = ExpenseService(db)
        try:
            svc.approve_claim(org_id, claim_uuid, approver_id=approver_id)
            db.commit()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(f"/expense/claims/{claim_id}?error=invalid_status", status_code=303)
        except Exception:
            db.rollback()
            logging.getLogger(__name__).exception(
                "Expense claim approval failed",
                extra={"claim_id": claim_id},
            )
            return RedirectResponse(f"/expense/claims/{claim_id}?error=approve_failed", status_code=303)

        return RedirectResponse(f"/expense/claims/{claim_id}?action=approved", status_code=303)

    @staticmethod
    def reject_claim_response(
        claim_id: str,
        reason: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        if not auth.has_any_permission(
            [
                "expense:claims:approve:tier1",
                "expense:claims:approve:tier2",
                "expense:claims:approve:tier3",
            ]
        ):
            return RedirectResponse("/expense/claims/list?error=permission", status_code=302)

        org_id = coerce_uuid(auth.organization_id)
        claim_uuid = coerce_uuid(claim_id)
        approver = (
            db.query(Employee)
            .filter(Employee.organization_id == org_id)
            .filter(Employee.person_id == auth.person_id)
            .first()
        )
        approver_id = approver.employee_id if approver else None

        svc = ExpenseService(db)
        try:
            svc.reject_claim(org_id, claim_uuid, approver_id=approver_id, reason=reason)
            db.commit()
        except ExpenseClaimStatusError:
            db.rollback()
            return RedirectResponse(f"/expense/claims/{claim_id}?error=invalid_status", status_code=303)
        except Exception:
            db.rollback()
            logging.getLogger(__name__).exception(
                "Expense claim rejection failed",
                extra={"claim_id": claim_id},
            )
            return RedirectResponse(f"/expense/claims/{claim_id}?error=reject_failed", status_code=303)

        return RedirectResponse(f"/expense/claims/{claim_id}?action=rejected", status_code=303)

    @staticmethod
    def categories_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str],
        is_active: Optional[str],
        page: int,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        is_active_value: Optional[bool] = None
        if isinstance(is_active, str):
            lowered = is_active.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                is_active_value = True
            elif lowered in {"false", "0", "no", "off"}:
                is_active_value = False

        pagination = PaginationParams.from_page(page, 20)
        result = svc.list_categories(
            org_id,
            search=search,
            is_active=is_active_value,
            pagination=pagination,
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
            }
        )
        return templates.TemplateResponse(request, "expense/categories.html", context)

    @staticmethod
    def new_category_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
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

    @staticmethod
    async def create_category_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse | RedirectResponse:
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        category_code = ExpenseClaimsWebService._form_str(form, "category_code")
        category_name = ExpenseClaimsWebService._form_str(form, "category_name")
        description = ExpenseClaimsWebService._form_str(form, "description")
        expense_account_id = ExpenseClaimsWebService._form_str(form, "expense_account_id")
        max_amount = ExpenseClaimsWebService._form_str(form, "max_amount_per_claim")
        requires_receipt = ExpenseClaimsWebService._form_str(form, "requires_receipt") in {"1", "true", "on", "yes"}
        is_active = ExpenseClaimsWebService._form_str(form, "is_active") in {"1", "true", "on", "yes"}

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

    @staticmethod
    def edit_category_form_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        category_id: str,
    ) -> HTMLResponse:
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

    @staticmethod
    async def update_category_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        category_id: str,
    ) -> HTMLResponse | RedirectResponse:
        from app.models.finance.gl.account import Account
        from app.models.finance.gl.account_category import AccountCategory, IFRSCategory

        form = getattr(request.state, "csrf_form", None)
        if form is None:
            form = await request.form()

        category_code = ExpenseClaimsWebService._form_str(form, "category_code")
        category_name = ExpenseClaimsWebService._form_str(form, "category_name")
        description = ExpenseClaimsWebService._form_str(form, "description")
        expense_account_id = ExpenseClaimsWebService._form_str(form, "expense_account_id")
        max_amount = ExpenseClaimsWebService._form_str(form, "max_amount_per_claim")
        requires_receipt = ExpenseClaimsWebService._form_str(form, "requires_receipt") in {"1", "true", "on", "yes"}
        is_active = ExpenseClaimsWebService._form_str(form, "is_active") in {"1", "true", "on", "yes"}

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

    @staticmethod
    def delete_category_response(
        category_id: str,
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        svc.update_category(org_id, coerce_uuid(category_id), is_active=False)
        db.commit()
        return RedirectResponse(url="/expense/categories", status_code=303)

    @staticmethod
    def expense_summary_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        parsed_start = date_type.fromisoformat(start_date) if start_date else None
        parsed_end = date_type.fromisoformat(end_date) if end_date else None

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

    @staticmethod
    def expense_by_category_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str],
        end_date: Optional[str],
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        parsed_start = date_type.fromisoformat(start_date) if start_date else None
        parsed_end = date_type.fromisoformat(end_date) if end_date else None

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

    @staticmethod
    def expense_by_employee_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        start_date: Optional[str],
        end_date: Optional[str],
        department_id: Optional[str],
    ) -> HTMLResponse:
        from app.services.people.hr import OrganizationService, DepartmentFilters

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)
        org_svc = OrganizationService(db, org_id)

        parsed_start = date_type.fromisoformat(start_date) if start_date else None
        parsed_end = date_type.fromisoformat(end_date) if end_date else None
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

    @staticmethod
    def expense_trends_report_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        months: int,
    ) -> HTMLResponse:
        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        report_data = svc.get_expense_trends_report(org_id, months=months)

        context = base_context(request, auth, "Expense Trends Report", "expense")
        context.update({
            "report": report_data,
            "months": months,
        })
        return templates.TemplateResponse(request, "expense/reports/trends.html", context)

    @staticmethod
    def cash_advances_list_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        status: Optional[str],
        page: int,
    ) -> HTMLResponse:
        from app.models.expense.cash_advance import CashAdvanceStatus

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        pagination = PaginationParams.from_page(page, 20)
        status_filter = None
        if status:
            try:
                status_filter = CashAdvanceStatus(status)
            except ValueError:
                pass

        result = svc.list_advances(
            org_id,
            status=status_filter,
            pagination=pagination,
        )

        context = base_context(request, auth, "Cash Advances", "advances")
        context.update({
            "advances": result.items,
            "status": status,
            "statuses": [s.value for s in CashAdvanceStatus],
            "page": page,
            "total_pages": result.total_pages,
            "total": result.total,
            "has_prev": result.has_prev,
            "has_next": result.has_next,
        })
        return templates.TemplateResponse(request, "expense/advances/list.html", context)

    @staticmethod
    def cash_advance_detail_response(
        request: Request,
        auth: WebAuthContext,
        db: Session,
        advance_id: str,
    ) -> HTMLResponse:
        from app.models.expense.cash_advance import CashAdvanceStatus
        from app.models.finance.banking.bank_account import BankAccount, BankAccountStatus

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        try:
            advance = svc.get_advance(org_id, coerce_uuid(advance_id))
        except Exception:
            context = base_context(request, auth, "Cash Advance", "advances")
            context["advance"] = None
            context["error"] = "Advance not found"
            return templates.TemplateResponse(request, "expense/advances/detail.html", context)

        bank_accounts = db.query(BankAccount).filter(
            BankAccount.organization_id == org_id,
            BankAccount.status == BankAccountStatus.active,
        ).order_by(BankAccount.account_name).all()

        linked_claims = []
        if advance.status in [CashAdvanceStatus.DISBURSED, CashAdvanceStatus.PARTIALLY_SETTLED]:
            claims = svc.list_claims(
                org_id,
                employee_id=advance.employee_id,
                pagination=PaginationParams(offset=0, limit=20),
            )
            linked_claims = [c for c in claims.items if c.cash_advance_id == advance.advance_id]

        context = base_context(request, auth, f"Advance {advance.advance_number}", "advances")
        context.update({
            "advance": advance,
            "bank_accounts": bank_accounts,
            "linked_claims": linked_claims,
            "can_disburse": advance.status == CashAdvanceStatus.APPROVED,
            "can_settle": advance.status in [CashAdvanceStatus.DISBURSED, CashAdvanceStatus.PARTIALLY_SETTLED],
        })
        return templates.TemplateResponse(request, "expense/advances/detail.html", context)

    @staticmethod
    def disburse_cash_advance_response(
        advance_id: str,
        bank_account_id: str,
        payment_mode: str,
        payment_reference: Optional[str],
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        from app.tasks.expense import post_cash_advance_disbursement

        org_id = coerce_uuid(auth.organization_id)
        svc = ExpenseService(db)

        try:
            advance = svc.disburse_advance(
                org_id,
                coerce_uuid(advance_id),
                payment_reference=payment_reference,
            )
            db.commit()

            post_cash_advance_disbursement.delay(
                organization_id=str(org_id),
                advance_id=str(advance.advance_id),
                user_id=str(auth.user_id),
                bank_account_id=bank_account_id,
            )
        except ExpenseServiceError:
            db.rollback()

        return RedirectResponse(f"/expense/advances/{advance_id}", status_code=303)

    @staticmethod
    def settle_cash_advance_response(
        advance_id: str,
        claim_id: str,
        settlement_amount: Optional[str],
        auth: WebAuthContext,
        db: Session,
    ) -> RedirectResponse:
        from app.tasks.expense import settle_cash_advance_with_claim

        org_id = coerce_uuid(auth.organization_id)
        try:
            settle_cash_advance_with_claim.delay(
                organization_id=str(org_id),
                advance_id=advance_id,
                claim_id=claim_id,
                user_id=str(auth.user_id),
                settlement_amount=settlement_amount,
            )
            db.commit()
        except Exception:
            db.rollback()

        return RedirectResponse(f"/expense/advances/{advance_id}", status_code=303)


expense_claims_web_service = ExpenseClaimsWebService()
