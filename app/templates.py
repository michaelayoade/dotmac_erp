"""
Centralized Jinja2 template configuration.

Import `templates` from this module instead of creating new Jinja2Templates instances.
This ensures consistent globals (i18n, datetime, etc.) across all routes.
"""

from datetime import datetime
from decimal import Decimal
from typing import Union

from fastapi.templating import Jinja2Templates

from app.i18n import t

# Single shared templates instance
templates = Jinja2Templates(directory="templates")

# Register global functions
templates.env.globals["now"] = datetime.now
templates.env.globals["t"] = t      # Translation function
templates.env.globals["_"] = t      # Alias for convenience


# Custom filters
def format_currency(value: Union[Decimal, float, int, None], symbol: str = "", decimals: int = 2) -> str:
    """Format a number as currency with thousand separators."""
    if value is None:
        return f"{symbol}0.00" if symbol else "0.00"
    try:
        num = float(value)
        formatted = f"{num:,.{decimals}f}"
        return f"{symbol}{formatted}" if symbol else formatted
    except (ValueError, TypeError):
        return str(value)


def format_number(value: Union[Decimal, float, int, None], decimals: int = 2) -> str:
    """Format a number with thousand separators."""
    if value is None:
        return "0"
    try:
        num = float(value)
        if decimals == 0:
            return f"{num:,.0f}"
        return f"{num:,.{decimals}f}"
    except (ValueError, TypeError):
        return str(value)


# Register custom filters
templates.env.filters["format_currency"] = format_currency
templates.env.filters["format_number"] = format_number
