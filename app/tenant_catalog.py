"""The one approved path from *no tenant context* to *a set of tenant ids*.

Every non-HTTP entry point that operates on more than one organization has the
same bootstrapping problem: it must learn which organizations exist before it
can open a tenant-scoped session for any of them, and `core_org.organization`
is itself RLS-protected. The historical answer was ``cross_org_session()``,
which bypasses only ERP's SQLAlchemy listener — never PostgreSQL RLS. That
worked solely because production runs as the `postgres` superuser. Under
``app_user`` the same query returns zero rows, and a scheduled task that
enumerates zero organizations does nothing at all and reports success.

This module replaces that pattern. It calls the narrow ``SECURITY DEFINER``
function installed by ``20260815_tenant_catalog_discovery``, which returns
identifiers and nothing else.

Use it, then hand each id to ``session_for_org``::

    from app.db.session_context import session_for_org
    from app.tenant_catalog import active_organization_ids

    for org_id in active_organization_ids():
        with session_for_org(org_id) as db:
            Service(db).run()
            db.commit()

``for_each_organization`` in ``app.db.session_context`` wraps exactly that loop
and is the preferred spelling for new callers.

What this module deliberately does **not** do
---------------------------------------------

It does not return organization *rows*. Nothing here exposes a name, a slug, a
status, or any other column, and it must stay that way: the moment this returns
a row, it becomes a general cross-tenant read path with none of RLS's
guarantees, and every caller that only needed an id pays for it. Organization
data is read through a tenant-scoped session like any other tenant data.

It also does not hold a cross-tenant credential. The privilege lives in the
database function's owner, not in the application's login — see the migration's
docstring for why that distinction is the entire point.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select, text

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

#: Matches the function installed by ``20260815_tenant_catalog_discovery``.
#: Kept as one constant so the architecture test can assert that no other
#: module in the tree names it.
DISCOVERY_FUNCTION = "tenant_catalog.organization_ids"

#: A set-returning function is selected FROM, not called in the target list, so
#: PostgreSQL streams it as a one-column relation. ``scalars()`` then takes that
#: column without depending on what the column is named.
#:
#: Written as a literal rather than interpolating ``DISCOVERY_FUNCTION``: the
#: only variable here is a bound parameter, and a query built by string
#: formatting is worth neither the lint suppression nor the second read. The
#: assertion below keeps the literal and the constant from drifting.
_DISCOVER_SQL = text("SELECT * FROM tenant_catalog.organization_ids(:include_inactive)")

assert DISCOVERY_FUNCTION in str(_DISCOVER_SQL), (
    "the discovery SQL must call the function named by DISCOVERY_FUNCTION; "
    "the architecture test asserts nothing else in the tree names it"
)


def _discover_postgresql(session: Session, include_inactive: bool) -> list[uuid.UUID]:
    """Call the definer through raw SQL.

    Raw SQL rather than the ORM on purpose: ``select(Organization...)`` would
    enter the org-filter listener, which raises ``MissingOrgContextError`` on an
    unprimed session. Suppressing that with ``allow_cross_org`` would put the
    exact bypass this module exists to retire back on the discovery path.
    """
    rows = session.scalars(_DISCOVER_SQL, {"include_inactive": include_inactive})
    return [row if isinstance(row, uuid.UUID) else uuid.UUID(str(row)) for row in rows]


def _discover_sqlite(session: Session, include_inactive: bool) -> list[uuid.UUID]:
    """Unit-test lane only: SQLite has neither RLS nor SECURITY DEFINER.

    The same dialect split already exists in ``app.rls`` for the scope GUCs.
    This branch is the reason ``app/tenant_catalog.py`` appears in the
    cross-org caller inventory as a declared ``allow_cross_org`` owner: there is
    no database-side isolation to defer to on SQLite, so the ORM listener is the
    only layer present and it must be told this read is deliberate.
    """
    from app.db.session_context import allow_cross_org
    from app.models.finance.core_org.organization import Organization

    query = select(Organization.organization_id)
    if not include_inactive:
        query = query.where(Organization.is_active.is_(True))
    with allow_cross_org(session):
        return list(session.scalars(query.order_by(Organization.organization_id)).all())


def organization_ids(
    *,
    include_inactive: bool = False,
    only: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    """Return tenant identifiers, in a session that is closed before returning.

    Args:
        include_inactive: include deactivated organizations. Defaults to
            ``False``. Financial settlement paths (GL posting, AR allocation)
            pass ``True`` because a deactivated organization's open documents
            still have to settle.
        only: narrow to a single organization. Callers that accept an optional
            ``organization_id`` argument use this instead of re-deriving a
            filter, so a caller cannot accidentally widen to the whole fleet
            when the argument was meant to pin one tenant. An id that is absent
            from the catalog (or inactive, unless ``include_inactive``) yields
            an empty list rather than an unscoped run.

    The session opened here is closed before the ids are returned, so a caller
    cannot accidentally keep an unscoped session alive alongside the
    tenant-scoped sessions it is about to open.
    """
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            found = _discover_postgresql(session, include_inactive)
        else:
            found = _discover_sqlite(session, include_inactive)
    finally:
        session.close()

    if only is not None:
        return [org_id for org_id in found if org_id == only]
    return found


def active_organization_ids(*, only: uuid.UUID | None = None) -> list[uuid.UUID]:
    """Active organizations only — the default for scheduled work.

    A deactivated organization should not receive reminders, insights, or
    generated documents, so this is the spelling nearly every task wants.
    """
    return organization_ids(include_inactive=False, only=only)
