"""
Currency context helpers for UI.

Provides active currency options and organization defaults.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models.ifrs.core_fx.currency import Currency
from app.services.ifrs.platform.org_context import org_context_service


def _ensure_default_currency(db: Session) -> None:
    """Ensure the default currency (NGN) exists in the database."""
    default_code = app_settings.default_functional_currency_code
    existing = db.get(Currency, default_code)
    if existing:
        return

    # Create the default currency
    db.add(
        Currency(
            currency_code=default_code,
            currency_name="Nigerian Naira",
            symbol="₦",
            decimal_places=2,
            is_active=True,
            is_crypto=False,
        )
    )
    db.commit()


def get_currency_context(db: Session, organization_id: str) -> dict:
    """Get currency context for templates.

    Ensures at least the default currency exists before returning.
    """
    # Ensure default currency exists
    _ensure_default_currency(db)

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
