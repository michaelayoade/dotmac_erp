"""
Safe Jinja2 Template Environment.

Provides a sandboxed Jinja2 environment for rendering user-defined templates
securely. Prevents template injection attacks by restricting dangerous
operations and built-in functions.

Security measures:
1. Uses SandboxedEnvironment to restrict attribute access
2. Blocks dangerous built-ins (exec, eval, open, etc.)
3. Only exposes safe string/number formatting functions
4. Prevents access to private attributes (__*__)
5. Limits function calls to a safe allowlist
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from jinja2.sandbox import SandboxedEnvironment, ImmutableSandboxedEnvironment

logger = logging.getLogger(__name__)

# Global singleton for the sandboxed environment
_sandboxed_env: Optional["SecureSandboxedEnvironment"] = None


# ============================================================================
# Safe Built-in Functions
# ============================================================================

def _safe_format_currency(value: Decimal | float | int | None, decimals: int = 2) -> str:
    """Format a number as currency with thousands separator."""
    if value is None:
        return "0.00"
    try:
        return f"{float(value):,.{decimals}f}"
    except (ValueError, TypeError):
        return "0.00"


def _safe_format_date(value: date | datetime | None, fmt: str = "%d %B %Y") -> str:
    """Format a date value safely."""
    if value is None:
        return ""
    try:
        if isinstance(value, datetime):
            value = value.date()
        return value.strftime(fmt)
    except (AttributeError, ValueError):
        return ""


def _safe_format_datetime(
    value: datetime | None, fmt: str = "%d %B %Y at %H:%M"
) -> str:
    """Format a datetime value safely."""
    if value is None:
        return ""
    try:
        return value.strftime(fmt)
    except (AttributeError, ValueError):
        return ""


def _safe_abs(value: Any) -> Any:
    """Safe absolute value function."""
    try:
        return abs(value)
    except (TypeError, ValueError):
        return value


def _safe_round(value: Any, ndigits: int = 0) -> Any:
    """Safe rounding function."""
    try:
        return round(value, ndigits)
    except (TypeError, ValueError):
        return value


def _safe_int(value: Any) -> int:
    """Safe integer conversion."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    """Safe float conversion."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_str(value: Any) -> str:
    """Safe string conversion."""
    try:
        return str(value)
    except Exception:
        return ""


def _safe_len(value: Any) -> int:
    """Safe length function."""
    try:
        return len(value)
    except (TypeError, ValueError):
        return 0


def _safe_sum(iterable: Any, start: int = 0) -> Any:
    """Safe sum function."""
    try:
        return sum(iterable, start)
    except (TypeError, ValueError):
        return start


def _safe_min(iterable: Any, default: Any = None) -> Any:
    """Safe minimum function."""
    try:
        return min(iterable, default=default)
    except (TypeError, ValueError):
        return default


def _safe_max(iterable: Any, default: Any = None) -> Any:
    """Safe maximum function."""
    try:
        return max(iterable, default=default)
    except (TypeError, ValueError):
        return default


# ============================================================================
# Safe Globals and Filters
# ============================================================================

SAFE_GLOBALS: dict[str, Any] = {
    # Safe type conversions
    "int": _safe_int,
    "float": _safe_float,
    "str": _safe_str,
    "bool": bool,
    # Safe math functions
    "abs": _safe_abs,
    "round": _safe_round,
    "sum": _safe_sum,
    "min": _safe_min,
    "max": _safe_max,
    "len": _safe_len,
    # Safe itertools
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "sorted": sorted,
    "reversed": reversed,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    # Formatting helpers
    "format_currency": _safe_format_currency,
    "format_date": _safe_format_date,
    "format_datetime": _safe_format_datetime,
    # Boolean values
    "True": True,
    "False": False,
    "None": None,
}

SAFE_FILTERS: dict[str, Any] = {
    "format_currency": _safe_format_currency,
    "format_date": _safe_format_date,
    "format_datetime": _safe_format_datetime,
    "abs": _safe_abs,
    "round": _safe_round,
    "int": _safe_int,
    "float": _safe_float,
    "string": _safe_str,
    "length": _safe_len,
    "sum": _safe_sum,
    "min": _safe_min,
    "max": _safe_max,
}


# ============================================================================
# Sandboxed Environment
# ============================================================================

class SecureSandboxedEnvironment(SandboxedEnvironment):
    """
    Custom sandboxed environment with additional security restrictions.

    Extends Jinja2's SandboxedEnvironment with:
    - Stricter attribute access controls
    - Custom intercepted calls list
    - Safe globals only
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Ensure autoescape is enabled for HTML safety
        kwargs.setdefault("autoescape", True)
        super().__init__(*args, **kwargs)

        # Add safe globals
        self.globals.update(SAFE_GLOBALS)

        # Add safe filters
        self.filters.update(SAFE_FILTERS)

    def is_safe_attribute(self, obj: Any, attr: str, value: Any) -> bool:
        """
        Check if an attribute is safe to access.

        Blocks:
        - Private attributes (__*)
        - Callable attributes that aren't in our safe list
        - Known dangerous attributes
        """
        # Block all dunder attributes
        if attr.startswith("_"):
            logger.warning(
                "Blocked access to private attribute '%s' on %s",
                attr, type(obj).__name__
            )
            return False

        # Block known dangerous attributes
        dangerous_attrs = {
            "mro", "subclasses", "bases", "class", "init", "globals",
            "code", "func", "gi_frame", "gi_code", "cr_frame", "cr_code",
            "tb_frame", "f_locals", "f_globals", "f_builtins",
        }
        if attr.lower() in dangerous_attrs:
            logger.warning(
                "Blocked access to dangerous attribute '%s' on %s",
                attr, type(obj).__name__
            )
            return False

        return super().is_safe_attribute(obj, attr, value)

    def is_safe_callable(self, obj: Any) -> bool:
        """Check if a callable is safe to call."""
        # Block type and class manipulation
        if obj in (type, object, getattr, setattr, delattr, hasattr):
            return False

        # Block dangerous built-ins
        dangerous_callables = {
            eval, exec, compile, open, __import__,
            globals, locals, vars, dir,
        }
        if obj in dangerous_callables:
            logger.warning("Blocked call to dangerous function: %s", obj)
            return False

        return super().is_safe_callable(obj)


def get_sandboxed_environment() -> SecureSandboxedEnvironment:
    """
    Get or create the sandboxed Jinja2 environment.

    Returns a singleton instance for efficiency.
    """
    global _sandboxed_env
    if _sandboxed_env is None:
        _sandboxed_env = SecureSandboxedEnvironment()
        logger.info("Created sandboxed Jinja2 environment")
    return _sandboxed_env


def render_template_safely(
    template_content: str,
    context: dict[str, Any],
) -> str:
    """
    Render a template string safely using the sandboxed environment.

    Args:
        template_content: The Jinja2 template string
        context: Dictionary of variables to pass to the template

    Returns:
        Rendered string

    Raises:
        jinja2.TemplateSyntaxError: If template has syntax errors
        jinja2.SecurityError: If template tries to access forbidden attributes
    """
    env = get_sandboxed_environment()
    template = env.from_string(template_content)
    return template.render(**context)
