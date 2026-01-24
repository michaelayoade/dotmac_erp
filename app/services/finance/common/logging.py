"""
Logging Utilities - Structured logging for IFRS services.

Provides consistent, contextual logging across all IFRS modules
with support for performance monitoring and correlation tracking.
"""

from __future__ import annotations

import functools
import logging
import time
from contextvars import ContextVar
from typing import Any, Callable, Optional, TypeVar
from uuid import UUID

# Context variables for request-scoped logging context
_log_context_org_id: ContextVar[Optional[str]] = ContextVar("log_org_id", default=None)
_log_context_user_id: ContextVar[Optional[str]] = ContextVar("log_user_id", default=None)
_log_context_correlation_id: ContextVar[Optional[str]] = ContextVar("log_correlation_id", default=None)


def set_log_context(
    organization_id: Optional[UUID | str] = None,
    user_id: Optional[UUID | str] = None,
    correlation_id: Optional[str] = None,
) -> None:
    """
    Set logging context for the current request.

    Args:
        organization_id: Current organization ID
        user_id: Current user ID
        correlation_id: Request correlation ID
    """
    if organization_id:
        _log_context_org_id.set(str(organization_id))
    if user_id:
        _log_context_user_id.set(str(user_id))
    if correlation_id:
        _log_context_correlation_id.set(correlation_id)


def clear_log_context() -> None:
    """Clear logging context."""
    _log_context_org_id.set(None)
    _log_context_user_id.set(None)
    _log_context_correlation_id.set(None)


def get_log_context() -> dict[str, Optional[str]]:
    """Get current logging context."""
    return {
        "org_id": _log_context_org_id.get(),
        "user_id": _log_context_user_id.get(),
        "correlation_id": _log_context_correlation_id.get(),
    }


class ContextualLogger:
    """
    Logger wrapper that automatically includes context.

    Usage:
        logger = ContextualLogger(__name__)
        logger.info("Processing invoice", invoice_id=invoice_id)

    Output includes:
        [INFO] app.services.ap.invoice | org=123 | Processing invoice invoice_id=456
    """

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)
        self._name = name

    def _format_message(self, message: str, **kwargs) -> str:
        """Format message with context and extra kwargs."""
        context = get_log_context()
        parts = []

        # Add context prefix
        if context["org_id"]:
            parts.append(f"org={context['org_id'][:8]}")
        if context["correlation_id"]:
            parts.append(f"corr={context['correlation_id'][:8]}")

        # Add message
        parts.append(message)

        # Add extra kwargs
        if kwargs:
            extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
            parts.append(extra)

        return " | ".join(parts)

    def debug(self, message: str, **kwargs) -> None:
        """Log debug message with context."""
        self._logger.debug(self._format_message(message, **kwargs))

    def info(self, message: str, **kwargs) -> None:
        """Log info message with context."""
        self._logger.info(self._format_message(message, **kwargs))

    def warning(self, message: str, **kwargs) -> None:
        """Log warning message with context."""
        self._logger.warning(self._format_message(message, **kwargs))

    def error(self, message: str, **kwargs) -> None:
        """Log error message with context."""
        self._logger.error(self._format_message(message, **kwargs))

    def exception(self, message: str, **kwargs) -> None:
        """Log exception with context and traceback."""
        self._logger.exception(self._format_message(message, **kwargs))


def get_logger(name: str) -> ContextualLogger:
    """
    Get a contextual logger for a module.

    Args:
        name: Module name (typically __name__)

    Returns:
        ContextualLogger instance
    """
    return ContextualLogger(name)


# Type variable for decorated functions
F = TypeVar('F', bound=Callable[..., Any])


