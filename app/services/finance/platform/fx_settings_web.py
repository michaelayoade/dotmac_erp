"""
FX Settings Web Service — template context and form handling for the
Exchange Rates settings page.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance.core_fx.currency import Currency
from app.models.finance.core_fx.exchange_rate import ExchangeRate, ExchangeRateSource
from app.models.finance.core_fx.exchange_rate_type import ExchangeRateType
from app.models.finance.core_org.organization import Organization
from app.services.finance.platform.ecb_rate_fetcher import (
    ExchangeRateFetcher,
    FetchResult,
)

logger = logging.getLogger(__name__)


class FXSettingsWebService:
    """Web-layer helpers for exchange-rate management UI."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # List context
    # ------------------------------------------------------------------

    def rates_list_context(
        self,
        organization_id: UUID,
        *,
        search: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """Return rates, currencies, rate_types for the template."""
        stmt = (
            select(ExchangeRate)
            .where(ExchangeRate.organization_id == organization_id)
            .order_by(
                ExchangeRate.effective_date.desc(), ExchangeRate.from_currency_code
            )
        )

        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                ExchangeRate.from_currency_code.ilike(pattern)
                | ExchangeRate.to_currency_code.ilike(pattern)
            )

        # Total count for pagination
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = self.db.scalar(count_stmt) or 0

        rates = list(self.db.scalars(stmt.offset(offset).limit(limit)).all())

        # Supporting data
        currencies = list(
            self.db.scalars(
                select(Currency)
                .where(Currency.is_active.is_(True))
                .order_by(Currency.currency_code)
            ).all()
        )
        rate_types = list(
            self.db.scalars(
                select(ExchangeRateType)
                .where(ExchangeRateType.organization_id == organization_id)
                .order_by(ExchangeRateType.type_code)
            ).all()
        )

        org = self.db.get(Organization, organization_id)
        functional_currency = org.functional_currency_code if org else "NGN"

        return {
            "rates": rates,
            "currencies": currencies,
            "rate_types": rate_types,
            "functional_currency": functional_currency,
            "total": total,
            "offset": offset,
            "limit": limit,
            "search": search or "",
        }

    # ------------------------------------------------------------------
    # Create manual rate
    # ------------------------------------------------------------------

    def create_manual_rate(
        self,
        organization_id: UUID,
        form_data: dict,
        user_id: UUID,
    ) -> tuple[bool, str | None]:
        """Validate and create a manual exchange rate.

        Returns:
            (success, error_message)
        """
        from_code = (form_data.get("from_currency_code") or "").strip().upper()
        to_code = (form_data.get("to_currency_code") or "").strip().upper()
        rate_str = (form_data.get("exchange_rate") or "").strip()
        date_str = (form_data.get("effective_date") or "").strip()
        rate_type_id_str = (form_data.get("rate_type_id") or "").strip()

        # Validation
        if not from_code or not to_code:
            return False, "Both currency codes are required."
        if from_code == to_code:
            return False, "From and To currencies must be different."

        try:
            rate_value = Decimal(rate_str)
        except (InvalidOperation, ValueError, TypeError):
            return False, "Invalid exchange rate value."
        if rate_value <= 0:
            return False, "Exchange rate must be positive."

        try:
            effective_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return False, "Invalid date format (expected YYYY-MM-DD)."

        # Ensure rate type exists; fall back to SPOT
        rate_type_id: UUID | None = None
        if rate_type_id_str:
            try:
                rate_type_id = UUID(rate_type_id_str)
            except ValueError:
                return False, "Invalid rate type."
        if rate_type_id is None:
            fetcher = ExchangeRateFetcher()
            spot = fetcher._get_or_create_spot_type(self.db, organization_id)
            rate_type_id = spot.rate_type_id

        # Check for existing rate on same date/pair/type
        existing = self.db.scalar(
            select(ExchangeRate).where(
                ExchangeRate.organization_id == organization_id,
                ExchangeRate.from_currency_code == from_code,
                ExchangeRate.to_currency_code == to_code,
                ExchangeRate.rate_type_id == rate_type_id,
                ExchangeRate.effective_date == effective_date,
            )
        )
        if existing:
            existing.exchange_rate = rate_value
            existing.source = ExchangeRateSource.MANUAL
            existing.created_by_user_id = user_id
            self.db.flush()
            return True, None

        new_rate = ExchangeRate(
            organization_id=organization_id,
            from_currency_code=from_code,
            to_currency_code=to_code,
            rate_type_id=rate_type_id,
            effective_date=effective_date,
            exchange_rate=rate_value,
            source=ExchangeRateSource.MANUAL,
            created_by_user_id=user_id,
        )
        self.db.add(new_rate)
        self.db.flush()
        return True, None

    # ------------------------------------------------------------------
    # Delete rate
    # ------------------------------------------------------------------

    def delete_rate(
        self,
        organization_id: UUID,
        rate_id: UUID,
    ) -> tuple[bool, str | None]:
        """Delete an exchange rate (must belong to the org).

        Returns:
            (success, error_message)
        """
        rate = self.db.scalar(
            select(ExchangeRate).where(
                ExchangeRate.exchange_rate_id == rate_id,
                ExchangeRate.organization_id == organization_id,
            )
        )
        if rate is None:
            return False, "Exchange rate not found."

        self.db.delete(rate)
        self.db.flush()
        return True, None

    # ------------------------------------------------------------------
    # Fetch from API
    # ------------------------------------------------------------------

    def fetch_latest(
        self,
        organization_id: UUID,
        user_id: UUID,
    ) -> FetchResult:
        """Trigger rate fetch from Currency API."""
        fetcher = ExchangeRateFetcher()
        return fetcher.fetch_latest_rates(self.db, organization_id, user_id=user_id)
