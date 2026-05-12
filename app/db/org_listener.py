"""SQLAlchemy do_orm_execute listener for multi-tenant org filtering.

When enabled (gated by ``settings.enforce_org_filter``), this listener
intercepts every ORM SELECT (including ``Session.get()``) and:

- Skips queries where ``session.info['allow_cross_org']`` is True
- Skips queries against models without an ``organization_id`` column
- Skips queries against deny-listed models (e.g., Organization itself)
- Raises ``MissingOrgContextError`` when ``session.info['organization_id']``
  is not set
- Otherwise, injects ``WHERE organization_id = :org_id`` via
  ``with_loader_criteria`` so it composes with user-supplied filters,
  joinedload/selectinload, and Session.get() PK lookups.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.db.multi_tenant import MissingOrgContextError, is_org_scoped

logger = logging.getLogger(__name__)

_registration_lock = threading.Lock()


def _add_org_filter(orm_execute_state) -> None:
    """do_orm_execute event handler. See module docstring for behavior."""
    # Bypass non-SELECT statements; UPDATE/DELETE filtering is out of scope
    # for Phase 1 (see spec section 'Out of scope').
    if not orm_execute_state.is_select:
        return

    # Skip column-load events (firing when accessing an expired attribute
    # triggers a sub-select). Mutating column-load statements with
    # with_loader_criteria is untested SA behavior; the parent query's
    # filter already constrained the row, so the column-load is safely
    # scoped without further injection.
    if orm_execute_state.is_column_load:
        return

    session = orm_execute_state.session

    # Cross-org bypass: caller wrapped the query in `with allow_cross_org(...)`.
    if session.info.get("allow_cross_org"):
        return

    # Identify the target model class. bind_mapper is set for entity-shaped
    # selects (select(Invoice)) but is None for column-only tuple selects
    # (select(Invoice.invoice_id, Invoice.amount)) which would otherwise
    # bypass the listener silently — the column-only path is common in
    # aging reports and lightweight reads.
    mapper = orm_execute_state.bind_mapper
    if mapper is not None:
        target_class = mapper.class_
    else:
        target_class = None
        for desc in orm_execute_state.statement.column_descriptions:
            entity = desc.get("entity")
            if entity is not None and is_org_scoped(entity):
                target_class = entity
                break
        if target_class is None:
            # No mapped org-scoped entity in this statement (e.g., genuinely
            # non-ORM, or selecting from non-org-scoped models only). Pass.
            return

    # Skip non-org-scoped models (no organization_id column or deny-listed).
    if not is_org_scoped(target_class):
        return

    org_id = session.info.get("organization_id")
    if org_id is None:
        # Use only class names — never the rendered SQL — to avoid leaking
        # bind values (customer UUIDs, employee IDs, etc.) into log lines.
        # The class names alone are enough to localize the offending site.
        statement_kind = type(orm_execute_state.statement).__name__
        raise MissingOrgContextError(
            target_class.__name__,
            query_repr=f"<{statement_kind} targeting {target_class.__name__}>",
        )

    # Inject the org filter via with_loader_criteria. include_aliases=True
    # ensures the filter applies even when the model is referenced via an
    # alias (e.g., joinedload subqueries). Capture org_id by value, not by
    # reference, so each query gets the org_id current at execute time.
    def _filter(cls, _org_id=org_id):  # type: ignore[no-untyped-def]
        return cls.organization_id == _org_id

    orm_execute_state.statement = orm_execute_state.statement.options(
        with_loader_criteria(
            target_class,
            _filter,
            include_aliases=True,
        )
    )


def register_org_listener() -> None:
    """Idempotently register ``_add_org_filter`` on the Session class.

    Called from app startup gated by ``settings.enforce_org_filter``.
    Safe to call concurrently — uses a module-level lock to avoid the
    contains+listen race window.
    """
    with _registration_lock:
        if event.contains(Session, "do_orm_execute", _add_org_filter):
            logger.debug("Org-filter listener already registered; skipping")
            return
        event.listen(Session, "do_orm_execute", _add_org_filter)
        logger.debug("Org-filter listener registered on Session.do_orm_execute")


def unregister_org_listener() -> None:
    """Remove the listener. Used by tests that need a clean teardown."""
    with _registration_lock:
        if event.contains(Session, "do_orm_execute", _add_org_filter):
            event.remove(Session, "do_orm_execute", _add_org_filter)
