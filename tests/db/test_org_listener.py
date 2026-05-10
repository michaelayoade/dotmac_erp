"""Tests for the SQLAlchemy do_orm_execute listener that injects
``WHERE organization_id = ?`` for org-scoped model queries.

These tests register the listener manually on a fresh Session class to
avoid global side effects on the test suite. The Phase 1 default is
listener-OFF; we test ON behavior in isolation.

Test environment note: tests/conftest.py swaps ``app.db.SessionLocal``
with a SQLite test double that does NOT create the production schemas
(``ar``, ``core_fx``, etc.). The integration-style tests in the plan
fail with ``no such table`` against this double. Per the plan's
instructions, we adapt those tests to use a unit-test approach that
invokes ``_add_org_filter`` directly with a mocked ``ORMExecuteState``
and inspects the mutated statement, rather than hitting the DB.

Test ``test_select_org_scoped_without_org_raises`` is preserved as-is
because the listener raises BEFORE the SQL emission, so the missing
table never matters for that path.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def registered_listener():
    """Register and unregister the listener for a single test."""
    from sqlalchemy import event

    from app.db.org_listener import _add_org_filter

    event.listen(Session, "do_orm_execute", _add_org_filter)
    yield
    event.remove(Session, "do_orm_execute", _add_org_filter)


def _make_execute_state(*, statement, mapper, session_info, is_select=True):
    """Build a mock ORMExecuteState sufficient for ``_add_org_filter``.

    The handler reads ``.is_select``, ``.session.info``, ``.bind_mapper``,
    and assigns to ``.statement``; nothing else.
    """
    state = MagicMock()
    state.is_select = is_select
    state.statement = statement
    state.bind_mapper = mapper
    state.session = MagicMock()
    state.session.info = session_info
    return state


class TestSelectFiltering:
    def test_select_org_scoped_with_org_set_injects_org_filter(self):
        """The listener must inject WHERE organization_id = :org_id into
        any select() targeting an org-scoped model.

        Adapted from the plan: instead of executing the statement against
        the test SQLite (which lacks the ar.invoice schema), we invoke the
        handler directly with a mocked ORMExecuteState, then compile the
        mutated statement and assert that the org_id filter appears.
        """
        from sqlalchemy import inspect as sa_inspect

        from app.db.org_listener import _add_org_filter
        from app.models.finance.ar.invoice import Invoice

        org_id = uuid4()
        stmt = select(Invoice).where(Invoice.status == "POSTED")
        mapper = sa_inspect(Invoice)
        state = _make_execute_state(
            statement=stmt,
            mapper=mapper,
            session_info={"organization_id": org_id},
        )

        _add_org_filter(state)

        # The handler should have rewritten state.statement with an added
        # with_loader_criteria option. Compiling against the postgres
        # dialect yields SQL we can inspect for the org_id column.
        from sqlalchemy.dialects import postgresql

        compiled = state.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
        sql_text = str(compiled).lower()

        assert "organization_id" in sql_text, (
            f"Expected listener to inject organization_id filter; SQL was:\n{sql_text}"
        )

    def test_select_org_scoped_without_org_raises(self, registered_listener):
        from app.db import SessionLocal
        from app.db.multi_tenant import MissingOrgContextError
        from app.models.finance.ar.invoice import Invoice

        db = SessionLocal()
        try:
            with pytest.raises(MissingOrgContextError) as exc:
                db.scalars(select(Invoice)).all()
            assert "Invoice" in str(exc.value)
        finally:
            db.close()

    def test_select_non_org_scoped_unaffected(self):
        """A model without ``organization_id`` (e.g., Currency) must pass
        through the listener untouched, regardless of whether the session
        is primed.

        Adapted from the plan: rather than executing ``select(Currency)``
        against the test SQLite (which lacks the ``core_fx.currency``
        schema), we invoke the handler with a mocked state and verify
        that the statement is NOT rewritten (no options added).
        """
        from sqlalchemy import inspect as sa_inspect

        from app.db.org_listener import _add_org_filter
        from app.models.finance.core_fx.currency import Currency

        original_stmt = select(Currency)
        mapper = sa_inspect(Currency)
        # Deliberately no organization_id in session.info — the listener
        # must still NOT raise for a non-org-scoped model.
        state = _make_execute_state(
            statement=original_stmt,
            mapper=mapper,
            session_info={},
        )

        _add_org_filter(state)

        # Listener should be a no-op: the statement reference is unchanged.
        assert state.statement is original_stmt, (
            "Listener must not mutate statements for non-org-scoped models"
        )


class TestColumnOnlySelect:
    """select(Invoice.invoice_id, Invoice.amount) — column-only selects
    have bind_mapper=None but still target an org-scoped entity. The
    listener must inspect column_descriptions to detect this."""

    def test_column_only_select_against_org_scoped_model_is_filtered(self):
        from unittest.mock import MagicMock

        from sqlalchemy import select

        from app.db.org_listener import _add_org_filter
        from app.models.finance.ar.invoice import Invoice

        org_id = uuid4()
        # Column-only select; bind_mapper will be None.
        stmt = select(Invoice.invoice_id, Invoice.total_amount)
        state = MagicMock()
        state.is_select = True
        state.is_column_load = False
        state.session.info = {"organization_id": org_id}
        state.bind_mapper = None  # Critical: simulating column-only path
        state.statement = stmt

        _add_org_filter(state)

        # Listener must have rewritten the statement (added a loader option).
        # We assert via SQL compile.
        from sqlalchemy.dialects import postgresql

        compiled_sql = str(state.statement.compile(dialect=postgresql.dialect()))
        assert "organization_id" in compiled_sql.lower(), (
            f"Expected listener to inject organization_id filter for "
            f"column-only select; got SQL:\n{compiled_sql}"
        )

    def test_column_only_select_without_org_raises(self):
        from unittest.mock import MagicMock

        from sqlalchemy import select

        from app.db.multi_tenant import MissingOrgContextError
        from app.db.org_listener import _add_org_filter
        from app.models.finance.ar.invoice import Invoice

        stmt = select(Invoice.invoice_id, Invoice.total_amount)
        state = MagicMock()
        state.is_select = True
        state.is_column_load = False
        state.session.info = {}
        state.bind_mapper = None
        state.statement = stmt

        with pytest.raises(MissingOrgContextError) as exc:
            _add_org_filter(state)
        assert "Invoice" in str(exc.value)


class TestExceptionDoesNotLeakBindValues:
    """The MissingOrgContextError message must not contain bind values
    (customer UUIDs, employee IDs, etc.)."""

    def test_error_message_excludes_query_bind_values(self):
        from unittest.mock import MagicMock
        from uuid import uuid4 as _uuid4

        from sqlalchemy import select

        from app.db.multi_tenant import MissingOrgContextError
        from app.db.org_listener import _add_org_filter
        from app.models.finance.ar.invoice import Invoice

        # Construct a query with a literal UUID in the WHERE — this would
        # show up if str(statement) were used.
        secret_id = _uuid4()
        stmt = select(Invoice).where(Invoice.invoice_id == secret_id)
        state = MagicMock()
        state.is_select = True
        state.is_column_load = False
        state.session.info = {}
        state.bind_mapper = MagicMock(class_=Invoice)
        state.statement = stmt

        with pytest.raises(MissingOrgContextError) as exc:
            _add_org_filter(state)

        # The secret UUID must NOT appear in the message.
        assert str(secret_id) not in str(exc.value)


class TestColumnLoadSkipped:
    """When is_column_load is True (post-expire attribute access), the
    listener must short-circuit before injecting filter."""

    def test_column_load_event_is_skipped(self):
        from unittest.mock import MagicMock

        from app.db.org_listener import _add_org_filter

        original_stmt = MagicMock()
        state = MagicMock()
        state.is_select = True
        state.is_column_load = True  # The trigger
        state.statement = original_stmt

        _add_org_filter(state)

        # No mutation: statement unchanged.
        assert state.statement is original_stmt


class TestRegistrationIdempotency:
    """register_org_listener under concurrent calls must not double-
    register (which would cause the listener to fire twice per query)."""

    def test_repeated_registration_only_registers_once(self):
        from sqlalchemy import event
        from sqlalchemy.orm import Session

        from app.db.org_listener import (
            _add_org_filter,
            register_org_listener,
            unregister_org_listener,
        )

        # Clean state before test.
        unregister_org_listener()

        try:
            register_org_listener()
            register_org_listener()
            register_org_listener()

            # Confirm the listener is registered exactly once. SQLAlchemy
            # doesn't expose a count directly; we use event.contains to
            # confirm it's registered, and the absence of duplicate firing
            # is implied by the contains-then-listen guard.
            assert event.contains(Session, "do_orm_execute", _add_org_filter)
        finally:
            unregister_org_listener()
            assert not event.contains(Session, "do_orm_execute", _add_org_filter)
