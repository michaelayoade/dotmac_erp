"""
Centralized Jinja2 template configuration.

Import `templates` from this module instead of creating new Jinja2Templates instances.
This ensures consistent globals (i18n, datetime, etc.) across all routes.
"""

import html
import re
from datetime import datetime
from decimal import Decimal
from typing import Union
from urllib.parse import unquote_plus

from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from app.i18n import t
from app.services import formatters as _fmt

# Single shared templates instance
templates = Jinja2Templates(directory="templates")

# Register global functions
templates.env.globals["now"] = datetime.now
templates.env.globals["t"] = t  # Translation function
templates.env.globals["_"] = t  # Alias for convenience


# Custom filters — delegate to service-layer formatters so that org-aware
# separators, currency codes, and date formats are applied automatically
# when a formatting context is active (see app.services.formatting_context).
def format_currency(
    value: Union[Decimal, float, int, None], symbol: str = "", decimals: int = 2
) -> str:
    """Format a number as currency with thousand separators.

    Negative amounts are displayed in accounting notation: ``(1,234.56)``
    instead of ``-1,234.56``.

    Template usage::

        {{ amount | format_currency('$', 0) }}
        {{ amount | format_currency }}           {# uses org defaults #}
    """
    if value is None:
        return f"{symbol}0.00" if symbol else "0.00"

    # Detect negative for accounting parentheses notation
    try:
        dec = Decimal(str(value))
        is_negative = dec < 0
        abs_value: Decimal | float | int = abs(dec) if is_negative else value
    except (ValueError, TypeError, ArithmeticError):
        is_negative = False
        abs_value = value

    # When a currency symbol/prefix is explicitly provided, concatenate it
    # directly (the core formatter would add an extra space via its own prefix).
    if symbol:
        formatted = f"{symbol}{_fmt.format_currency(abs_value, show_symbol=False, decimal_places=decimals)}"
    else:
        formatted = _fmt.format_currency(
            abs_value, show_symbol=False, decimal_places=decimals
        )

    # Accounting notation: wrap negatives in parentheses
    if is_negative:
        return f"({formatted})"
    return formatted


def format_number(value: Union[Decimal, float, int, None], decimals: int = 2) -> str:
    """Format a number with org-aware thousand separators."""
    if value is None:
        return "0"
    return _fmt.format_number(value, decimal_places=decimals, none_value="0")


def format_date_filter(value, fmt: str = "") -> str:
    """Jinja2 filter for date formatting.  Uses org prefs when *fmt* is empty."""
    if fmt:
        return _fmt.format_date(value, format=fmt)
    return _fmt.format_date(value)


def format_datetime_filter(value, fmt: str = "") -> str:
    """Jinja2 filter for datetime formatting.  Uses org prefs when *fmt* is empty."""
    if fmt:
        return _fmt.format_datetime(value, fmt=fmt)
    return _fmt.format_datetime(value)


def urldecode(value: str | None) -> str:
    """Decode URL-encoded string (handles + and %XX sequences)."""
    if value is None:
        return ""
    return unquote_plus(value)


# HTML Sanitization for safe rendering
# Whitelist of allowed HTML tags
ALLOWED_TAGS = frozenset(
    [
        "p",
        "br",
        "b",
        "i",
        "u",
        "strong",
        "em",
        "s",
        "strike",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "li",
        "a",
        "span",
        "div",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "blockquote",
        "pre",
        "code",
    ]
)

# Allowed attributes for specific tags
ALLOWED_ATTRS = {
    "a": {"href", "title", "target"},
    "span": {"class"},
    "div": {"class"},
    "table": {"class"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

# Pattern to match HTML tags
TAG_PATTERN = re.compile(r"<(/?)(\w+)([^>]*)>", re.IGNORECASE)
# Pattern to match attributes
ATTR_PATTERN = re.compile(
    r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\S+))', re.IGNORECASE
)
# Pattern for dangerous protocols
DANGEROUS_PROTOCOLS = re.compile(r"^\s*(?:javascript|vbscript|data):", re.IGNORECASE)


def sanitize_html(value: str | None) -> Markup:
    """
    Sanitize HTML content to prevent XSS attacks.

    Allows only safe HTML tags and attributes.
    Strips event handlers and javascript: URLs.

    Usage in templates:
        {{ content | sanitize_html }}

    Instead of unsafe:
        {{ content | safe }}
    """
    if value is None:
        return Markup("")

    if not isinstance(value, str):
        value = str(value)

    def replace_tag(match: re.Match[str]) -> str:
        closing, tag_name, attrs = match.groups()
        tag_lower = tag_name.lower()

        # Remove disallowed tags entirely
        if tag_lower not in ALLOWED_TAGS:
            return ""

        # Closing tag - just return it
        if closing:
            return f"</{tag_lower}>"

        # Process attributes
        allowed_attrs = ALLOWED_ATTRS.get(tag_lower, set())
        safe_attrs = []

        for attr_match in ATTR_PATTERN.finditer(attrs):
            attr_name = attr_match.group(1).lower()
            attr_value = (
                attr_match.group(2) or attr_match.group(3) or attr_match.group(4) or ""
            )

            # Skip disallowed attributes
            if attr_name not in allowed_attrs:
                continue

            # Skip event handlers (onclick, onload, etc.)
            if attr_name.startswith("on"):
                continue

            # Sanitize href/src to prevent javascript: URLs
            if attr_name in ("href", "src"):
                if DANGEROUS_PROTOCOLS.match(attr_value):
                    continue
                # Allow only http, https, mailto, tel, and relative URLs
                if attr_value and not attr_value.startswith(
                    ("/", "#", "http://", "https://", "mailto:", "tel:")
                ):
                    continue

            # Escape attribute value
            safe_value = html.escape(attr_value, quote=True)
            safe_attrs.append(f'{attr_name}="{safe_value}"')

        if safe_attrs:
            return f"<{tag_lower} {' '.join(safe_attrs)}>"
        return f"<{tag_lower}>"

    # Replace tags with sanitized versions
    sanitized = TAG_PATTERN.sub(replace_tag, value)

    # Mark as safe for Jinja2
    # Markup is safe because sanitize_html removes unsafe tags/attrs.
    return Markup(sanitized)  # noqa: S704 # nosec B704


def nl2br(value: str | None) -> Markup:
    """
    Convert newlines to <br> tags safely.

    Escapes HTML content first, then replaces newlines.
    Returns a safe Markup object.

    Usage in templates:
        {{ content | nl2br }}

    Instead of unsafe:
        {{ content | replace('\\n', '<br>') | safe }}
    """
    if value is None:
        return Markup("")  # noqa: S704

    # Escape HTML characters first
    escaped = html.escape(str(value))
    # Replace newlines with <br> tags
    with_breaks = escaped.replace("\n", "<br>")
    # Markup is safe because we escape input before inserting <br>.
    return Markup(with_breaks)  # noqa: S704 # nosec B704


# Register custom filters
templates.env.filters["format_currency"] = format_currency
templates.env.filters["format_number"] = format_number
templates.env.filters["format_date"] = format_date_filter
templates.env.filters["format_datetime"] = format_datetime_filter
templates.env.filters["urldecode"] = urldecode
templates.env.filters["sanitize_html"] = sanitize_html
templates.env.filters["nl2br"] = nl2br
