"""
Template Renderer for Workflow Automation.

Provides sandboxed Jinja2 template rendering for email subjects,
notification bodies, and other workflow action text that needs
variable substitution from entity context.
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from jinja2 import TemplateSyntaxError, Undefined
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

# Shared sandboxed environment — prevents access to dangerous
# Python internals (e.g. __class__, __subclasses__, etc.)
_env = SandboxedEnvironment(
    autoescape=True,
    undefined=Undefined,
)


def _build_template_context(
    entity_type: str,
    entity_id: Any,
    old_values: Optional[Dict[str, Any]] = None,
    new_values: Optional[Dict[str, Any]] = None,
    user_id: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the variable context available to templates.

    Variables exposed:
        entity_type, entity_id — the triggering entity
        old.* / new.* — field values before/after the event
        user_id — the user who triggered the event
        today, now — current date/datetime strings
        Any keys from *extra*
    """
    ctx: Dict[str, Any] = {
        "entity_type": str(entity_type),
        "entity_id": str(entity_id) if entity_id else "",
        "old": _stringify_values(old_values or {}),
        "new": _stringify_values(new_values or {}),
        "user_id": str(user_id) if user_id else "",
        "today": date.today().isoformat(),
        "now": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra:
        ctx.update(extra)
    return ctx


def _stringify_values(values: Dict[str, Any]) -> Dict[str, str]:
    """Convert all values to strings for safe template rendering."""
    result: Dict[str, str] = {}
    for k, v in values.items():
        if v is None:
            result[k] = ""
        elif isinstance(v, Decimal):
            result[k] = str(v)
        elif isinstance(v, (datetime, date)):
            result[k] = v.isoformat()
        else:
            result[k] = str(v)
    return result


def render_template(
    template_string: str,
    entity_type: str,
    entity_id: Any,
    old_values: Optional[Dict[str, Any]] = None,
    new_values: Optional[Dict[str, Any]] = None,
    user_id: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """Render a Jinja2 template string in a sandboxed environment.

    Args:
        template_string: The Jinja2 template text (e.g. "Invoice {{new.invoice_number}} approved")
        entity_type: Entity type string (e.g. "INVOICE")
        entity_id: Entity UUID
        old_values: Dict of field values before the event
        new_values: Dict of field values after the event
        user_id: UUID of the user who triggered the event
        extra: Additional variables to expose

    Returns:
        Rendered string. On error, returns the original template_string
        with basic variable substitution as a fallback.
    """
    if not template_string:
        return ""

    ctx = _build_template_context(
        entity_type=entity_type,
        entity_id=entity_id,
        old_values=old_values,
        new_values=new_values,
        user_id=user_id,
        extra=extra,
    )

    try:
        tpl = _env.from_string(template_string)
        return tpl.render(ctx)
    except TemplateSyntaxError:
        logger.warning(
            "Invalid Jinja2 template syntax in workflow action config: %s",
            template_string[:200],
        )
    except Exception:
        logger.exception("Template render failed")

    # Fallback: simple {{entity_id}} substitution
    result = template_string
    result = result.replace("{{entity_id}}", str(entity_id) if entity_id else "")
    result = result.replace("{{entity_type}}", str(entity_type))
    return result