def log_slow_operation(
    threshold_ms: int = 500,
    logger: Optional[logging.Logger] = None,
) -> Callable[[F], F]:
    """
    Decorator to log slow operations.

    Args:
        threshold_ms: Log warning if operation takes longer than this (milliseconds)
        logger: Logger to use (defaults to module logger)

    Usage:
        @log_slow_operation(threshold_ms=500)
        def expensive_query(...):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                if elapsed_ms > threshold_ms:
                    log = logger or logging.getLogger(func.__module__)
                    context = get_log_context()
                    log.warning(
                        "Slow operation: %s.%s took %.1fms (threshold: %dms) org=%s",
                        func.__module__,
                        func.__name__,
                        elapsed_ms,
                        threshold_ms,
                        context.get("org_id", "?")[:8] if context.get("org_id") else "?"
                    )

        return wrapper  # type: ignore
    return decorator


def log_service_call(
    logger: Optional[logging.Logger] = None,
    log_args: bool = False,
    log_result: bool = False,
) -> Callable[[F], F]:
    """
    Decorator to log service method calls.

    Args:
        logger: Logger to use (defaults to module logger)
        log_args: Log method arguments
        log_result: Log method result

    Usage:
        @log_service_call()
        def create_invoice(...):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            log = logger or logging.getLogger(func.__module__)
            context = get_log_context()
            org_prefix = f"org={context.get('org_id', '?')[:8]}" if context.get("org_id") else ""

            # Log entry
            if log_args:
                log.debug(
                    "%s | Calling %s args=%r kwargs=%r",
                    org_prefix, func.__name__, args[1:], kwargs  # Skip self/cls
                )
            else:
                log.debug("%s | Calling %s", org_prefix, func.__name__)

            start_time = time.perf_counter()
            try:
                result = func(*args, **kwargs)

                elapsed_ms = (time.perf_counter() - start_time) * 1000
                if log_result:
                    log.debug(
                        "%s | %s completed in %.1fms result=%r",
                        org_prefix, func.__name__, elapsed_ms, result
                    )
                else:
                    log.debug(
                        "%s | %s completed in %.1fms",
                        org_prefix, func.__name__, elapsed_ms
                    )

                return result

            except Exception as e:
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                log.exception(
                    "%s | %s failed after %.1fms: %s",
                    org_prefix, func.__name__, elapsed_ms, e
                )
                raise

        return wrapper  # type: ignore
    return decorator


def log_db_error(
    logger: Optional[logging.Logger] = None,
    operation: str = "database operation",
) -> Callable[[F], F]:
    """
    Decorator to log database errors with context.

    Args:
        logger: Logger to use
        operation: Description of the operation

    Usage:
        @log_db_error(operation="create invoice")
        def create_invoice(...):
            ...
    """
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                log = logger or logging.getLogger(func.__module__)
                context = get_log_context()
                log.exception(
                    "Database error in %s org=%s: %s",
                    operation,
                    context.get("org_id", "?"),
                    e
                )
                raise

        return wrapper  # type: ignore
    return decorator


class ServiceLogger:
    """
    Mixin class to add logging capabilities to services.

    Usage:
        class MyService(ServiceLogger):
            def create_item(self):
                self.log_info("Creating item", item_id=123)
    """

    @property
    def _service_logger(self) -> ContextualLogger:
        """Get logger for this service class."""
        if not hasattr(self, "_logger_instance"):
            self._logger_instance = get_logger(self.__class__.__module__)
        return self._logger_instance

    def log_debug(self, message: str, **kwargs) -> None:
        """Log debug message."""
        self._service_logger.debug(message, **kwargs)

    def log_info(self, message: str, **kwargs) -> None:
        """Log info message."""
        self._service_logger.info(message, **kwargs)

    def log_warning(self, message: str, **kwargs) -> None:
        """Log warning message."""
        self._service_logger.warning(message, **kwargs)

    def log_error(self, message: str, **kwargs) -> None:
        """Log error message."""
        self._service_logger.error(message, **kwargs)

    def log_exception(self, message: str, **kwargs) -> None:
        """Log exception with traceback."""
        self._service_logger.exception(message, **kwargs)
