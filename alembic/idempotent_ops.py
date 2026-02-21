"""Alembic operation guards for idempotent re-runs.

This module monkeypatches selected Alembic ``Operations`` methods so that
"already exists" / "does not exist" database errors are treated as safe no-ops.
It is intended for PostgreSQL deployments where partial migration runs can
leave the schema in an intermediate state.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from alembic.operations import Operations
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)

# SQLSTATE classes handled as idempotent outcomes.
# - duplicates: object already exists
# - missing: object already dropped / missing
_DUPLICATE_CODES = {"42P07", "42710", "42701"}
_MISSING_CODES = {"42P01", "42704", "42703"}

_CREATE_OPS = {
    "create_table",
    "create_index",
    "add_column",
    "create_foreign_key",
    "create_unique_constraint",
    "create_primary_key",
    "create_check_constraint",
    "create_exclude_constraint",
}
_DROP_OPS = {
    "drop_table",
    "drop_index",
    "drop_column",
    "drop_constraint",
}


def _sqlstate(exc: BaseException) -> str | None:
    if not isinstance(exc, DBAPIError):
        return None
    orig = exc.orig
    if orig is None:
        return None
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def _is_idempotent_db_error(op_name: str, exc: BaseException) -> bool:
    code = _sqlstate(exc)
    if not code:
        return False
    if op_name in _CREATE_OPS:
        return code in _DUPLICATE_CODES
    if op_name in _DROP_OPS:
        return code in _MISSING_CODES
    return False


def _wrap_operation(op_name: str, method: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(method)
    def _wrapped(self: Operations, *args: Any, **kwargs: Any) -> Any:
        # Prefer native IF [NOT] EXISTS where Alembic supports it.
        if op_name == "create_index":
            kwargs.setdefault("if_not_exists", True)
        elif op_name == "drop_index" or op_name == "drop_table":
            kwargs.setdefault("if_exists", True)
        elif op_name == "create_table":
            kwargs.setdefault("if_not_exists", True)

        bind = self.get_bind()
        if bind is None:
            return method(self, *args, **kwargs)

        # Use a SAVEPOINT so one handled duplicate/missing error does not abort
        # the whole migration transaction.
        try:
            with bind.begin_nested():
                return method(self, *args, **kwargs)
        except Exception as exc:
            if _is_idempotent_db_error(op_name, exc):
                logger.warning(
                    "Ignoring idempotent Alembic operation error",
                    extra={"operation": op_name, "error": str(exc)},
                )
                return None
            raise

    return _wrapped


def install_idempotent_operation_guards() -> None:
    """Install monkeypatch wrappers for selected Alembic Operations methods."""
    if getattr(Operations, "_dotmac_idempotent_ops_installed", False):
        return

    targets = sorted(_CREATE_OPS | _DROP_OPS)
    for op_name in targets:
        method = getattr(Operations, op_name, None)
        if method is None:
            continue
        setattr(Operations, op_name, _wrap_operation(op_name, method))

    Operations._dotmac_idempotent_ops_installed = True
