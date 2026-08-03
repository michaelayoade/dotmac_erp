"""
Currency context helpers for UI.

Provides active currency options and organization defaults.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance.core_fx.currency import Currency
from app.services.finance.money_boundary import SUPPORTED_CURRENCIES
from app.services.finance.platform.org_context import org_context_service

# Display metadata for the boundary-provisioned currencies. decimal_places
# comes from SUPPORTED_CURRENCIES (the E4 minor-unit authority) so the
# database rows can never drift from the boundary registry at seed time —
# the core_fx consistency canary (tests/integration/platform/
# test_currency_registry_canary.py) enforces the invariant on live rows.
_CURRENCY_DISPLAY: dict[str, tuple[str, str]] = {
    "NGN": ("Nigerian Naira", "₦"),
    "USD": ("US Dollar", "$"),
    "EUR": ("Euro", "€"),
    "GBP": ("Pound Sterling", "£"),
}


def ensure_supported_currencies(db: Session) -> int:
    """Idempotently provision every SUPPORTED_CURRENCIES entry in
    ``core_fx.currency`` (the same path that has always seeded NGN).

    Returns the number of rows created; does not commit — the caller owns
    the transaction. Existing rows are never modified here (drift against
    the registry is a canary failure, not something to silently rewrite).
    """
    created = 0
    for code, minor_units in SUPPORTED_CURRENCIES.by_code.items():
        if db.get(Currency, code):
            continue
        name, symbol = _CURRENCY_DISPLAY.get(code, (code, code))
        db.add(
            Currency(
                currency_code=code,
                currency_name=name,
                symbol=symbol,
                decimal_places=minor_units,
                is_active=True,
                is_crypto=False,
            )
        )
        created += 1
    return created


def _ensure_default_currency(db: Session) -> None:
    """Ensure the boundary-supported currencies exist in the database."""
    if ensure_supported_currencies(db):
        db.commit()


def get_currency_context(db: Session, organization_id: str) -> dict:
    """Get currency context for templates.

    Ensures at least the default currency exists before returning.
    """
    # Ensure default currency exists
    _ensure_default_currency(db)

    settings = org_context_service.get_currency_settings(db, organization_id)
    currencies = db.scalars(
        select(Currency)
        .where(Currency.is_active.is_(True))
        .order_by(Currency.currency_code)
    ).all()

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
