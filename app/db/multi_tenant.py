"""Multi-tenant primitives: org-scoping detection, deny-list, exception type.

The session listener at ``app/db/org_listener.py`` consumes these primitives
to inject ``WHERE organization_id = ?`` filters on ORM queries.
"""

from __future__ import annotations

from typing import Any


class MissingOrgContextError(RuntimeError):
    """Raised when an ORM query targets an org-scoped model but the session
    has no primed ``organization_id`` and is not inside ``allow_cross_org``.

    A missing org context is a programming bug (forgotten priming at the
    entry-point boundary), not a runtime condition. The exception message
    names the model class so the offending site is debuggable from logs.
    """

    def __init__(self, model_name: str, *, query_repr: str | None = None) -> None:
        detail = (
            f"ORM query against org-scoped model {model_name!r} executed without "
            f"a primed session.info['organization_id']. Either prime the session "
            f"at the entry point (HTTP route via Depends(get_db); Celery task via "
            f"session_for_org(org_id)) or wrap the query in "
            f"`with allow_cross_org(session):` if it is genuinely cross-tenant."
        )
        if query_repr:
            detail += f"\nQuery: {query_repr}"
        super().__init__(detail)
        self.model_name = model_name


# Models that are genuinely cross-tenant or whose ``organization_id`` column
# is the tenant identifier itself. The listener treats these as not-org-scoped
# and applies no filter.
#
# Models without an ``organization_id`` column (e.g., Currency, Country,
# TaxJurisdiction) are skipped automatically by ``is_org_scoped`` — they
# do NOT need to appear here.
def _build_deny_list() -> frozenset[type]:
    from app.models.finance.core_org.organization import Organization

    return frozenset({Organization})


# Built once at module-import time. The lazy import inside _build_deny_list()
# defers Organization until this module is first imported, which is
# sufficient circular-import protection — no race window from on-demand
# building under concurrent first-call.
_DENY_LIST: frozenset[type] = _build_deny_list()


def get_cross_org_deny_list() -> frozenset[type]:
    """Return the cross-org deny-list (immutable frozenset)."""
    return _DENY_LIST


def is_org_scoped(target: Any) -> bool:
    """Return True iff ``target`` is a model class with an ``organization_id``
    mapped column AND is not on the cross-org deny-list.

    Accepts a class, an instance, or an ``AliasedClass`` (e.g., the result of
    ``sqlalchemy.orm.aliased(Model)``). ``None`` and non-mapped types return
    False without raising.
    """
    if target is None:
        return False

    # Unwrap SQLAlchemy aliases so aliased(OrgScopedModel) is detected the
    # same as the underlying class. Without this, queries that join via
    # aliased() bypass the listener's filter — a silent leak path.
    # NOTE: AliasedClass is not re-exported from ``sqlalchemy.orm`` in SA 2.0;
    # it lives in ``sqlalchemy.orm.util``.
    from sqlalchemy.orm.util import AliasedClass

    if isinstance(target, AliasedClass):
        from sqlalchemy import inspect as sa_inspect

        cls = sa_inspect(target).mapper.class_
    elif isinstance(target, type):
        cls = target
    else:
        cls = type(target)

    table = getattr(cls, "__table__", None)
    if table is None:
        return False
    if "organization_id" not in table.columns:
        return False
    return cls not in get_cross_org_deny_list()
