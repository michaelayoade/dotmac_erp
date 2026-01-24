"""
GL Period Web Service - Fiscal period and trial balance web view methods.

Provides view-focused data and operations for GL fiscal period web routes.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.finance.gl.account import Account
from app.models.finance.gl.account_balance import AccountBalance, BalanceType
from app.models.finance.gl.account_category import AccountCategory
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.fiscal_year import FiscalYear
from app.services.common import coerce_uuid
from app.services.finance.platform.org_context import org_context_service
from app.templates import templates
from app.web.deps import base_context, WebAuthContext
from app.services.finance.gl.web.base import (
    format_currency,
    format_date,
    fiscal_year_option_view,
    ifrs_label,
    parse_date,
    period_option_view,
    TrialBalanceTotals,
)

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
        year_id: Optional[str] = None,
    ) -> dict:
        """Get context for fiscal periods listing page."""
        logger.debug(
            "periods_context: org=%s year_id=%s",
            organization_id, year_id
        )
        org_id = coerce_uuid(organization_id)

        # Get fiscal years
        years = (
            db.query(FiscalYear)
            .filter(FiscalYear.organization_id == org_id)
            .order_by(FiscalYear.year_code.desc())
            .all()
        )

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
        periods = (
            db.query(FiscalPeriod)
            .filter(FiscalPeriod.fiscal_year_id == selected_year.fiscal_year_id)
            .order_by(FiscalPeriod.period_number)
            .all()
        )

        return {
            "years": [fiscal_year_option_view(y) for y in years],
            "selected_year": fiscal_year_option_view(selected_year),
            "periods": [period_option_view(p) for p in periods],
        }

    @staticmethod
    def period_form_context(
        db: Session,
        organization_id: str,
        period_id: Optional[str] = None,
    ) -> dict:
        """Get context for period create/edit form."""
        logger.debug(
            "period_form_context: org=%s period_id=%s",
            organization_id, period_id
        )
        org_id = coerce_uuid(organization_id)

        period = None
        if period_id:
            period = db.get(FiscalPeriod, coerce_uuid(period_id))
            if period and period.organization_id != org_id:
                period = None

        years = (
            db.query(FiscalYear)
            .filter(FiscalYear.organization_id == org_id)
            .order_by(FiscalYear.year_code.desc())
            .all()
        )

        return {
            "period": period_option_view(period) if period else None,
            "years": [fiscal_year_option_view(y) for y in years],
            "statuses": [s.value for s in PeriodStatus],
        }

    @staticmethod
    def trial_balance_context(
        db: Session,
        organization_id: str,
        as_of_date: Optional[str] = None,
    ) -> dict:
        """Get context for trial balance report."""
        logger.debug(
            "trial_balance_context: org=%s as_of_date=%s",
            organization_id, as_of_date
        )
        org_id = coerce_uuid(organization_id)
        ref_date = parse_date(as_of_date) or date.today()

        # Find period for the reference date
        period = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.organization_id == org_id,
                FiscalPeriod.start_date <= ref_date,
                FiscalPeriod.end_date >= ref_date,
            )
            .order_by(FiscalPeriod.start_date.desc())
            .first()
        )

        # If no period for the date, get the latest period
        if not period:
            period = (
                db.query(FiscalPeriod)
                .filter(FiscalPeriod.organization_id == org_id)
                .order_by(FiscalPeriod.end_date.desc())
                .first()
            )

        balances = []
        total_debit = Decimal("0")
        total_credit = Decimal("0")

        if period:
            rows = (
                db.query(AccountBalance, Account, AccountCategory)
                .join(Account, AccountBalance.account_id == Account.account_id)
                .join(AccountCategory, Account.category_id == AccountCategory.category_id)
                .filter(
                    AccountBalance.organization_id == org_id,
                    AccountBalance.fiscal_period_id == period.fiscal_period_id,
                    AccountBalance.balance_type == BalanceType.ACTUAL,
                )
                .order_by(Account.account_code)
                .all()
            )

            for balance, account, category in rows:
                debit = balance.closing_debit or Decimal("0")
                credit = balance.closing_credit or Decimal("0")
                total_debit += debit
                total_credit += credit
                balances.append(
                    {
                        "account_code": account.account_code,
                        "account_name": account.account_name,
                        "category": ifrs_label(category.ifrs_category),
                        "debit": format_currency(debit, balance.currency_code),
                        "credit": format_currency(credit, balance.currency_code),
                    }
                )

        logger.debug("trial_balance_context: found %d account balances", len(balances))

        zero_value = f"{settings.default_presentation_currency_code} 0.00"

        return {
            "balances": balances,
            "as_of_date": as_of_date or format_date(ref_date),
            "total_debit": format_currency(total_debit) or zero_value,
            "total_credit": format_currency(total_credit) or zero_value,
        }

    # =========================================================================
    # Business Logic Methods
    # =========================================================================

    @staticmethod
    def close_period(
        db: Session,
        organization_id: str,
        period_id: str,
    ) -> Optional[str]:
        """Close a fiscal period. Returns error message or None on success."""
        logger.debug(
            "close_period: org=%s period_id=%s",
            organization_id, period_id
        )
        org_id = coerce_uuid(organization_id)
        per_id = coerce_uuid(period_id)

        period = db.get(FiscalPeriod, per_id)
        if not period or period.organization_id != org_id:
            return "Period not found"

        if period.status == PeriodStatus.HARD_CLOSED:
            return "Period is already closed"

        try:
            period.status = PeriodStatus.HARD_CLOSED
            db.commit()
            logger.info("close_period: closed period %s for org %s", per_id, org_id)
            return None

        except Exception as e:
            db.rollback()
            logger.exception("close_period: failed for org %s", org_id)
            return f"Failed to close period: {str(e)}"

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
        return templates.TemplateResponse(request, "finance/gl/period_close.html", context)

    def list_periods_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render fiscal periods list page."""
        context = base_context(request, auth, "Fiscal Periods", "gl")
        context.update(self.periods_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(request, "finance/gl/periods.html", context)

    def new_period_form_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
    ) -> HTMLResponse:
        """Render new fiscal period form page."""
        context = base_context(request, auth, "New Fiscal Year", "gl")
        context.update(self.period_form_context(db, str(auth.organization_id)))
        return templates.TemplateResponse(request, "finance/gl/period_form.html", context)

    def trial_balance_response(
        self,
        request: Request,
        auth: WebAuthContext,
        db: Session,
        as_of_date: Optional[str],
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
        return templates.TemplateResponse(request, "finance/gl/trial_balance.html", context)
