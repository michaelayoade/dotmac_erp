"""Session context primitives for multi-tenant scoping.

This codebase has **two independent layers** that filter queries by org:

1. The SQLAlchemy ORM listener — reads ``session.info["organization_id"]``
   and injects ``WHERE organization_id = :org`` into ORM queries. Set by
   :func:`prime_session`.
2. PostgreSQL native RLS policies — read the ``app.current_organization_id``
   GUC (``current_setting('app.current_organization_id')``) and filter at
   the database layer. Set by :func:`app.rls.set_current_organization_sync`.

**Production code that opens a session MUST set both.** Setting only one is
a silent bug: queries either filter to zero rows under DB-RLS (if enabled
on the target schema) or skip filtering under the ORM listener (if not).
This is the same hazard that has hit Celery tasks repeatedly — a session
opened for "an org" that doesn't actually scope its queries to that org.

Public API for callers
----------------------

Use the high-level context managers — they set both layers and clean up:

- :func:`session_for_org` — single-org tenant session (Celery tasks,
  CLI scripts, anything outside a web request).
- :func:`cross_org_session` — explicit cross-tenant access for global
  batch jobs (e.g. "list every org with pending work").

The low-level primitives below (:func:`prime_session`,
:func:`allow_cross_org`) exist for infrastructure code only — the web
dependency at ``app/api/deps.py::get_db_for_org`` composes them with the
RLS helpers because it owns the session-lifecycle contract for HTTP
requests. New code should not compose primitives manually.

A note on ``SET LOCAL`` and commits
------------------------------------

``SET LOCAL app.current_organization_id = ...`` is **transaction-scoped**
— it is reset at COMMIT and ROLLBACK. A session that commits in the
middle of its work loses its RLS GUC and silently starts returning zero
rows on the next query. The strongest pattern is therefore one tenant
session per org, commit once at the end:

    for org_id in org_ids:
        with session_for_org(org_id) as db:
            service.run()
            db.commit()

Avoid commit-and-continue within a single ``session_for_org`` block. If
truly necessary, re-prime explicitly after each commit (call site
becomes the contract owner — the helpers can't catch this for you).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy.orm import Session

from app.rls import (
    bypass_rls_sync,
    set_current_organization_sync,
)


def prime_session(session: Session, organization_id: UUID) -> None:
    """Set ``session.info["organization_id"]`` for the ORM-listener layer.

    .. warning::

       This sets ONLY the SQLAlchemy listener half of tenant scoping. It
       does NOT set the PostgreSQL ``app.current_organization_id`` GUC
       used by native RLS policies. Calling this alone leaves DB-level
       RLS unprimed, which on RLS-enabled schemas (``expense``, ``payroll``,
       ``recruit``, ``training``, ``ipsas``, and any future additions)
       causes silent zero-row reads.

       **Do not use this as a Celery task entry-point helper.** Use
       :func:`session_for_org` instead — it sets both layers.

       Direct callers are limited to infrastructure code that already
       composes both layers explicitly (notably ``app.api.deps.get_db_for_org``
       and ``app.web.deps``). Application code should not import this.

    Calling on an already-primed session overwrites the previous value —
    useful for tasks that iterate orgs *within a single session*, though
    one-session-per-org (via :func:`session_for_org`) is preferred.
    """
    session.info["organization_id"] = organization_id


@contextmanager
def allow_cross_org(session: Session) -> Iterator[None]:
    """Temporarily bypass the SQLAlchemy ORM-listener org filter.

    .. warning::

       Like :func:`prime_session`, this bypasses ONLY the ORM listener
       layer. It does NOT bypass PostgreSQL RLS policies. Use
       :func:`cross_org_session` for genuine cross-tenant work — it
       bypasses both layers and opens a dedicated session.

       Direct use is reserved for infrastructure code that composes both
       layers (web admin-bypass dependencies, audit listeners that
       deliberately re-pin context).

    Restores prior state in ``finally`` so an exception inside the block
    does not leak the bypass. Nested usage preserves outer state.
    """
    prior = session.info.get("allow_cross_org", False)
    session.info["allow_cross_org"] = True
    try:
        yield
    finally:
        session.info["allow_cross_org"] = prior


def prime_tenant_context(session: Session, organization_id: UUID) -> None:
    """Set both tenant layers on an *existing* session.

    Use this when a request opens unprimed (the org isn't known at
    request entry — e.g., a public portal route resolving an org from a
    URL slug, or an onboarding portal resolving from a token) and the
    service has just looked the org up under :func:`allow_cross_org` /
    :func:`app.rls.bypass_rls_sync`. After the lookup, retroactively
    prime the session for the rest of the work:

        # Route opens unprimed because slug → org_id isn't known yet
        @router.get("/{org_slug}/jobs")
        def list_jobs(org_slug: str, db: Session = Depends(get_db)):
            org = service.resolve_org_from_slug(org_slug)  # bypass internally
            # ... lookup complete; switch the session to tenant scope:
            prime_tenant_context(db, org.organization_id)
            return service.list_jobs(org.organization_id)

    This composes :func:`prime_session` (ORM listener via
    ``session.info``) and :func:`app.rls.set_current_organization_sync`
    (PostgreSQL GUC) so callers can't forget one layer — the same
    dual-layer guarantee that :func:`session_for_org` and
    :func:`app.api.deps.get_db_with_org` provide for the
    open-already-primed flow.

    .. warning::

       Only use this for the open-unprimed-then-resolve pattern.
       New Celery tasks should use :func:`session_for_org`; new HTTP
       routes should use :func:`app.api.deps.get_db_with_org` (API) or
       :func:`app.web.deps.get_db_for_org` (web). This helper exists
       for the legitimately-public-with-resolved-org case only.
    """
    prime_session(session, organization_id)
    if session.get_bind().dialect.name == "postgresql":
        set_current_organization_sync(session, organization_id)


@contextmanager
def session_for_org(organization_id: UUID) -> Iterator[Session]:
    """Canonical tenant-scoped session for non-HTTP entry points.

    Opens a fresh ``SessionLocal``, sets **both** tenant-context layers
    (ORM listener via :func:`prime_session` and PostgreSQL GUC via
    ``set_current_organization_sync``), yields the session, and closes
    it on exit (even on exception).

    Use this in every Celery task, CLI script, scheduled job, or other
    non-HTTP entry point that operates on a single org's data::

        @shared_task
        def process_payroll(org_id: str) -> dict:
            with session_for_org(UUID(org_id)) as db:
                PayrollService(db).run()
                db.commit()
                return {"ok": True}

    For tasks that span multiple organizations, open one session per org
    in the loop — this avoids ``SET LOCAL`` being cleared on commit and
    prevents identity-map contamination across tenants::

        for org_id in org_ids:
            with session_for_org(org_id) as db:
                Service(db).run()
                db.commit()
    """
    # Local import: SessionLocal is at module top-level of app.db; importing
    # it here avoids a circular dependency at import time.
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        # Layer 1: ORM listener — filters ORM queries based on session.info
        prime_session(session, organization_id)
        # Layer 2: PostgreSQL GUC — filters DB-RLS-policy-protected tables.
        # SET LOCAL is transaction-scoped, so a commit inside the with-block
        # will silently un-set this. Per-org sessions sidestep that.
        set_current_organization_sync(session, organization_id)
        yield session
    finally:
        session.close()


@contextmanager
def cross_org_session() -> Iterator[Session]:
    """Canonical session for genuinely cross-tenant work (admin/batch).

    Opens a fresh ``SessionLocal`` with **both** bypass layers active —
    ``allow_cross_org`` for the ORM listener and ``bypass_rls_sync`` for
    PostgreSQL RLS. Use this when a task needs to list rows across every
    organization, then process them under per-org sessions::

        with cross_org_session() as cross_db:
            org_ids = list(cross_db.scalars(select(Organization.id)).all())
        for org_id in org_ids:
            with session_for_org(org_id) as db:
                ...

    Don't reuse a ``cross_org_session`` for per-org work — switching
    contexts mid-session is the bug class this helper exists to prevent.
    """
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        # ORM listener bypass — session.info marker the listener checks.
        session.info["allow_cross_org"] = True
        # PostgreSQL RLS bypass — SET LOCAL app.bypass_rls = 'true'.
        with bypass_rls_sync(session):
            yield session
    finally:
        session.info["allow_cross_org"] = False
        session.close()
