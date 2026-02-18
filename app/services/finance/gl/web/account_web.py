"""
GL Account Web Service - Account-related web view methods.

Provides view-focused data and operations for GL account web routes.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.gl.account import Account, AccountType, NormalBalance
from app.models.finance.gl.account_balance import AccountBalance, BalanceType
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.services.audit_info import get_audit_service
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.common.sorting import apply_sort
from app.services.finance.gl.chart_of_accounts import chart_of_accounts_service
from app.services.finance.gl.web.base import (
    account_detail_view,
    account_form_view,
    category_option_view,
    format_currency,
    ifrs_label,
)
from app.services.finance.platform.currency_context import get_currency_context
from app.services.finance.platform.org_context import org_context_service
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


def _calculate_account_balances(
    db: Session,
    organization_id,
    account_ids: list,
) -> dict:
    """Calculate current balances for a list of accounts."""
    if not account_ids:
        return {}

    # Get latest closed period for each account
    balances = db.execute(
        select(
            AccountBalance.account_id,
            AccountBalance.net_balance,
        )
        .join(
            FiscalPeriod,
            AccountBalance.fiscal_period_id == FiscalPeriod.fiscal_period_id,
        )
        .where(
            AccountBalance.account_id.in_(account_ids),
            AccountBalance.balance_type == BalanceType.ACTUAL,
            FiscalPeriod.status.in_(
                [PeriodStatus.SOFT_CLOSED, PeriodStatus.HARD_CLOSED]
            ),
        )
        .order_by(FiscalPeriod.end_date.desc())
    ).all()

    result = {}
    for account_id, net_balance in balances:
        if account_id not in result:
            result[account_id] = net_balance

    return result


def _calculate_account_balance_trends(
    db: Session,
    organization_id,
    account_ids: list,
    periods: int = 6,
) -> dict:
    """Calculate balance trends over recent periods."""
    if not account_ids:
        return {}

    # Get recent closed periods
    recent_periods = list(
        db.scalars(
            select(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == coerce_uuid(organization_id),
                FiscalPeriod.status.in_(
                    [PeriodStatus.SOFT_CLOSED, PeriodStatus.HARD_CLOSED]
                ),
            )
            .order_by(FiscalPeriod.end_date.desc())
            .limit(periods)
        ).all()
    )

    if not recent_periods:
        return {}

    period_ids = [p.fiscal_period_id for p in recent_periods]

    balances = db.execute(
        select(
            AccountBalance.account_id,
            AccountBalance.fiscal_period_id,
            AccountBalance.net_balance,
        ).where(
            AccountBalance.account_id.in_(account_ids),
            AccountBalance.fiscal_period_id.in_(period_ids),
            AccountBalance.balance_type == BalanceType.ACTUAL,
        )
    ).all()

    # Build trend data
    result = {aid: [0.0] * periods for aid in account_ids}
    period_index = {pid: i for i, pid in enumerate(reversed(period_ids))}

    for account_id, period_id, net_balance in balances:
        idx = period_index.get(period_id)
        if idx is not None:
            result[account_id][idx] = float(net_balance)

    return result


class AccountWebService:
    """Web service methods for GL accounts."""

    @staticmethod
    def list_accounts_context(
        db: Session,
        organization_id: str,
        search: str | None,
        category: str | None,
        status: str | None,
        page: int,
        limit: int = 50,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> dict:
        """Get context for account listing page."""
        logger.debug(
            "list_accounts_context: org=%s search=%r category=%s status=%s page=%d",
            organization_id,
            search,
            category,
            status,
            page,
        )
        offset = (page - 1) * limit
        org_id = coerce_uuid(organization_id)
        from app.services.finance.gl.account_query import build_account_query

        query = build_account_query(
            db=db,
            organization_id=organization_id,
            search=search,
            category=category,
            status=status,
        )

        total_count = query.with_entities(func.count(Account.account_id)).scalar() or 0
        column_map = {
            "account_code": Account.account_code,
            "account_name": Account.account_name,
            "is_active": Account.is_active,
        }
        query = apply_sort(
            query, sort, sort_dir, column_map, default=Account.account_code.asc()
        )
        accounts = query.limit(limit).offset(offset).all()

        audit_service = get_audit_service(db)
        creator_ids = [
            account.created_by_user_id
            for account in accounts
            if account.created_by_user_id
        ]
        creator_names = audit_service.get_user_names_batch(creator_ids)

        account_ids = [a.account_id for a in accounts]
        balances = _calculate_account_balances(db, org_id, account_ids)
        balance_trends = _calculate_account_balance_trends(db, org_id, account_ids)

        functional_currency = org_context_service.get_functional_currency(db, org_id)

        accounts_view = []
        for account in accounts:
            category_label = ifrs_label(account.category.ifrs_category)
            account_balance = balances.get(account.account_id, Decimal("0"))
            trend = balance_trends.get(account.account_id)
            accounts_view.append(
                {
                    "account_id": account.account_id,
                    "account_code": account.account_code,
                    "account_name": account.account_name,
                    "description": account.description,
                    "category": category_label,
                    "normal_balance": account.normal_balance.value,
                    "balance": format_currency(account_balance, functional_currency),
                    "balance_trend": trend
                    if trend and any(v != 0 for v in trend)
                    else None,
                    "is_active": account.is_active,
                    "created_at": account.created_at,
                    "created_by_user_id": account.created_by_user_id,
                    "created_by_name": (
                        creator_names.get(account.created_by_user_id)
                        if account.created_by_user_id
                        else None
                    ),
                    "updated_at": account.updated_at,
                }
            )

        active_count = (
            build_account_query(
                db=db,
                organization_id=organization_id,
                search=search,
                category=category,
                status="active",
            )
            .with_entities(func.count(Account.account_id))
            .scalar()
            or 0
        )
        inactive_count = (
            build_account_query(
                db=db,
                organization_id=organization_id,
                search=search,
                category=category,
                status="inactive",
            )
            .with_entities(func.count(Account.account_id))
            .scalar()
            or 0
        )
        total_pages = max(1, (total_count + limit - 1) // limit)
        active_filters = build_active_filters(
            params={
                "category": category,
                "status": status,
                "search": search,
            },
            labels={"category": "Category", "status": "Status", "search": "Search"},
            options={
                "category": {
                    "ASSET": "Assets",
                    "LIABILITY": "Liabilities",
                    "EQUITY": "Equity",
                    "REVENUE": "Revenue",
                    "EXPENSE": "Expenses",
                },
                "status": {"active": "Active", "inactive": "Inactive"},
            },
        )

        logger.debug("list_accounts_context: found %d accounts", total_count)

        return {
            "accounts": accounts_view,
            "search": search,
            "category": category,
            "status": status,
            "sort": sort,
            "sort_dir": sort_dir,
            "active_filters": active_filters,
            "active_count": active_count,
            "inactive_count": inactive_count,
            "page": page,
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "total_pages": total_pages,
        }

    @staticmethod
    def account_form_context(
        db: Session,
        organization_id: str,
        account_id: str | None = None,
    ) -> dict:
        """Get context for account create/edit form."""
        logger.debug(
            "account_form_context: org=%s account_id=%s", organization_id, account_id
        )
        org_id = coerce_uuid(organization_id)
        account = None
        if account_id:
            account = db.get(Account, coerce_uuid(account_id))
            if not account or account.organization_id != org_id:
                account = None

        categories = chart_of_accounts_service.ensure_default_categories(db, org_id)

        context = {
            "account": account_form_view(account) if account else None,
            "account_categories": [category_option_view(cat) for cat in categories],
            "account_types": [value.value for value in AccountType],
            "normal_balances": [value.value for value in NormalBalance],
            "subledger_types": ["AR", "AP", "INVENTORY", "ASSET", "BANK"],
        }
        context.update(get_currency_context(db, organization_id))
        return context

    @staticmethod
    def account_detail_context(
        db: Session,
        organization_id: str,
        account_id: str,
    ) -> dict:
        """Get context for account detail page."""
        logger.debug(
            "account_detail_context: org=%s account_id=%s", organization_id, account_id
        )
        org_id = coerce_uuid(organization_id)
        account = db.get(Account, coerce_uuid(account_id))
        if not account or account.organization_id != org_id:
            return {"account": None}

        return {"account": account_detail_view(account)}

    @staticmethod
    def create_account(
        db: Session,
        organization_id: str,
        account_code: str,
        account_name: str,
        category_id: str,
        account_type: str,
        normal_balance: str,
        description: str = "",
        search_terms: str = "",
        is_multi_currency: bool = False,
        default_currency_code: str = settings.default_functional_currency_code,
        is_active: bool = True,
        is_posting_allowed: bool = True,
        is_budgetable: bool = False,
        is_reconciliation_required: bool = False,
        subledger_type: str | None = None,
        is_cash_equivalent: bool = False,
        is_financial_instrument: bool = False,
    ) -> tuple[Account | None, str | None]:
        """Create a new GL account. Returns (account, error)."""
        logger.debug(
            "create_account: org=%s code=%s name=%s",
            organization_id,
            account_code,
            account_name,
        )
        org_id = coerce_uuid(organization_id)
        payload = {
            "account_code": account_code,
            "account_name": account_name,
            "category_id": category_id,
            "account_type": account_type,
            "normal_balance": normal_balance,
            "description": description,
            "search_terms": search_terms,
            "is_multi_currency": is_multi_currency,
            "default_currency_code": default_currency_code,
            "is_active": is_active,
            "is_posting_allowed": is_posting_allowed,
            "is_budgetable": is_budgetable,
            "is_reconciliation_required": is_reconciliation_required,
            "subledger_type": subledger_type,
            "is_cash_equivalent": is_cash_equivalent,
            "is_financial_instrument": is_financial_instrument,
        }

        try:
            input_data = chart_of_accounts_service.build_input_from_payload(
                db, org_id, payload
            )
            account = chart_of_accounts_service.create_account(
                db=db,
                organization_id=org_id,
                input=input_data,
            )
            return account, None
        except Exception as e:
            logger.exception("create_account: failed for org %s", org_id)
            return None, str(e)

    @staticmethod
    def update_account(
        db: Session,
        organization_id: str,
        account_id: str,
        account_code: str,
        account_name: str,
        category_id: str,
        account_type: str,
        normal_balance: str,
        description: str = "",
        search_terms: str = "",
        is_multi_currency: bool = False,
        default_currency_code: str = settings.default_functional_currency_code,
        is_active: bool = True,
        is_posting_allowed: bool = True,
        is_budgetable: bool = False,
        is_reconciliation_required: bool = False,
        subledger_type: str | None = None,
        is_cash_equivalent: bool = False,
        is_financial_instrument: bool = False,
    ) -> tuple[Account | None, str | None]:
        """Update an existing GL account. Returns (account, error)."""
        logger.debug(
            "update_account: org=%s account_id=%s", organization_id, account_id
        )
        org_id = coerce_uuid(organization_id)
        payload = {
            "account_code": account_code,
            "account_name": account_name,
            "category_id": category_id,
            "account_type": account_type,
            "normal_balance": normal_balance,
            "description": description,
            "search_terms": search_terms,
            "is_multi_currency": is_multi_currency,
            "default_currency_code": default_currency_code,
            "is_active": is_active,
            "is_posting_allowed": is_posting_allowed,
            "is_budgetable": is_budgetable,
            "is_reconciliation_required": is_reconciliation_required,
            "subledger_type": subledger_type,
            "is_cash_equivalent": is_cash_equivalent,
            "is_financial_instrument": is_financial_instrument,
        }

        try:
            input_data = chart_of_accounts_service.build_input_from_payload(
                db, org_id, payload
            )
            account = chart_of_accounts_service.update_account_full(
                db=db,
                organization_id=org_id,
                account_id=coerce_uuid(account_id),
                input=input_data,
            )
            return account, None
        except Exception as e:
            logger.exception("update_account: failed for org %s", org_id)
            return None, str(e)

    @staticmethod
    def delete_account(
        db: Session,
        organization_id: str,
        account_id: str,
    ) -> str | None:
        """Delete a GL account. Returns error message or None on success."""
        logger.debug(
            "delete_account: org=%s account_id=%s", organization_id, account_id
        )
        org_id = coerce_uuid(organization_id)
        try:
            chart_of_accounts_service.delete_account(
                db=db,
                organization_id=org_id,
                account_id=coerce_uuid(account_id),
            )
            logger.info("delete_account: deleted %s for org %s", account_id, org_id)
            return None
        except Exception as e:
            logger.exception("delete_account: failed for org %s", org_id)
            return str(e)

    # =========================================================================
    # HTTP Response Methods
    # =========================================================================

    def list_accounts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: str | None,
        category: str | None,
        status: str | None,
        page: int,
        sort: str | None = None,
        sort_dir: str | None = None,
    ) -> HTMLResponse:
        """Render accounts list page."""
        context = base_context(request, auth, "Chart of Accounts", "gl", db=db)
        context.update(
            self.list_accounts_context(
                db,
                str(auth.organization_id),
                search=search,
                category=category,
                status=status,
                page=page,
                sort=sort,
                sort_dir=sort_dir,
            )
        )
        return templates.TemplateResponse(request, "finance/gl/accounts.html", context)

    def account_new_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new account form page."""
        context = base_context(request, auth, "New Account", "gl")
        context.update(self.account_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(
            request, "finance/gl/account_form.html", context
        )

    def account_detail_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
    ) -> HTMLResponse:
        """Render account detail page."""
        context = base_context(request, auth, "Account Details", "gl")
        context.update(
            self.account_detail_context(
                db,
                str(auth.organization_id),
                account_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/gl/account_detail.html", context
        )

    def account_edit_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
    ) -> HTMLResponse:
        """Render account edit form page."""
        context = base_context(request, auth, "Edit Account", "gl")
        context.update(
            self.account_form_context(
                db,
                str(auth.organization_id),
                account_id=account_id,
            )
        )
        return templates.TemplateResponse(
            request, "finance/gl/account_form.html", context
        )

    def create_account_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_code: str,
        account_name: str,
        category_id: str,
        account_type: str,
        normal_balance: str,
        description: str,
        search_terms: str,
        is_multi_currency: bool,
        default_currency_code: str | None,
        is_active: bool,
        is_posting_allowed: bool,
        is_budgetable: bool,
        is_reconciliation_required: bool,
        subledger_type: str | None,
        is_cash_equivalent: bool,
        is_financial_instrument: bool,
    ) -> HTMLResponse | RedirectResponse:
        """Handle account creation form submission."""
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        currency_code = (
            default_currency_code
            or org_context_service.get_functional_currency(
                db,
                org_id,
            )
        )

        account, error = self.create_account(
            db,
            str(org_id),
            account_code=account_code,
            account_name=account_name,
            category_id=category_id,
            account_type=account_type,
            normal_balance=normal_balance,
            description=description,
            search_terms=search_terms,
            is_multi_currency=is_multi_currency,
            default_currency_code=currency_code,
            is_active=is_active,
            is_posting_allowed=is_posting_allowed,
            is_budgetable=is_budgetable,
            is_reconciliation_required=is_reconciliation_required,
            subledger_type=subledger_type,
            is_cash_equivalent=is_cash_equivalent,
            is_financial_instrument=is_financial_instrument,
        )

        if error or account is None:
            context = base_context(request, auth, "New Account", "gl")
            context.update(self.account_form_context(db, str(auth.organization_id)))
            context["error"] = error or "Account creation failed"
            context["form_data"] = {
                "account_code": account_code,
                "account_name": account_name,
                "category_id": category_id,
                "account_type": account_type,
                "normal_balance": normal_balance,
                "description": description,
                "search_terms": search_terms,
                "is_multi_currency": is_multi_currency,
                "default_currency_code": currency_code,
                "is_active": is_active,
                "is_posting_allowed": is_posting_allowed,
                "is_budgetable": is_budgetable,
                "is_reconciliation_required": is_reconciliation_required,
                "subledger_type": subledger_type,
                "is_cash_equivalent": is_cash_equivalent,
                "is_financial_instrument": is_financial_instrument,
            }
            return templates.TemplateResponse(
                request, "finance/gl/account_form.html", context
            )

        return RedirectResponse(
            url=f"/finance/gl/accounts/{account.account_id}?saved=1", status_code=303
        )

    def update_account_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
        account_code: str,
        account_name: str,
        category_id: str,
        account_type: str,
        normal_balance: str,
        description: str,
        search_terms: str,
        is_multi_currency: bool,
        default_currency_code: str | None,
        is_active: bool,
        is_posting_allowed: bool,
        is_budgetable: bool,
        is_reconciliation_required: bool,
        subledger_type: str | None,
        is_cash_equivalent: bool,
        is_financial_instrument: bool,
    ) -> HTMLResponse | RedirectResponse:
        """Handle account update form submission."""
        org_id = auth.organization_id
        if org_id is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        currency_code = (
            default_currency_code
            or org_context_service.get_functional_currency(
                db,
                org_id,
            )
        )

        _, error = self.update_account(
            db,
            str(org_id),
            account_id=account_id,
            account_code=account_code,
            account_name=account_name,
            category_id=category_id,
            account_type=account_type,
            normal_balance=normal_balance,
            description=description,
            search_terms=search_terms,
            is_multi_currency=is_multi_currency,
            default_currency_code=currency_code,
            is_active=is_active,
            is_posting_allowed=is_posting_allowed,
            is_budgetable=is_budgetable,
            is_reconciliation_required=is_reconciliation_required,
            subledger_type=subledger_type,
            is_cash_equivalent=is_cash_equivalent,
            is_financial_instrument=is_financial_instrument,
        )

        if error:
            context = base_context(request, auth, "Edit Account", "gl")
            context.update(
                self.account_form_context(
                    db,
                    str(auth.organization_id),
                    account_id=account_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/gl/account_form.html", context
            )

        return RedirectResponse(
            url=f"/finance/gl/accounts/{account_id}?saved=1", status_code=303
        )

    def delete_account_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        account_id: str,
    ) -> HTMLResponse | RedirectResponse:
        """Handle account deletion."""
        error = self.delete_account(db, str(auth.organization_id), account_id)

        if error:
            context = base_context(request, auth, "Account Details", "gl")
            context.update(
                self.account_detail_context(
                    db,
                    str(auth.organization_id),
                    account_id,
                )
            )
            context["error"] = error
            return templates.TemplateResponse(
                request, "finance/gl/account_detail.html", context
            )

        return RedirectResponse(
            url="/finance/gl/accounts?success=Record+deleted+successfully",
            status_code=303,
        )
