"""
FXService - Foreign exchange rate management and currency conversion.

Provides rate resolution, currency conversion, and functional currency
conversion capabilities for multi-currency accounting operations.
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.finance.core_fx.exchange_rate import ExchangeRate
from app.models.finance.core_fx.exchange_rate_type import ExchangeRateType
from app.models.finance.core_org.organization import Organization
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class ConversionResult:
    """Result of a currency conversion."""

    original_amount: Decimal
    original_currency: str
    converted_amount: Decimal
    target_currency: str
    exchange_rate: Decimal
    rate_date: date
    rate_type: str


class FXService(ListResponseMixin):
    """
    Service for foreign exchange rate management and currency conversion.

    Supports multiple rate types (SPOT, AVERAGE, CLOSING) and automatic
    conversion to functional currency.
    """

    @staticmethod
    def get_rate(
        db: Session,
        organization_id: UUID,
        from_currency: str,
        to_currency: str,
        rate_type_code: str,
        effective_date: date,
    ) -> ExchangeRate:
        """
        Get exchange rate for a currency pair on a specific date.

        Uses the most recent rate on or before the effective date.

        Args:
            db: Database session
            organization_id: Organization scope
            from_currency: Source currency code (ISO 4217)
            to_currency: Target currency code (ISO 4217)
            rate_type_code: Rate type (SPOT, AVERAGE, CLOSING, etc.)
            effective_date: Date for rate lookup

        Returns:
            ExchangeRate record

        Raises:
            HTTPException(404): If no rate found
        """
        org_id = coerce_uuid(organization_id)

        # Same currency - no conversion needed
        if from_currency == to_currency:
            # Return a synthetic rate of 1.0
            rate = ExchangeRate(
                organization_id=org_id,
                from_currency_code=from_currency,
                to_currency_code=to_currency,
                exchange_rate=Decimal("1.0"),
                effective_date=effective_date,
            )
            return rate

        # Get the rate type
        rate_type = (
            db.query(ExchangeRateType)
            .filter(
                and_(
                    ExchangeRateType.organization_id == org_id,
                    ExchangeRateType.type_code == rate_type_code,
                )
            )
            .first()
        )

        if not rate_type:
            raise HTTPException(
                status_code=404,
                detail=f"Exchange rate type '{rate_type_code}' not found",
            )

        # Try direct rate first
        direct_rate = (
            db.query(ExchangeRate)
            .filter(
                and_(
                    ExchangeRate.organization_id == org_id,
                    ExchangeRate.from_currency_code == from_currency,
                    ExchangeRate.to_currency_code == to_currency,
                    ExchangeRate.rate_type_id == rate_type.rate_type_id,
                    ExchangeRate.effective_date <= effective_date,
                )
            )
            .order_by(ExchangeRate.effective_date.desc())
            .first()
        )

        if direct_rate:
            return direct_rate

        # Try inverse rate
        inverse_rate = (
            db.query(ExchangeRate)
            .filter(
                and_(
                    ExchangeRate.organization_id == org_id,
                    ExchangeRate.from_currency_code == to_currency,
                    ExchangeRate.to_currency_code == from_currency,
                    ExchangeRate.rate_type_id == rate_type.rate_type_id,
                    ExchangeRate.effective_date <= effective_date,
                )
            )
            .order_by(ExchangeRate.effective_date.desc())
            .first()
        )

        if inverse_rate:
            # Create synthetic rate from inverse
            rate = ExchangeRate(
                organization_id=org_id,
                from_currency_code=from_currency,
                to_currency_code=to_currency,
                rate_type_id=rate_type.rate_type_id,
                exchange_rate=inverse_rate.inverse_rate,
                effective_date=inverse_rate.effective_date,
            )
            return rate

        raise HTTPException(
            status_code=404,
            detail=f"No exchange rate found for {from_currency}/{to_currency} "
            f"({rate_type_code}) on or before {effective_date}",
        )

    @staticmethod
    def convert(
        db: Session,
        organization_id: UUID,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        rate_type_code: str,
        effective_date: date,
    ) -> ConversionResult:
        """
        Convert an amount between currencies.

        Args:
            db: Database session
            organization_id: Organization scope
            amount: Amount to convert
            from_currency: Source currency code
            to_currency: Target currency code
            rate_type_code: Rate type to use
            effective_date: Date for rate lookup

        Returns:
            ConversionResult with converted amount and rate details

        Raises:
            HTTPException(404): If no rate found
        """
        # Same currency - no conversion needed
        if from_currency == to_currency:
            return ConversionResult(
                original_amount=amount,
                original_currency=from_currency,
                converted_amount=amount,
                target_currency=to_currency,
                exchange_rate=Decimal("1.0"),
                rate_date=effective_date,
                rate_type=rate_type_code,
            )

        rate = FXService.get_rate(
            db,
            organization_id,
            from_currency,
            to_currency,
            rate_type_code,
            effective_date,
        )

        converted_amount = amount * rate.exchange_rate

        return ConversionResult(
            original_amount=amount,
            original_currency=from_currency,
            converted_amount=converted_amount.quantize(Decimal("0.000001")),
            target_currency=to_currency,
            exchange_rate=rate.exchange_rate,
            rate_date=rate.effective_date,
            rate_type=rate_type_code,
        )

    @staticmethod
    def convert_to_functional(
        db: Session,
        organization_id: UUID,
        amount: Decimal,
        currency_code: str,
        effective_date: date,
        rate_type_code: str = "SPOT",
    ) -> ConversionResult:
        """
        Convert an amount to the organization's functional currency.

        Args:
            db: Database session
            organization_id: Organization scope
            amount: Amount to convert
            currency_code: Source currency code
            effective_date: Date for rate lookup
            rate_type_code: Rate type (default: SPOT)

        Returns:
            ConversionResult with functional currency amount

        Raises:
            HTTPException(404): If organization or rate not found
        """
        org_id = coerce_uuid(organization_id)

        # Get organization's functional currency
        org = db.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        functional_currency = org.functional_currency_code

        return FXService.convert(
            db,
            organization_id,
            amount,
            currency_code,
            functional_currency,
            rate_type_code,
            effective_date,
        )

    @staticmethod
    def batch_convert(
        db: Session,
        organization_id: UUID,
        conversions: list[dict],
        rate_type_code: str = "SPOT",
    ) -> list[ConversionResult]:
        """
        Convert multiple amounts in a single call.

        Each conversion dict should contain:
        - amount: Decimal
        - from_currency: str
        - to_currency: str
        - effective_date: date

        Args:
            db: Database session
            organization_id: Organization scope
            conversions: List of conversion requests
            rate_type_code: Rate type to use (default: SPOT)

        Returns:
            List of ConversionResult objects

        Raises:
            HTTPException(404): If any rate not found
        """
        results = []

        for conv in conversions:
            result = FXService.convert(
                db,
                organization_id,
                conv["amount"],
                conv["from_currency"],
                conv["to_currency"],
                rate_type_code,
                conv["effective_date"],
            )
            results.append(result)

        return results

    @staticmethod
    def get_default_rate_type(
        db: Session,
        organization_id: UUID,
    ) -> ExchangeRateType:
        """
        Get the default exchange rate type for an organization.

        Args:
            db: Database session
            organization_id: Organization scope

        Returns:
            Default ExchangeRateType

        Raises:
            HTTPException(404): If no default rate type configured
        """
        org_id = coerce_uuid(organization_id)

        rate_type = (
            db.query(ExchangeRateType)
            .filter(
                and_(
                    ExchangeRateType.organization_id == org_id,
                    ExchangeRateType.is_default == True,  # noqa: E712
                )
            )
            .first()
        )

        if not rate_type:
            raise HTTPException(
                status_code=404,
                detail="No default exchange rate type configured",
            )

        return rate_type

    @staticmethod
    def get_functional_currency(
        db: Session,
        organization_id: UUID,
    ) -> str:
        """
        Get the functional currency for an organization.

        Args:
            db: Database session
            organization_id: Organization scope

        Returns:
            Functional currency code (ISO 4217)

        Raises:
            HTTPException(404): If organization not found
        """
        org_id = coerce_uuid(organization_id)

        org = db.get(Organization, org_id)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        return org.functional_currency_code

    @staticmethod
    def create_rate(
        db: Session,
        organization_id: UUID,
        from_currency: str,
        to_currency: str,
        rate_type_code: str,
        effective_date: date,
        exchange_rate: Decimal,
        source: str | None = None,
        created_by_user_id: UUID | None = None,
    ) -> ExchangeRate:
        """
        Create a new exchange rate.

        Args:
            db: Database session
            organization_id: Organization scope
            from_currency: Source currency code
            to_currency: Target currency code
            rate_type_code: Rate type code
            effective_date: Effective date
            exchange_rate: The exchange rate
            source: Optional rate source (MANUAL, ECB, etc.)
            created_by_user_id: User creating the rate

        Returns:
            Created ExchangeRate

        Raises:
            HTTPException(404): If rate type not found
            HTTPException(400): If invalid rate
        """
        org_id = coerce_uuid(organization_id)

        if exchange_rate <= 0:
            raise HTTPException(
                status_code=400,
                detail="Exchange rate must be positive",
            )

        # Get the rate type
        rate_type = (
            db.query(ExchangeRateType)
            .filter(
                and_(
                    ExchangeRateType.organization_id == org_id,
                    ExchangeRateType.type_code == rate_type_code,
                )
            )
            .first()
        )

        if not rate_type:
            raise HTTPException(
                status_code=404,
                detail=f"Exchange rate type '{rate_type_code}' not found",
            )

        rate = ExchangeRate(
            organization_id=org_id,
            from_currency_code=from_currency,
            to_currency_code=to_currency,
            rate_type_id=rate_type.rate_type_id,
            effective_date=effective_date,
            exchange_rate=exchange_rate,
            source=source,
            created_by_user_id=coerce_uuid(created_by_user_id)
            if created_by_user_id
            else None,
        )

        db.add(rate)
        db.commit()
        db.refresh(rate)
        return rate

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        from_currency: str | None = None,
        to_currency: str | None = None,
        rate_type_code: str | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExchangeRate]:
        """
        List exchange rates.

        Args:
            db: Database session
            organization_id: Filter by organization
            from_currency: Filter by source currency
            to_currency: Filter by target currency
            rate_type_code: Filter by rate type
            from_date: Filter by start date
            to_date: Filter by end date
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of ExchangeRate objects
        """
        query = db.query(ExchangeRate)

        if organization_id:
            query = query.filter(
                ExchangeRate.organization_id == coerce_uuid(organization_id)
            )

        if from_currency:
            query = query.filter(ExchangeRate.from_currency_code == from_currency)

        if to_currency:
            query = query.filter(ExchangeRate.to_currency_code == to_currency)

        if from_date:
            query = query.filter(ExchangeRate.effective_date >= from_date)

        if to_date:
            query = query.filter(ExchangeRate.effective_date <= to_date)

        if rate_type_code and organization_id:
            org_id = coerce_uuid(organization_id)
            rate_type = (
                db.query(ExchangeRateType)
                .filter(
                    and_(
                        ExchangeRateType.organization_id == org_id,
                        ExchangeRateType.type_code == rate_type_code,
                    )
                )
                .first()
            )
            if rate_type:
                query = query.filter(
                    ExchangeRate.rate_type_id == rate_type.rate_type_id
                )

        query = query.order_by(ExchangeRate.effective_date.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
fx_service = FXService()
