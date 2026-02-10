"""
Exchange Rate Fetcher — pulls daily rates from the free Currency API.

API: https://github.com/fawazahmed0/exchange-api
- No auth required, no rate limits, updated daily
- Supports 300+ currencies including NGN
- Primary endpoint on Cloudflare Pages, fallback on jsDelivr CDN
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.core_fx.currency import Currency
from app.models.finance.core_fx.exchange_rate import ExchangeRate, ExchangeRateSource
from app.models.finance.core_fx.exchange_rate_type import ExchangeRateType
from app.models.finance.core_org.organization import Organization

logger = logging.getLogger(__name__)

API_URL = "https://latest.currency-api.pages.dev/v1/currencies/{base}.json"
FALLBACK_URL = (
    "https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest"
    "/v1/currencies/{base}.json"
)
_HTTP_TIMEOUT = 15.0


@dataclass(frozen=True)
class FetchResult:
    """Immutable result of a rate-fetch operation."""

    rates_created: int = 0
    rates_updated: int = 0
    rates_skipped: int = 0
    errors: list[str] = field(default_factory=list)


class ExchangeRateFetcher:
    """Fetches daily exchange rates from the free Currency API."""

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def fetch_latest_rates(
        self,
        db: Session,
        organization_id: UUID,
        *,
        user_id: UUID | None = None,
    ) -> FetchResult:
        """Fetch rates for all active currencies against the org's functional currency.

        Returns:
            FetchResult with counts of created/updated/skipped rates and any errors.
        """
        org = db.get(Organization, organization_id)
        if org is None:
            return FetchResult(errors=["Organization not found"])

        base_code = org.functional_currency_code.lower()

        # Fetch JSON from primary, fall back to CDN
        data = self._fetch_json(base_code)
        if data is None:
            return FetchResult(errors=["Failed to fetch rates from API (both URLs)"])

        rate_date_str: str = data.get("date", "")
        try:
            effective_date = datetime.strptime(rate_date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            effective_date = date.today()

        rates_map: dict[str, float] = data.get(base_code, {})
        if not rates_map:
            return FetchResult(errors=[f"No rates returned for base={base_code}"])

        # Get active currencies from DB
        active_codes = set(
            db.scalars(
                select(Currency.currency_code).where(Currency.is_active.is_(True))
            ).all()
        )

        spot_type = self._get_or_create_spot_type(db, organization_id)

        created = 0
        updated = 0
        skipped = 0
        errors: list[str] = []

        for code_lower, rate_value in rates_map.items():
            code_upper = code_lower.upper()
            if code_upper not in active_codes:
                continue
            if code_upper == org.functional_currency_code:
                continue  # skip self-pair

            try:
                rate_decimal = Decimal(str(rate_value))
            except (InvalidOperation, ValueError, TypeError):
                errors.append(f"Invalid rate value for {code_upper}: {rate_value}")
                continue

            if rate_decimal <= 0:
                continue

            action = self._store_rate(
                db,
                organization_id=organization_id,
                from_currency_code=org.functional_currency_code,
                to_currency_code=code_upper,
                rate_type_id=spot_type.rate_type_id,
                effective_date=effective_date,
                rate=rate_decimal,
                source=ExchangeRateSource.API,
                user_id=user_id,
            )
            if action == "created":
                created += 1
            elif action == "updated":
                updated += 1
            else:
                skipped += 1

        db.flush()
        logger.info(
            "FX fetch for org %s: %d created, %d updated, %d skipped, %d errors",
            organization_id,
            created,
            updated,
            skipped,
            len(errors),
        )
        return FetchResult(
            rates_created=created,
            rates_updated=updated,
            rates_skipped=skipped,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_json(self, base_code: str) -> dict | None:
        """GET the rate JSON, trying primary then fallback URL."""
        for url_template in (API_URL, FALLBACK_URL):
            url = url_template.format(base=base_code)
            try:
                resp = httpx.get(url, timeout=_HTTP_TIMEOUT, follow_redirects=True)
                resp.raise_for_status()
                data: dict = resp.json()
                return data
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("Rate fetch failed for %s: %s", url, exc)
        return None

    def _get_or_create_spot_type(
        self, db: Session, organization_id: UUID
    ) -> ExchangeRateType:
        """Ensure a SPOT rate type exists for the org."""
        stmt = select(ExchangeRateType).where(
            ExchangeRateType.organization_id == organization_id,
            ExchangeRateType.type_code == "SPOT",
        )
        spot = db.scalar(stmt)
        if spot is not None:
            return spot

        spot = ExchangeRateType(
            organization_id=organization_id,
            type_code="SPOT",
            type_name="Spot Rate",
            description="Market spot rate (auto-fetched daily)",
            is_default=True,
        )
        db.add(spot)
        db.flush()
        logger.info("Created SPOT rate type for org %s", organization_id)
        return spot

    def _store_rate(
        self,
        db: Session,
        *,
        organization_id: UUID,
        from_currency_code: str,
        to_currency_code: str,
        rate_type_id: UUID,
        effective_date: date,
        rate: Decimal,
        source: ExchangeRateSource,
        user_id: UUID | None,
    ) -> str:
        """Store a single rate — returns 'created', 'updated', or 'skipped'."""
        stmt = select(ExchangeRate).where(
            ExchangeRate.organization_id == organization_id,
            ExchangeRate.from_currency_code == from_currency_code,
            ExchangeRate.to_currency_code == to_currency_code,
            ExchangeRate.rate_type_id == rate_type_id,
            ExchangeRate.effective_date == effective_date,
        )
        existing = db.scalar(stmt)

        if existing is not None:
            if existing.exchange_rate == rate:
                return "skipped"
            existing.exchange_rate = rate
            existing.source = source
            return "updated"

        new_rate = ExchangeRate(
            organization_id=organization_id,
            from_currency_code=from_currency_code,
            to_currency_code=to_currency_code,
            rate_type_id=rate_type_id,
            effective_date=effective_date,
            exchange_rate=rate,
            source=source,
            created_by_user_id=user_id,
        )
        db.add(new_rate)
        return "created"
