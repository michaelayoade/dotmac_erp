"""
GL Account Web Service - Account-related web view methods.

Provides view-focused data and operations for GL account web routes.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.gl.account import Account, AccountType, NormalBalance
from app.models.finance.gl.account_balance import AccountBalance, BalanceType
from app.models.finance.gl.account_category import AccountCategory, IFRSCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.services.audit_info import get_audit_service
from app.services.common import coerce_uuid
from app.services.finance.gl.web.base import (
    account_detail_view,
    account_form_view,
    category_option_view,
    format_currency,
    ifrs_label,
    parse_category,
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
    balances = (
        db.query(
            AccountBalance.account_id,
            AccountBalance.net_balance,
        )
        .join(
            FiscalPeriod,
            AccountBalance.fiscal_period_id == FiscalPeriod.fiscal_period_id,
        )
        .filter(
            AccountBalance.account_id.in_(account_ids),
            AccountBalance.balance_type == BalanceType.ACTUAL,
            FiscalPeriod.status.in_(
                [PeriodStatus.SOFT_CLOSED, PeriodStatus.HARD_CLOSED]
            ),
        )
        .order_by(FiscalPeriod.end_date.desc())
        .all()
    )

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
    recent_periods = (
        db.query(FiscalPeriod)
        .filter(
            FiscalPeriod.organization_id == coerce_uuid(organization_id),
            FiscalPeriod.status.in_(
                [PeriodStatus.SOFT_CLOSED, PeriodStatus.HARD_CLOSED]
            ),
        )
        .order_by(FiscalPeriod.end_date.desc())
        .limit(periods)
        .all()
    )

    if not recent_periods:
        return {}

    period_ids = [p.fiscal_period_id for p in recent_periods]

    balances = (
        db.query(
            AccountBalance.account_id,
            AccountBalance.fiscal_period_id,
            AccountBalance.net_balance,
        )
        .filter(
            AccountBalance.account_id.in_(account_ids),
            AccountBalance.fiscal_period_id.in_(period_ids),
            AccountBalance.balance_type == BalanceType.ACTUAL,
        )
        .all()
    )

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
        search: Optional[str],
        category: Optional[str],
        status: Optional[str],
        page: int,
        limit: int = 50,
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
        org_id = coerce_uuid(organization_id)
        offset = (page - 1) * limit

        is_active = None
        if status == "active":
            is_active = True
        elif status == "inactive":
            is_active = False

        category_value = parse_category(category)

        query = (
            db.query(Account)
            .join(AccountCategory, Account.category_id == AccountCategory.category_id)
            .filter(Account.organization_id == org_id)
        )

        if is_active is not None:
            query = query.filter(Account.is_active == is_active)
        if category_value:
            query = query.filter(AccountCategory.ifrs_category == category_value)
        if search:
            search_pattern = f"%{search}%"
            query = query.filter(
                (Account.account_code.ilike(search_pattern))
                | (Account.account_name.ilike(search_pattern))
                | (Account.search_terms.ilike(search_pattern))
            )

        total_count = query.with_entities(func.count(Account.account_id)).scalar() or 0
        accounts = (
            query.order_by(Account.account_code).limit(limit).offset(offset).all()
        )

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

        total_pages = max(1, (total_count + limit - 1) // limit)

        logger.debug("list_accounts_context: found %d accounts", total_count)

        return {
            "accounts": accounts_view,
            "search": search,
            "category": category,
            "status": status,
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
        account_id: Optional[str] = None,
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

        categories = (
            db.query(AccountCategory)
            .filter(
                AccountCategory.organization_id == org_id,
                AccountCategory.is_active.is_(True),
            )
            .order_by(AccountCategory.category_code)
            .all()
        )

        if not categories:
            # Seed default categories
            defaults = [
                ("AST", "Assets", IFRSCategory.ASSETS),
                ("LIA", "Liabilities", IFRSCategory.LIABILITIES),
                ("EQT", "Equity", IFRSCategory.EQUITY),
                ("REV", "Revenue", IFRSCategory.REVENUE),
                ("EXP", "Expenses", IFRSCategory.EXPENSES),
                (
                    "OCI",
                    "Other Comprehensive Income",
                    IFRSCategory.OTHER_COMPREHENSIVE_INCOME,
                ),
            ]
            seeded = []
            for index, (code, name, ifrs_cat) in enumerate(defaults, start=1):
                seeded.append(
                    AccountCategory(
                        organization_id=org_id,
                        category_code=code,
                        category_name=name,
                        description=f"Default {name} category",
                        ifrs_category=ifrs_cat,
                        hierarchy_level=1,
                        display_order=index,
                        is_active=True,
                    )
                )
            db.add_all(seeded)
            db.commit()
            categories = (
                db.query(AccountCategory)
                .filter(
                    AccountCategory.organization_id == org_id,
                    AccountCategory.is_active.is_(True),
                )
                .order_by(AccountCategory.category_code)
                .all()
            )

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
        subledger_type: Optional[str] = None,
        is_cash_equivalent: bool = False,
        is_financial_instrument: bool = False,
    ) -> tuple[Optional[Account], Optional[str]]:
        """Create a new GL account. Returns (account, error)."""
        logger.debug(
            "create_account: org=%s code=%s name=%s",
            organization_id,
            account_code,
            account_name,
        )
        org_id = coerce_uuid(organization_id)

        try:
            account_type_enum = AccountType(account_type)
        except ValueError:
            logger.warning("create_account: invalid account type %s", account_type)
            return None, f"Invalid account type: {account_type}"

        try:
            normal_balance_enum = NormalBalance(normal_balance)
        except ValueError:
            logger.warning("create_account: invalid normal balance %s", normal_balance)
            return None, f"Invalid normal balance: {normal_balance}"

        cat_id = coerce_uuid(category_id)
        category = db.get(AccountCategory, cat_id)
        if not category or category.organization_id != org_id:
            return None, "Invalid account category"

        existing = (
            db.query(Account)
            .filter(Account.organization_id == org_id)
            .filter(Account.account_code == account_code)
            .first()
        )
        if existing:
            return None, f"Account code '{account_code}' already exists"

        try:
            account = Account(
                organization_id=org_id,
                account_code=account_code,
                account_name=account_name,
                description=description or None,
                search_terms=search_terms or None,
                category_id=cat_id,
                account_type=account_type_enum,
                normal_balance=normal_balance_enum,
                is_multi_currency=is_multi_currency,
                default_currency_code=default_currency_code,
                is_active=is_active,
                is_posting_allowed=is_posting_allowed,
                is_budgetable=is_budgetable,
                is_reconciliation_required=is_reconciliation_required,
                subledger_type=subledger_type or None,
                is_cash_equivalent=is_cash_equivalent,
                is_financial_instrument=is_financial_instrument,
            )
            db.add(account)
            db.commit()
            db.refresh(account)
            logger.info(
                "create_account: created %s for org %s", account.account_code, org_id
            )
            return account, None

        except Exception as e:
            db.rollback()
            logger.exception("create_account: failed for org %s", org_id)
            return None, f"Failed to create account: {str(e)}"

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
        subledger_type: Optional[str] = None,
        is_cash_equivalent: bool = False,
        is_financial_instrument: bool = False,
    ) -> tuple[Optional[Account], Optional[str]]:
        """Update an existing GL account. Returns (account, error)."""
        logger.debug(
            "update_account: org=%s account_id=%s", organization_id, account_id
        )
        org_id = coerce_uuid(organization_id)
        acct_id = coerce_uuid(account_id)

        account = db.get(Account, acct_id)
        if not account or account.organization_id != org_id:
            return None, "Account not found"

        try:
            account_type_enum = AccountType(account_type)
        except ValueError:
            return None, f"Invalid account type: {account_type}"

        try:
            normal_balance_enum = NormalBalance(normal_balance)
        except ValueError:
            return None, f"Invalid normal balance: {normal_balance}"

        cat_id = coerce_uuid(category_id)
        category = db.get(AccountCategory, cat_id)
        if not category or category.organization_id != org_id:
            return None, "Invalid account category"

        existing = (
            db.query(Account)
            .filter(Account.organization_id == org_id)
            .filter(Account.account_code == account_code)
            .filter(Account.account_id != acct_id)
            .first()
        )
        if existing:
            return None, f"Account code '{account_code}' already exists"

        try:
            account.account_code = account_code
            account.account_name = account_name
            account.description = description or None
            account.search_terms = search_terms or None
            account.category_id = cat_id
            account.account_type = account_type_enum
            account.normal_balance = normal_balance_enum
            account.is_multi_currency = is_multi_currency
            account.default_currency_code = default_currency_code
            account.is_active = is_active
            account.is_posting_allowed = is_posting_allowed
            account.is_budgetable = is_budgetable
            account.is_reconciliation_required = is_reconciliation_required
            account.subledger_type = subledger_type or None
            account.is_cash_equivalent = is_cash_equivalent
            account.is_financial_instrument = is_financial_instrument

            db.commit()
            db.refresh(account)
            logger.info(
                "update_account: updated %s for org %s", account.account_code, org_id
            )
            return account, None

        except Exception as e:
            db.rollback()
            logger.exception("update_account: failed for org %s", org_id)
            return None, f"Failed to update account: {str(e)}"

    @staticmethod
    def delete_account(
        db: Session,
        organization_id: str,
        account_id: str,
    ) -> Optional[str]:
        """Delete a GL account. Returns error message or None on success."""
        logger.debug(
            "delete_account: org=%s account_id=%s", organization_id, account_id
        )
        org_id = coerce_uuid(organization_id)
        acct_id = coerce_uuid(account_id)

        account = db.get(Account, acct_id)
        if not account or account.organization_id != org_id:
            return "Account not found"

        from app.models.finance.gl.journal_entry_line import JournalEntryLine

        line_count = (
            db.query(JournalEntryLine)
            .filter(JournalEntryLine.account_id == acct_id)
            .count()
        )
        if line_count > 0:
            return f"Cannot delete account with {line_count} journal entries. Deactivate instead."

        balance_count = (
            db.query(AccountBalance)
            .filter(AccountBalance.account_id == acct_id)
            .count()
        )
        if balance_count > 0:
            return "Cannot delete account with balance records. Deactivate instead."

        try:
            db.delete(account)
            db.commit()
            logger.info("delete_account: deleted %s for org %s", acct_id, org_id)
            return None

        except Exception as e:
            db.rollback()
            logger.exception("delete_account: failed for org %s", org_id)
            return f"Failed to delete account: {str(e)}"

    # =========================================================================
    # HTTP Response Methods
    # =========================================================================

    def list_accounts_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        search: Optional[str],
        category: Optional[str],
        status: Optional[str],
        page: int,
    ) -> HTMLResponse:
        """Render accounts list page."""
        context = base_context(request, auth, "Chart of Accounts", "gl")
        context.update(
            self.list_accounts_context(
                db,
                str(auth.organization_id),
                search=search,
                category=category,
                status=status,
                page=page,
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
        default_currency_code: Optional[str],
        is_active: bool,
        is_posting_allowed: bool,
        is_budgetable: bool,
        is_reconciliation_required: bool,
        subledger_type: Optional[str],
        is_cash_equivalent: bool,
        is_financial_instrument: bool,
    ) -> HTMLResponse | RedirectResponse:
        """Handle account creation form submission."""
        org_id = auth.organization_id
        assert org_id is not None
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
            url=f"/finance/gl/accounts/{account.account_id}", status_code=303
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
        default_currency_code: Optional[str],
        is_active: bool,
        is_posting_allowed: bool,
        is_budgetable: bool,
        is_reconciliation_required: bool,
        subledger_type: Optional[str],
        is_cash_equivalent: bool,
        is_financial_instrument: bool,
    ) -> HTMLResponse | RedirectResponse:
        """Handle account update form submission."""
        org_id = auth.organization_id
        assert org_id is not None
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
            url=f"/finance/gl/accounts/{account_id}", status_code=303
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

        return RedirectResponse(url="/finance/gl/accounts", status_code=303)
