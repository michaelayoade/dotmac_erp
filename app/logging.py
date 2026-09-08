import json
import logging
import logging.config
from datetime import datetime, timezone


try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc


def _get_request_context() -> dict[str, str]:
    """Get request context from context variables if available.

    Pulls request_id / actor_id from the observability middleware and
    org_id / correlation_id from the finance logging context so they
    appear as structured JSON fields in every log record.
    """
    context: dict[str, str] = {}
    try:
        from app.observability import get_actor_id, get_request_id

        request_id = get_request_id()
        if request_id:
            context["request_id"] = request_id
        actor_id = get_actor_id()
        if actor_id:
            context["actor_id"] = actor_id
    except ImportError:
        pass

    try:
        from app.services.finance.common.logging import get_log_context

        log_ctx = get_log_context()
        for key in ("org_id", "user_id", "correlation_id"):
            val = log_ctx.get(key)
            if val:
                context[key] = val
    except ImportError:
        pass

    return context


# Standard LogRecord attributes that should NOT be forwarded as extra fields.
_BUILTIN_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)

# Known structured fields we always promote to top-level JSON keys.
_KNOWN_EXTRA_KEYS = (
    "request_id",
    "actor_id",
    "org_id",
    "user_id",
    "correlation_id",
    "path",
    "method",
    "status",
    "duration_ms",
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Auto-include request context from context variables
        payload.update(_get_request_context())

        # Promote known extra fields passed via extra={} on log calls
        for key in _KNOWN_EXTRA_KEYS:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        # Capture arbitrary caller-supplied extra fields (e.g. invoice_id,
        # claim_id) so ContextualLogger kwargs appear as structured JSON.
        for key, value in record.__dict__.items():
            if key not in _BUILTIN_RECORD_ATTRS and key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": JsonLogFormatter,
            }
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "json",
            }
        },
        "loggers": {
            # Celery emits one INFO record for every successful task through
            # this logger.  Task outcome and duration are captured separately
            # by app.celery_app metrics, so retaining those records adds high
            # volume without operational signal.  Keep warnings and failures
            # visible on the normal JSON handler.
            "celery.app.trace": {
                "handlers": ["default"],
                "level": "WARNING",
                "propagate": False,
            },
        },
        "root": {"handlers": ["default"], "level": "INFO"},
    }
    logging.config.dictConfig(logging_config)
