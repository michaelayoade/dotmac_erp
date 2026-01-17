import json
import logging
import logging.config
from datetime import datetime, timezone


def _get_request_context() -> dict:
    """Get request context from context variables if available.

    This allows automatic inclusion of request_id and actor_id in logs
    without explicitly passing them.
    """
    try:
        from app.observability import get_request_id, get_actor_id
        context = {}
        request_id = get_request_id()
        if request_id:
            context["request_id"] = request_id
        actor_id = get_actor_id()
        if actor_id:
            context["actor_id"] = actor_id
        return context
    except ImportError:
        return {}


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Auto-include request context from context variables
        payload.update(_get_request_context())

        # Override with explicitly passed values
        for key in (
            "request_id",
            "actor_id",
            "path",
            "method",
            "status",
            "duration_ms",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


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
        "root": {"handlers": ["default"], "level": "INFO"},
    }
    logging.config.dictConfig(logging_config)
