"""Session context primitives for multi-tenant scoping.

Three public surfaces:
- ``prime_session(session, org_id)``: set the org context for a session.
- ``allow_cross_org(session)``: context manager that bypasses scoping.
- ``session_for_org(org_id)``: factory for non-HTTP entry points (added in Task 3).
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from uuid import UUID

from sqlalchemy.orm import Session


def prime_session(session: Session, organization_id: UUID) -> None:
    """Set the org context for ``session``. Called once at the entry-point
    boundary (HTTP request via ``get_db``; Celery task via ``session_for_org``).

    Subsequent ORM queries on org-scoped models will be filtered by this
    org_id (provided the listener is enabled). Calling this on an already-
    primed session overwrites the previous value — useful for tasks that
    iterate orgs.
    """
    session.info["organization_id"] = organization_id


@contextmanager
def allow_cross_org(session: Session) -> Iterator[None]:
    """Temporarily bypass org-scoping for the duration of the ``with`` block.

    Restores the prior state in ``finally`` so an exception inside the block
    does not leak the bypass to subsequent queries. Nested usage preserves
    the outer state correctly.

    Use sparingly — only for genuinely cross-tenant operations such as super-
    admin tooling, system maintenance jobs, or queries against globally-shared
    models that happen to have an ``organization_id`` column.
    """
    prior = session.info.get("allow_cross_org", False)
    session.info["allow_cross_org"] = True
    try:
        yield
    finally:
        session.info["allow_cross_org"] = prior


@contextmanager
def session_for_org(organization_id: UUID) -> Iterator[Session]:
    """Open a primed session for non-HTTP entry points (Celery tasks, CLI
    scripts). Yields a Session with ``organization_id`` already set on
    ``info``. Closes the session on exit, even on exception.

    For tasks that span multiple organizations (e.g., daily reminder
    batch), call this once per org_id in the loop:

        with session_for_org(org_id) as db:
            service.process(db)
            db.commit()
    """
    # Local import: SessionLocal is at module top-level of app.db; importing
    # it here avoids a circular dependency at import time.
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        prime_session(session, organization_id)
        yield session
    finally:
        session.close()
