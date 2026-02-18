"""
GL Period Web Service - Fiscal period and trial balance web view methods.

Provides view-focused data and operations for GL fiscal period web routes.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_balance import AccountBalance, BalanceType
from app.models.finance.gl.account_category import AccountCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.fiscal_year import FiscalYear
from app.services.common import coerce_uuid
from app.services.common_filters import build_active_filters
from app.services.finance.gl.fiscal_period import (
    FiscalPeriodInput,
    fiscal_period_service,
)
from app.services.finance.gl.web.base import (
    fiscal_year_option_view,
    format_currency,
    format_date,
    ifrs_label,
    parse_date,
    period_option_view,
)
from app.templates import templates
from app.web.deps import WebAuthContext, base_context

logger = logging.getLogger(__name__)


class PeriodWebService:
    """Web service methods for GL fiscal periods and trial balance."""

    # =========================================================================
    # Context Methods
    # =========================================================================

    @staticmethod
    def periods_context(
        db: Session,
        organization_id: str,
        year_id: str | None = None,
    ) -> dict:
        """Get context for fiscal periods listing page."""
        logger.debug("periods_context: org=%s year_id=%s", organization_id, year_id)
        org_id = coerce_uuid(organization_id)

        # Get fiscal years
        years = db.scalars(
            select(FiscalYear)
            .where(FiscalYear.organization_id == org_id)
            .order_by(FiscalYear.year_code.desc())
        )
        years = years.all()

        if not years:
            return {
                "years": [],
                "selected_year": None,
                "periods": [],
            }

        # Select year
        selected_year = None
        if year_id:
            selected_year = db.get(FiscalYear, coerce_uuid(year_id))
            if selected_year and selected_year.organization_id != org_id:
                selected_year = None

        if not selected_year:
            selected_year = years[0]

        # Get periods for selected year
        periods = db.scalars(
            select(FiscalPeriod)
            .where(FiscalPeriod.fiscal_year_id == selected_year.fiscal_year_id)
            .order_by(FiscalPeriod.period_number)
        )
        periods = periods.all()

        return {
            "years": [fiscal_year_option_view(y) for y in years],
            "selected_year": fiscal_year_option_view(selected_year),
            "periods": [period_option_view(p) for p in periods],
        }

    @staticmethod
    def period_form_context(
        db: Session,
        organization_id: str,
        period_id: str | None = None,
    ) -> dict:
        """Get context for period create/edit form."""
        logger.debug(
            "period_form_context: org=%s period_id=%s", organization_id, period_id
        )
        org_id = coerce_uuid(organization_id)

        period = None
        if period_id:
            period = db.get(FiscalPeriod, coerce_uuid(period_id))
            if period and period.organization_id != org_id:
                period = None

        years = db.scalars(
            select(FiscalYear)
            .where(FiscalYear.organization_id == org_id)
            .order_by(FiscalYear.year_code.desc())
        )
        years = years.all()

        return {
            "period": period_option_view(period) if period else None,
            "years": [fiscal_year_option_view(y) for y in years],
            "statuses": [s.value for s in PeriodStatus],
        }

    @staticmethod
    def trial_balance_context(
        db: Session,
        organization_id: str,
        as_of_date: str | None = None,
    ) -> dict:
        """Get context for trial balance report."""
        logger.debug(
            "trial_balance_context: org=%s as_of_date=%s", organization_id, as_of_date
        )
        org_id = coerce_uuid(organization_id)
        ref_date = parse_date(as_of_date) or date.today()

        # Find period for the reference date
        period = db.scalars(
            select(FiscalPeriod)
            .where(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= ref_date,
                FiscalPeriod.end_date >= ref_date,
            )
            .order_by(FiscalPeriod.start_date.desc())
        )
        period = period.first()

        # If no period for the date, get the latest period
        if not period:
            period = db.scalars(
                select(FiscalPeriod)
                .where(FiscalPeriod.organization_id == org_id)
                .order_by(FiscalPeriod.end_date.desc())
            )
            period = period.first()

        balances = []
        total_debit = Decimal("0")
        total_credit = Decimal("0")

        if period:
            rows = db.execute(
                select(AccountBalance, Account, AccountCategory)
                .join(Account, AccountBalance.account_id == Account.account_id)
                .join(
                    AccountCategory, Account.category_id == AccountCategory.category_id
                )
                .where(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.fiscal_period_id == period.fiscal_period_id,
                    AccountBalance.balance_type == BalanceType.ACTUAL,
                )
                .order_by(Account.account_code)
            )
            rows = rows.all()

            for balance, account, category in rows:
                debit = balance.closing_debit or Decimal("0")
                credit = balance.closing_credit or Decimal("0")
                total_debit += debit
                total_credit += credit
                balances.append(
                    {
                        "account_id": str(account.account_id),
                        "account_code": account.account_code,
                        "account_name": account.account_name,
                        "category": ifrs_label(category.ifrs_category),
                        "debit": format_currency(debit, balance.currency_code),
                        "credit": format_currency(credit, balance.currency_code),
                    }
                )

        logger.debug("trial_balance_context: found %d account balances", len(balances))

        zero_value = f"{settings.default_presentation_currency_code} 0.00"
        active_filters = build_active_filters(
            params={"as_of_date": as_of_date},
            labels={"as_of_date": "As Of"},
        )

        return {
            "balances": balances,
            "as_of_date": as_of_date or format_date(ref_date),
            "total_debit": format_currency(total_debit) or zero_value,
            "total_credit": format_currency(total_credit) or zero_value,
            "active_filters": active_filters,
        }

    # =========================================================================
    # Business Logic Methods
    # =========================================================================

    @staticmethod
    def close_period(
        db: Session,
        organization_id: str,
        period_id: str,
        closed_by_user_id: str,
    ) -> str | None:
        """Close a fiscal period. Returns error message or None on success."""
        logger.debug("close_period: org=%s period_id=%s", organization_id, period_id)
        org_id = coerce_uuid(organization_id)
        per_id = coerce_uuid(period_id)
        user_id = coerce_uuid(closed_by_user_id)

        try:
            fiscal_period_service.hard_close_period(
                db=db,
                organization_id=org_id,
                fiscal_period_id=per_id,
                closed_by_user_id=user_id,
            )
            logger.info("close_period: closed period %s for org %s", per_id, org_id)
            return None
        except Exception as e:
            logger.exception("close_period: failed for org %s", org_id)
            return str(e)

    # =========================================================================
    # Response Methods
    # =========================================================================

    def period_close_response(
        self,
        request: Request,
        auth: WebAuthContext,
    ) -> HTMLResponse:
        """Render period close checklist page."""
        context = base_context(request, auth, "Period Close", "gl")
        return templates.TemplateResponse(
            request, "finance/gl/period_close.html", context
        )

    def list_periods_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        year_id: str | None = None,
    ) -> HTMLResponse:
        """Render fiscal periods list page."""
        context = base_context(request, auth, "Fiscal Periods", "gl")
        context.update(
            self.periods_context(
                db,
                str(auth.organization_id),
                year_id=year_id,
            )
        )
        return templates.TemplateResponse(request, "finance/gl/periods.html", context)

    def new_period_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        error_message: str | None = None,
        form_data: dict | None = None,
    ) -> HTMLResponse:
        """Render new fiscal period form page."""
        context = base_context(request, auth, "New Fiscal Period", "gl")
        context.update(self.period_form_context(db, str(auth.organization_id)))
        context["error_message"] = error_message
        context["form_data"] = form_data or {}
        return templates.TemplateResponse(
            request, "finance/gl/period_form.html", context
        )

    def create_period_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        fiscal_year_id: str,
        period_number: int,
        period_name: str,
        start_date: str,
        end_date: str,
        is_adjustment_period: bool,
        is_closing_period: bool,
    ) -> HTMLResponse | RedirectResponse:
        """Create a fiscal period from the web form."""
        form_data = {
            "fiscal_year_id": fiscal_year_id,
            "period_number": period_number,
            "period_name": period_name,
            "start_date": start_date,
            "end_date": end_date,
            "is_adjustment_period": is_adjustment_period,
            "is_closing_period": is_closing_period,
        }

        if not period_name.strip():
            return self.new_period_form_response(
                request,
                auth,
                db,
                error_message="Period name is required",
                form_data=form_data,
            )

        start = parse_date(start_date)
        end = parse_date(end_date)
        if not start or not end:
            return self.new_period_form_response(
                request,
                auth,
                db,
                error_message="Start date and end date are required",
                form_data=form_data,
            )
        if end < start:
            return self.new_period_form_response(
                request,
                auth,
                db,
                error_message="End date must be on or after start date",
                form_data=form_data,
            )

        org_id = coerce_uuid(auth.organization_id)
        year = db.get(FiscalYear, coerce_uuid(fiscal_year_id))
        if not year or year.organization_id != org_id:
            return self.new_period_form_response(
                request,
                auth,
                db,
                error_message="Fiscal year not found",
                form_data=form_data,
            )

        if start < year.start_date or end > year.end_date:
            return self.new_period_form_response(
                request,
                auth,
                db,
                error_message=(
                    "Period dates must be within the selected fiscal year range"
                ),
                form_data=form_data,
            )

        try:
            fiscal_period_service.create_period(
                db,
                org_id,
                FiscalPeriodInput(
                    fiscal_year_id=year.fiscal_year_id,
                    period_number=period_number,
                    period_name=period_name.strip(),
                    start_date=start,
                    end_date=end,
                    is_adjustment_period=is_adjustment_period,
                    is_closing_period=is_closing_period,
                ),
            )
        except HTTPException as exc:
            return self.new_period_form_response(
                request,
                auth,
                db,
                error_message=str(exc.detail),
                form_data=form_data,
            )
        except Exception as exc:
            logger.exception("create_period_response: failed to create period")
            return self.new_period_form_response(
                request,
                auth,
                db,
                error_message=str(exc),
                form_data=form_data,
            )

        return RedirectResponse(url="/finance/gl/periods?saved=1", status_code=303)

    def open_period_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        period_id: str,
        year_id: str | None = None,
    ) -> RedirectResponse:
        """Open a fiscal period and redirect back to period list."""
        if not auth.user_id:
            return RedirectResponse(
                url="/finance/gl/periods?error=User+is+not+associated+with+a+person",
                status_code=303,
            )

        try:
            fiscal_period_service.open_period(
                db=db,
                organization_id=coerce_uuid(auth.organization_id),
                fiscal_period_id=coerce_uuid(period_id),
                opened_by_user_id=coerce_uuid(auth.user_id),
            )
        except HTTPException as exc:
            msg = str(exc.detail).replace(" ", "+")
            if year_id:
                return RedirectResponse(
                    url=f"/finance/gl/periods?year_id={year_id}&error={msg}",
                    status_code=303,
                )
            return RedirectResponse(
                url=f"/finance/gl/periods?error={msg}",
                status_code=303,
            )

        if year_id:
            return RedirectResponse(
                url=f"/finance/gl/periods?year_id={year_id}&saved=1", status_code=303
            )
        return RedirectResponse(url="/finance/gl/periods?saved=1", status_code=303)

    def close_period_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        period_id: str,
        year_id: str | None = None,
    ) -> RedirectResponse:
        """Soft-close a fiscal period and redirect back to period list."""
        if not auth.user_id:
            return RedirectResponse(
                url="/finance/gl/periods?error=User+is+not+associated+with+a+person",
                status_code=303,
            )

        try:
            fiscal_period_service.close_period(
                db=db,
                organization_id=coerce_uuid(auth.organization_id),
                fiscal_period_id=coerce_uuid(period_id),
                closed_by_user_id=coerce_uuid(auth.user_id),
            )
        except HTTPException as exc:
            msg = str(exc.detail).replace(" ", "+")
            if year_id:
                return RedirectResponse(
                    url=f"/finance/gl/periods?year_id={year_id}&error={msg}",
                    status_code=303,
                )
            return RedirectResponse(
                url=f"/finance/gl/periods?error={msg}",
                status_code=303,
            )

        if year_id:
            return RedirectResponse(
                url=f"/finance/gl/periods?year_id={year_id}&saved=1", status_code=303
            )
        return RedirectResponse(url="/finance/gl/periods?saved=1", status_code=303)

    def trial_balance_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        as_of_date: str | None,
    ) -> HTMLResponse:
        """Render trial balance report page."""
        context = base_context(request, auth, "Trial Balance", "gl")
        context.update(
            self.trial_balance_context(
                db,
                str(auth.organization_id),
                as_of_date=as_of_date,
            )
        )
        return templates.TemplateResponse(
            request, "finance/gl/trial_balance.html", context
        )
