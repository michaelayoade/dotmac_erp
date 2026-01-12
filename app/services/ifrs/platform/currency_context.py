"""
Currency context helpers for UI.

Provides active currency options and organization defaults.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.ifrs.core_fx.currency import Currency
from app.services.ifrs.platform.org_context import org_context_service


def get_currency_context(db: Session, organization_id: str) -> dict:
    settings = org_context_service.get_currency_settings(db, organization_id)
    currencies = (
        db.query(Currency)
        .filter(Currency.is_active.is_(True))
        .order_by(Currency.currency_code)
        .all()
    )

    return {
        "currencies": [
            {
                "code": currency.currency_code,
                "name": currency.currency_name,
                "symbol": currency.symbol or "",
            }
            for currency in currencies
        ],
        "default_currency_code": settings["functional"],
        "presentation_currency_code": settings["presentation"],
    }
