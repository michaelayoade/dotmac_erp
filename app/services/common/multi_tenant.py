"""Multi-tenant service helpers.

Thin sugar over ``db.get`` for the most common pattern: fetch by PK,
404 if missing or cross-org. The org-scoping is handled by the session
listener; this helper just translates ``None`` into a useful exception.
"""

from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

from app.errors import NotFoundError  # adapt per verification

T = TypeVar("T")


def get_or_404_for_org(db: Session, model: type[T], pk: UUID) -> T:
    """Fetch ``model`` by PK or raise ``NotFoundError``.

    The session listener at ``app/db/org_listener.py`` automatically
    scopes the underlying ``db.get`` call to the session's primed
    organization_id. If the PK doesn't exist OR exists in a different
    org, ``db.get`` returns ``None`` and this helper raises 404.

    The error message names the model only — never reveals whether the
    PK exists in another org (would leak existence info across tenants).

    Equivalent to::

        obj = db.get(model, pk)
        if obj is None:
            raise NotFoundError(...)
        return obj
    """
    obj = db.get(model, pk)
    if obj is None:
        raise NotFoundError(f"{model.__name__} not found")
    return obj
