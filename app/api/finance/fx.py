"""
FX Rate Lookup API — exchange rate endpoint for form auto-fill.

Returns the latest SPOT rate for a given target currency relative to
the organization's functional currency.
"""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.deps import require_tenant_auth
from app.db import SessionLocal
from app.models.finance.core_fx.exchange_rate import ExchangeRate
from app.models.finance.core_fx.exchange_rate_type import ExchangeRateType
from app.models.finance.core_org.organization import Organization
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fx", tags=["fx"])


def _get_db():  # noqa: ANN202
    with SessionLocal() as db:
        yield db


@router.get("/rate")
def lookup_rate(
    to: str = Query(
        ..., min_length=3, max_length=3, description="Target currency code"
    ),
    rate_date: date | None = Query(
        None, alias="date", description="Effective date (default: today)"
    ),
    auth: dict = Depends(require_tenant_auth),
    db: Session = Depends(_get_db),
) -> dict:
    """
    Look up the latest SPOT exchange rate for a currency pair.

    The 'from' currency is always the organization's functional currency.
    Returns ``{"rate": null}`` when no rate is found (never 404).
    """
    org_id = coerce_uuid(auth["organization_id"])
    effective = rate_date or date.today()

    # Get org's functional currency
    org = db.get(Organization, org_id)
    if not org or not org.functional_currency_code:
        return {
            "rate": None,
            "message": "Organization functional currency not configured",
        }

    from_currency: str = org.functional_currency_code

    # Same currency — trivial case
    if from_currency.upper() == to.upper():
        return {
            "rate": "1",
            "inverse_rate": "1",
            "effective_date": str(effective),
            "source": "identity",
        }

    # Find SPOT rate type for org
    rate_type_stmt = select(ExchangeRateType).where(
        and_(
            ExchangeRateType.organization_id == org_id,
            ExchangeRateType.type_code == "SPOT",
        )
    )
    rate_type = db.scalar(rate_type_stmt)
    if not rate_type:
        return {"rate": None, "message": "No SPOT rate type configured"}

    # Try direct rate: from_currency → to
    direct_stmt = (
        select(ExchangeRate)
        .where(
            and_(
                ExchangeRate.organization_id == org_id,
                ExchangeRate.from_currency_code == from_currency,
                ExchangeRate.to_currency_code == to.upper(),
                ExchangeRate.rate_type_id == rate_type.rate_type_id,
                ExchangeRate.effective_date <= effective,
            )
        )
        .order_by(ExchangeRate.effective_date.desc())
        .limit(1)
    )
    direct = db.scalar(direct_stmt)

    if direct:
        return {
            "rate": str(direct.exchange_rate),
            "inverse_rate": str(direct.inverse_rate),
            "effective_date": str(direct.effective_date),
            "source": direct.source.value if direct.source else "MANUAL",
        }

    # Try inverse rate: to → from_currency
    inverse_stmt = (
        select(ExchangeRate)
        .where(
            and_(
                ExchangeRate.organization_id == org_id,
                ExchangeRate.from_currency_code == to.upper(),
                ExchangeRate.to_currency_code == from_currency,
                ExchangeRate.rate_type_id == rate_type.rate_type_id,
                ExchangeRate.effective_date <= effective,
            )
        )
        .order_by(ExchangeRate.effective_date.desc())
        .limit(1)
    )
    inverse = db.scalar(inverse_stmt)

    if inverse:
        return {
            "rate": str(inverse.inverse_rate),
            "inverse_rate": str(inverse.exchange_rate),
            "effective_date": str(inverse.effective_date),
            "source": inverse.source.value if inverse.source else "MANUAL",
        }

    return {"rate": None, "message": f"No rate found for {from_currency}/{to.upper()}"}
