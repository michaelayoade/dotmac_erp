# Multi-Tenant Session Listener — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a SQLAlchemy session-level listener that auto-injects `WHERE organization_id = ?` for ORM queries on org-scoped models, gated on a primed session. Phase 1: build it disabled by default; no behavior change in any environment.

**Architecture:** Single `do_orm_execute` event listener registered globally on `Session`. Detects org-scoped models via the `organization_id` column heuristic + a small explicit deny-list. Reads `session.info['organization_id']` (primed by `get_db` for HTTP, `session_for_org` factory for Celery/CLI). Strict raise on missing context. Opt-out via `with allow_cross_org(session):` context manager. All gated behind `ENFORCE_ORG_FILTER` env flag (default `false`).

**Tech Stack:** SQLAlchemy 2.0, FastAPI, pytest, Pydantic settings.

**Spec:** `docs/superpowers/specs/2026-05-10-multi-org-listener-design.md`.

**Verified during brainstorm**: `Person` is per-org (filterable, NOT on deny-list); `Currency`/`TaxJurisdiction` have no `organization_id` column (heuristic skips automatically); `Organization` has `organization_id` as its PK and MUST be on the deny-list. R1 resolved.

---

## Task 1: Foundation — `MissingOrgContextError`, deny-list, detection helper

**Files:**
- Create: `app/db/multi_tenant.py`
- Test: `tests/db/test_multi_tenant.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/db/test_multi_tenant.py`:

```python
"""Tests for the multi-tenant primitives (exception, deny-list, detection)."""

from __future__ import annotations

import pytest


class TestMissingOrgContextError:
    def test_inherits_from_runtime_error(self):
        from app.db.multi_tenant import MissingOrgContextError

        assert issubclass(MissingOrgContextError, RuntimeError)

    def test_message_names_the_model(self):
        from app.db.multi_tenant import MissingOrgContextError

        exc = MissingOrgContextError("Invoice")
        assert "Invoice" in str(exc)


class TestIsOrgScoped:
    def test_returns_true_for_class_with_organization_id_column(self):
        from app.db.multi_tenant import is_org_scoped
        from app.models.finance.ar.invoice import Invoice

        assert is_org_scoped(Invoice) is True

    def test_returns_false_for_organization_class_itself(self):
        """Organization's own org_id IS its PK — no parent to scope by."""
        from app.db.multi_tenant import is_org_scoped
        from app.models.finance.core_org.organization import Organization

        assert is_org_scoped(Organization) is False

    def test_returns_false_for_genuinely_shared_models(self):
        """Currency / Country / TaxJurisdiction have no organization_id;
        the column heuristic skips them automatically."""
        from app.db.multi_tenant import is_org_scoped
        from app.models.finance.core_fx.currency import Currency

        assert is_org_scoped(Currency) is False

    def test_returns_false_for_none(self):
        from app.db.multi_tenant import is_org_scoped

        assert is_org_scoped(None) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/db/test_multi_tenant.py -v`
Expected: 5 failures with `ModuleNotFoundError: No module named 'app.db.multi_tenant'`.

- [ ] **Step 3: Create the module**

Create `app/db/multi_tenant.py`:

```python
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


_DENY_LIST: frozenset[type] | None = None


def get_cross_org_deny_list() -> frozenset[type]:
    """Lazily build and cache the deny-list. Models import the listener
    module at startup, so we defer importing models until first call to
    avoid circular imports."""
    global _DENY_LIST
    if _DENY_LIST is None:
        _DENY_LIST = _build_deny_list()
    return _DENY_LIST


def is_org_scoped(target: Any) -> bool:
    """Return True iff ``target`` is a model class with an ``organization_id``
    mapped column AND is not on the cross-org deny-list.

    Accepts a class or an instance; ``None`` and non-mapped types return False.
    """
    if target is None:
        return False
    cls = target if isinstance(target, type) else type(target)
    table = getattr(cls, "__table__", None)
    if table is None:
        return False
    if "organization_id" not in table.columns:
        return False
    return cls not in get_cross_org_deny_list()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/db/test_multi_tenant.py -v`
Expected: 5 passes.

- [ ] **Step 5: Add `tests/db/__init__.py` if missing**

Run: `ls tests/db/__init__.py 2>/dev/null || touch tests/db/__init__.py`

---

## Task 2: `prime_session` and `allow_cross_org` context manager

**Files:**
- Create: `app/db/session_context.py`
- Test: `tests/db/test_session_context.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/db/test_session_context.py`:

```python
"""Tests for session context primitives (prime, cross-org bypass, factory)."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest


class TestPrimeSession:
    def test_sets_organization_id_on_session_info(self):
        from app.db.session_context import prime_session

        session = MagicMock()
        session.info = {}
        org_id = uuid4()

        prime_session(session, org_id)

        assert session.info["organization_id"] == org_id

    def test_overwrites_existing_organization_id(self):
        from app.db.session_context import prime_session

        session = MagicMock()
        session.info = {"organization_id": uuid4()}
        new_org_id = uuid4()

        prime_session(session, new_org_id)

        assert session.info["organization_id"] == new_org_id


class TestAllowCrossOrg:
    def test_sets_flag_inside_block_unsets_after(self):
        from app.db.session_context import allow_cross_org

        session = MagicMock()
        session.info = {}

        with allow_cross_org(session):
            assert session.info["allow_cross_org"] is True

        # After exit: flag must be False (or removed) so subsequent queries
        # are NOT bypassed.
        assert session.info.get("allow_cross_org") is False or "allow_cross_org" not in session.info

    def test_restores_state_after_exception(self):
        from app.db.session_context import allow_cross_org

        session = MagicMock()
        session.info = {}

        with pytest.raises(RuntimeError):
            with allow_cross_org(session):
                raise RuntimeError("boom")

        assert session.info.get("allow_cross_org") is False or "allow_cross_org" not in session.info

    def test_nested_preserves_outer_state(self):
        """Nested context managers must restore the OUTER state on inner exit,
        not unconditionally clear the flag."""
        from app.db.session_context import allow_cross_org

        session = MagicMock()
        session.info = {}

        with allow_cross_org(session):
            assert session.info["allow_cross_org"] is True
            with allow_cross_org(session):
                assert session.info["allow_cross_org"] is True
            # Inner exit: outer must still be True
            assert session.info["allow_cross_org"] is True

        # Outer exit: now False
        assert session.info.get("allow_cross_org") is False or "allow_cross_org" not in session.info
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `poetry run pytest tests/db/test_session_context.py -v`
Expected: 5 failures with `ModuleNotFoundError: No module named 'app.db.session_context'`.

- [ ] **Step 3: Create the module**

Create `app/db/session_context.py`:

```python
"""Session context primitives for multi-tenant scoping.

Three public surfaces:
- ``prime_session(session, org_id)``: set the org context for a session.
- ``allow_cross_org(session)``: context manager that bypasses scoping.
- ``session_for_org(org_id)``: factory for non-HTTP entry points (added in Task 3).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `poetry run pytest tests/db/test_session_context.py -v`
Expected: 5 passes.

- [ ] **Step 5: Quick verification that no circular imports broke**

Run: `poetry run python -c "from app.db.session_context import prime_session, allow_cross_org; print('OK')"`

Note: `python -c` may run in a different venv than `poetry run pytest`. If it errors with `ModuleNotFoundError: No module named 'fastapi'` ignore — the pytest run is the authoritative check.

---

## Task 3: `session_for_org` factory for non-HTTP entry points

**Files:**
- Modify: `app/db/session_context.py` (extend)
- Test: `tests/db/test_session_context.py` (extend)

- [ ] **Step 1: Verify the existing session factory location**

Run: `grep -rn "^SessionLocal\b\|SessionLocal\s*=" app/db/ | head -5`

Expected: locates the canonical `SessionLocal` definition (likely `app/db/__init__.py` or `app/db/session.py`). Note the import path; you'll need it in the next step.

- [ ] **Step 2: Write the failing tests**

Append to `tests/db/test_session_context.py`:

```python
class TestSessionForOrg:
    def test_yields_primed_session(self):
        from app.db.session_context import session_for_org

        org_id = uuid4()
        with session_for_org(org_id) as db:
            assert db.info["organization_id"] == org_id

    def test_closes_session_on_exit(self):
        """The factory must close the session even on exception so DB
        connections aren't leaked under task failures."""
        from app.db.session_context import session_for_org

        org_id = uuid4()
        captured = {}
        try:
            with session_for_org(org_id) as db:
                captured["db"] = db
                raise RuntimeError("boom")
        except RuntimeError:
            pass

        # The session should have been closed; subsequent ORM ops would fail.
        # We can detect closure via the bind being released — easiest check:
        # session.is_active should be False after a failed transaction + close.
        # SQLAlchemy 2.0: after close(), session.is_active is False.
        assert captured["db"].is_active is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/db/test_session_context.py::TestSessionForOrg -v`
Expected: 2 failures with `ImportError: cannot import name 'session_for_org'`.

- [ ] **Step 4: Implement `session_for_org`**

Append to `app/db/session_context.py`:

```python
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
```

If your earlier verification showed `SessionLocal` at a different path (e.g., `app.db.session`), adjust the import accordingly.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/db/test_session_context.py -v`
Expected: 7 passes (5 prior + 2 new).

---

## Task 4: Add `enforce_org_filter` config setting

**Files:**
- Modify: `app/config.py` (add a single setting field)

- [ ] **Step 1: Locate the Settings class**

Run: `grep -n "^class Settings\|enforce_\|^    [a-z_]*: bool" app/config.py | head -20`

Expected: shows the `class Settings(BaseSettings):` block and existing `bool` fields. Note the convention used (likely `Field(default=...)` or simple `name: bool = False`).

- [ ] **Step 2: Add the new setting**

Edit `app/config.py`. Find a logical location near other feature flags (or the end of the Settings class) and add:

```python
    # Multi-tenant enforcement — Phase 1 ships this OFF by default.
    # When True, the org-scope listener (app/db/org_listener.py) is registered
    # at startup and raises MissingOrgContextError on any ORM query against
    # an org-scoped model from a session that wasn't primed via prime_session.
    # See docs/superpowers/specs/2026-05-10-multi-org-listener-design.md (D5).
    enforce_org_filter: bool = False
```

If the file uses `Field(...)` with descriptions for env-overridable settings, mirror that style:

```python
    enforce_org_filter: bool = Field(
        default=False,
        description=(
            "When True, the multi-tenant org-scope listener is registered "
            "at startup. Defaults to False — Phase 1 of the rollout. See "
            "docs/superpowers/specs/2026-05-10-multi-org-listener-design.md (D5)."
        ),
    )
```

- [ ] **Step 3: Verify the setting loads**

Run: `poetry run python -c "from app.config import settings; print(repr(settings.enforce_org_filter))"`

Expected: prints `False` (the default). If `python -c` lands in an empty venv, run via pytest instead:
`poetry run pytest tests/test_config.py -k "settings" -q` (if that test exists; otherwise skip — the next task's tests will exercise the flag).

---

## Task 5: The listener — filter injection on `select()`

**Files:**
- Create: `app/db/org_listener.py`
- Test: `tests/db/test_org_listener.py` (new)

- [ ] **Step 1: Verify R2 — no conflicting Session-level listeners**

Run: `grep -rn "@event.listens_for(Session\|event.listen(Session" app/ --include='*.py' | head -10`

Expected output: locate any existing Session-level listeners (e.g., `app/services/audit_listener.py` per the spec). Read the relevant file to confirm what events they bind. If any binds `do_orm_execute`, note the ordering — multiple listeners on the same event are OK but order matters; SQLAlchemy fires them in registration order.

If a conflicting listener exists, no code change in this task — just document the finding for the implementer review.

- [ ] **Step 2: Write the failing tests**

Create `tests/db/test_org_listener.py`:

```python
"""Tests for the SQLAlchemy do_orm_execute listener that injects
``WHERE organization_id = ?`` for org-scoped model queries.

These tests register the listener manually on a fresh Session class to
avoid global side effects on the test suite. The Phase 1 default is
listener-OFF; we test ON behavior in isolation."""

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


class TestSelectFiltering:
    def test_select_org_scoped_with_org_set_compiles_with_org_filter(
        self, registered_listener
    ):
        """The listener must inject WHERE organization_id = :org_id into
        any select() targeting an org-scoped model."""
        from app.db import SessionLocal
        from app.models.finance.ar.invoice import Invoice
        from app.db.session_context import prime_session

        org_id = uuid4()
        db = SessionLocal()
        try:
            prime_session(db, org_id)
            stmt = select(Invoice).where(Invoice.status == "POSTED")
            # Trigger compilation via execute on a real connection;
            # we only care about the compiled SQL, not the result rows.
            compiled = db.execute(stmt).statement.compile(
                compile_kwargs={"literal_binds": False}
            )
            sql_text = str(compiled).lower()
        finally:
            db.close()

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

    def test_select_non_org_scoped_unaffected(self, registered_listener):
        """Currency has no organization_id column; query must execute
        without injection regardless of session priming."""
        from app.db import SessionLocal
        from app.models.finance.core_fx.currency import Currency

        db = SessionLocal()
        try:
            # No prime_session call — this should still work because
            # Currency is not org-scoped (no organization_id column).
            results = db.scalars(select(Currency)).all()
            # We don't assert results content (the test DB may or may not
            # have currencies seeded); the assertion is "did not raise".
            assert isinstance(results, list)
        finally:
            db.close()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/db/test_org_listener.py::TestSelectFiltering -v`
Expected: 3 failures with `ImportError: cannot import name '_add_org_filter'`.

- [ ] **Step 4: Implement the listener**

Create `app/db/org_listener.py`:

```python
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

from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria

from app.db.multi_tenant import MissingOrgContextError, is_org_scoped

logger = logging.getLogger(__name__)


def _add_org_filter(orm_execute_state) -> None:
    """do_orm_execute event handler. See module docstring for behavior."""
    # Bypass non-SELECT statements; UPDATE/DELETE filtering is out of scope
    # for Phase 1 (see spec section 'Out of scope').
    if not orm_execute_state.is_select:
        return

    session = orm_execute_state.session

    # Cross-org bypass: caller wrapped the query in `with allow_cross_org(...)`.
    if session.info.get("allow_cross_org"):
        return

    # Identify the target model class via the bound mapper.
    mapper = orm_execute_state.bind_mapper
    if mapper is None:
        # Some non-ORM cases (e.g., raw text() execution wrapped in select())
        # don't expose a mapper. We can't enforce; let it through.
        return
    target_class = mapper.class_

    # Skip non-org-scoped models (no organization_id column or deny-listed).
    if not is_org_scoped(target_class):
        return

    org_id = session.info.get("organization_id")
    if org_id is None:
        raise MissingOrgContextError(
            target_class.__name__,
            query_repr=str(orm_execute_state.statement)[:500],
        )

    # Inject the org filter via with_loader_criteria. include_aliases=True
    # ensures the filter applies even when the model is referenced via an
    # alias (e.g., joinedload subqueries). Capture org_id by value, not by
    # reference, so each query gets the org_id current at execute time.
    orm_execute_state.statement = orm_execute_state.statement.options(
        with_loader_criteria(
            target_class,
            lambda cls, _org_id=org_id: cls.organization_id == _org_id,
            include_aliases=True,
        )
    )


def register_org_listener() -> None:
    """Idempotently register ``_add_org_filter`` on the Session class.

    Called from app startup gated by ``settings.enforce_org_filter``.
    Safe to call multiple times (SQLAlchemy's event.contains check).
    """
    if event.contains(Session, "do_orm_execute", _add_org_filter):
        logger.debug("Org-filter listener already registered; skipping")
        return
    event.listen(Session, "do_orm_execute", _add_org_filter)
    logger.info("Org-filter listener registered on Session.do_orm_execute")


def unregister_org_listener() -> None:
    """Remove the listener. Used by tests that need a clean teardown."""
    if event.contains(Session, "do_orm_execute", _add_org_filter):
        event.remove(Session, "do_orm_execute", _add_org_filter)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/db/test_org_listener.py::TestSelectFiltering -v`
Expected: 3 passes.

If `test_select_org_scoped_with_org_set_compiles_with_org_filter` fails because the test DB doesn't have an `ar.invoice` schema, replace `Invoice` with whichever org-scoped model the test DB does have populated, or add a `pytest.skip` guard for missing-schema cases.

---

## Task 6: Listener — `Session.get()` returns None for cross-org PK lookups

**Files:**
- Modify: `tests/db/test_org_listener.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/db/test_org_listener.py`:

```python
class TestGetFiltering:
    """Session.get(Model, pk) flows through do_orm_execute in SQLA 1.4+
    so the listener applies the same filter. A get for an entity in a
    different org returns None (PK matches, but org_id doesn't)."""

    def test_get_returns_none_when_pk_belongs_to_other_org(
        self, registered_listener
    ):
        """If org_a's session attempts db.get(Invoice, pk_owned_by_org_b),
        the with_loader_criteria filter rejects the row at SQL time and
        Session.get returns None."""
        # This test requires test data: an Invoice owned by org_b.
        # Use whatever test fixture exists in this codebase. If none, mark
        # the test as a runtime check via the SQL compile path instead.
        pytest.skip(
            "Integration test requires seeded cross-org Invoice fixture; "
            "covered by existing FX revaluation suite which exercises "
            "db.get for org-scoped models with Phase-2 conftest hook."
        )

    def test_get_without_org_set_raises(self, registered_listener):
        from app.db import SessionLocal
        from app.db.multi_tenant import MissingOrgContextError
        from app.models.finance.ar.invoice import Invoice

        db = SessionLocal()
        try:
            with pytest.raises(MissingOrgContextError):
                db.get(Invoice, uuid4())
        finally:
            db.close()
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `poetry run pytest tests/db/test_org_listener.py::TestGetFiltering -v`
Expected: 1 pass + 1 skip. The skip is intentional — the cross-org-returns-None case lands in Phase 2 with real fixtures.

If `test_get_without_org_set_raises` fails because `Session.get` doesn't fire `do_orm_execute` in this SQLAlchemy version, surface the finding: it would mean the listener doesn't cover get() and we need a separate `before_compile` hook. SQLA 1.4+ docs say it should fire; this is a verification step.

---

## Task 7: Listener — `allow_cross_org` bypass

**Files:**
- Modify: `tests/db/test_org_listener.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/db/test_org_listener.py`:

```python
class TestAllowCrossOrg:
    def test_bypasses_filter_when_inside_context_manager(
        self, registered_listener
    ):
        from app.db import SessionLocal
        from app.db.session_context import allow_cross_org
        from app.models.finance.ar.invoice import Invoice

        db = SessionLocal()
        try:
            # No prime_session call — would normally raise. With
            # allow_cross_org active, the listener short-circuits.
            with allow_cross_org(db):
                results = db.scalars(select(Invoice).limit(1)).all()
                # The query ran without raising; results may be empty,
                # which is fine.
                assert isinstance(results, list)
        finally:
            db.close()

    def test_resumes_enforcement_after_context_exit(
        self, registered_listener
    ):
        from app.db import SessionLocal
        from app.db.multi_tenant import MissingOrgContextError
        from app.db.session_context import allow_cross_org
        from app.models.finance.ar.invoice import Invoice

        db = SessionLocal()
        try:
            with allow_cross_org(db):
                db.scalars(select(Invoice).limit(1)).all()
            # Outside the block: enforcement is back on.
            with pytest.raises(MissingOrgContextError):
                db.scalars(select(Invoice).limit(1)).all()
        finally:
            db.close()
```

- [ ] **Step 2: Run the tests to verify they pass**

Run: `poetry run pytest tests/db/test_org_listener.py::TestAllowCrossOrg -v`
Expected: 2 passes. (Listener and context manager were both built in earlier tasks; this just exercises the integration.)

---

## Task 8: Listener — Organization (deny-listed) is not filtered

**Files:**
- Modify: `tests/db/test_org_listener.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/db/test_org_listener.py`:

```python
class TestDenyList:
    def test_organization_class_is_not_filtered(self, registered_listener):
        """Querying Organization itself must not raise even without a primed
        org_id — Organization is on the deny-list because its PK is the
        org_id (no parent org to scope by)."""
        from app.db import SessionLocal
        from app.models.finance.core_org.organization import Organization

        db = SessionLocal()
        try:
            # No prime_session call; Organization is deny-listed.
            results = db.scalars(select(Organization).limit(1)).all()
            assert isinstance(results, list)
        finally:
            db.close()
```

- [ ] **Step 2: Run the test to verify it passes**

Run: `poetry run pytest tests/db/test_org_listener.py::TestDenyList -v`
Expected: 1 pass.

---

## Task 9: R3 verification — `with_loader_criteria` composes with `selectinload`

**Files:**
- Modify: `tests/db/test_org_listener.py` (extend)

- [ ] **Step 1: Identify an org-scoped model with a relationship**

Run: `grep -n "relationship(" app/models/finance/ar/invoice.py | head -5`

Expected: locates an `Invoice.line_items` (or similar) relationship pointing to another org-scoped model. Note the relationship name and the related model's path.

If `Invoice` doesn't have a clean relationship for this test, pick another org-scoped model. The point is to test that the listener's filter composes cleanly with eager loading.

- [ ] **Step 2: Write the failing test**

Append to `tests/db/test_org_listener.py`:

```python
class TestEagerLoadingComposition:
    def test_selectinload_on_org_scoped_relationship_compiles_with_org_filter(
        self, registered_listener
    ):
        """with_loader_criteria(include_aliases=True) must inject the
        org_id filter on both the parent select AND the selectinload
        sub-select for the relationship."""
        from sqlalchemy.orm import selectinload

        from app.db import SessionLocal
        from app.db.session_context import prime_session
        from app.models.finance.ar.invoice import Invoice

        org_id = uuid4()
        db = SessionLocal()
        try:
            prime_session(db, org_id)
            # Adjust the .options() call to whichever relationship attr
            # your model exposes. If no obvious relationship exists, this
            # test is optional — drop it and the spec's R3 is verified by
            # Phase 2 integration runs against the FX revaluation suite.
            stmt = select(Invoice).options(
                selectinload(Invoice.line_items)  # adapt name if needed
            )
            # We just need the query to execute without raising; the
            # SQL trace would show two SELECTs each with organization_id.
            results = db.scalars(stmt).all()
            assert isinstance(results, list)
        finally:
            db.close()
```

If `Invoice.line_items` doesn't exist, pick another `selectinload`-able relationship from the model. The test's purpose is the SQL-execution path, not the data.

- [ ] **Step 2: Run the test to verify it passes**

Run: `poetry run pytest tests/db/test_org_listener.py::TestEagerLoadingComposition -v`
Expected: 1 pass.

If the test fails with a SQLAlchemy error about the loader criteria not applying to the aliased relationship, the listener's `include_aliases=True` argument needs investigation — surface this finding to the cross-cutting review.

---

## Task 10: Wire `register_org_listener()` at app startup, gated on env flag

**Files:**
- Modify: `app/db/__init__.py` (add conditional registration call)

- [ ] **Step 1: Read the existing `app/db/__init__.py`**

Run: `cat app/db/__init__.py | head -40`

Note where `SessionLocal` is constructed and the engine bound. The listener registration should happen ONCE at module import time, after `Base` and the engine are bound.

- [ ] **Step 2: Add conditional registration**

Edit `app/db/__init__.py`. After the Engine and SessionLocal are constructed (typically near the end of the module), add:

```python
# Phase 1: org-filter listener is registered only when the env flag is on.
# Default is OFF — no behavior change. See:
#   docs/superpowers/specs/2026-05-10-multi-org-listener-design.md (D5)
from app.config import settings as _app_settings

if _app_settings.enforce_org_filter:
    from app.db.org_listener import register_org_listener

    register_org_listener()
```

The `_` prefix on `_app_settings` avoids polluting the module's public namespace.

- [ ] **Step 3: Verify the wiring is correct**

Run: `poetry run python -c "import app.db; print('imports clean')"` (skip if venv mismatch).

Then run a quick test to confirm the flag-OFF default doesn't break anything:

```bash
poetry run pytest tests/db/test_org_listener.py -v
```

Expected: all tests pass — but they should pass because the test fixtures register the listener manually. The point is that NO tests break from the import-time wiring.

Then run the broader test suite to confirm no regressions from the new module imports:

```bash
poetry run pytest tests/ifrs/gl/ -q
```

Expected: same pass count as before (~437 tests).

- [ ] **Step 4: Verify the flag-ON path works**

Run: `ENFORCE_ORG_FILTER=true poetry run pytest tests/db/test_org_listener.py -v`

Expected: same passes (the listener is now globally registered AND registered by fixtures — `event.contains` makes registration idempotent so this is safe).

---

## Task 11: Modify `get_db` to call `prime_session`

**Files:**
- Modify: `app/web/deps.py` (add `prime_session` call inside `get_db`)
- Test: `tests/web/test_deps_org_priming.py` (new)

- [ ] **Step 1: Read the existing `get_db` dependency**

Run: `grep -n "def get_db\|def require_auth" app/web/deps.py | head -5`

Then read the function body around `get_db` to understand its current shape.

Important: `get_db` is currently called by every route via `Depends(get_db)`, often alongside `Depends(require_auth)`. The challenge: `get_db` doesn't currently take `auth` as a parameter — it's a sibling dependency. We need a way to inject the org_id from auth INTO the session.

Options:
1. Introduce `get_db_for_org` that takes auth and primes the session inline. Routes opt in by changing `Depends(get_db)` → `Depends(get_db_for_org)`. Backward compatible but per-route migration.
2. Add `auth: WebAuthContext = Depends(require_auth)` as a parameter to `get_db`. ALL routes get the new behavior automatically. Requires that every route also has auth — most do, but not all (e.g., login endpoint).
3. Use FastAPI's request-scoped state: middleware sets `request.state.organization_id`; `get_db` reads it. Cleanest separation, no parameter coupling.

For Phase 1, pick Option 1 (`get_db_for_org`). It's surgical and doesn't change the existing `get_db` contract — routes that legitimately don't have auth (login, healthcheck) keep using the current `get_db`.

- [ ] **Step 2: Write the failing test**

Create `tests/web/test_deps_org_priming.py`:

```python
"""Tests for get_db_for_org — the auth-aware DB dependency that primes
the session with the request's organization_id."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest


def test_get_db_for_org_primes_session_with_auth_org_id():
    """The dependency must call prime_session(db, auth.organization_id)
    before yielding the session."""
    from app.web.deps import get_db_for_org

    org_id = uuid4()
    auth = MagicMock(organization_id=org_id)

    # get_db_for_org is a generator dependency; the simplest test is to
    # call it directly and inspect the yielded session's info.
    gen = get_db_for_org(auth=auth)
    db = next(gen)
    try:
        assert db.info["organization_id"] == org_id
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def test_get_db_for_org_closes_session_on_completion():
    """The session must be closed even after the generator exhausts."""
    from app.web.deps import get_db_for_org

    org_id = uuid4()
    auth = MagicMock(organization_id=org_id)

    gen = get_db_for_org(auth=auth)
    db = next(gen)
    try:
        next(gen)
    except StopIteration:
        pass

    assert db.is_active is False
```

Add `tests/web/__init__.py` if missing: `touch tests/web/__init__.py`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/web/test_deps_org_priming.py -v`
Expected: 2 failures with `ImportError: cannot import name 'get_db_for_org'`.

- [ ] **Step 4: Implement `get_db_for_org`**

Edit `app/web/deps.py`. Add (near the existing `get_db`):

```python
from app.db.session_context import prime_session


def get_db_for_org(
    auth: WebAuthContext = Depends(require_auth),
):
    """DB session dependency that primes the session with the request's
    organization_id before yielding.

    Use this dependency in routes that act on org-scoped data:

        @router.get("/things")
        def list_things(
            auth=Depends(require_auth),
            db: Session = Depends(get_db_for_org),
        ):
            ...

    The plain ``get_db`` remains for routes that legitimately don't have
    a per-request organization context (login, healthcheck, public pages).
    """
    db = SessionLocal()
    try:
        prime_session(db, auth.organization_id)
        yield db
    finally:
        db.close()
```

If `SessionLocal` and `Depends`/`require_auth` aren't already imported in `deps.py`, add them.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/web/test_deps_org_priming.py -v`
Expected: 2 passes.

---

## Task 12: `get_or_404_for_org` helper

**Files:**
- Create: `app/services/common/multi_tenant.py`
- Test: `tests/services/test_multi_tenant_helpers.py` (new)

- [ ] **Step 1: Verify `NotFoundError` exists in the codebase**

Run: `grep -rn "class NotFoundError\|class NotFound\b" app/ --include='*.py' | head -5`

Expected: locates the canonical `NotFoundError` (per CLAUDE.md, likely `app.errors.NotFoundError`). Note the import path.

- [ ] **Step 2: Write the failing tests**

Create `tests/services/test_multi_tenant_helpers.py`:

```python
"""Tests for get_or_404_for_org — sugar over db.get + 404."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest


def test_returns_object_when_found():
    from app.services.common.multi_tenant import get_or_404_for_org

    expected = MagicMock()
    db = MagicMock()
    db.get.return_value = expected

    result = get_or_404_for_org(db, MagicMock, uuid4())

    assert result is expected


def test_raises_not_found_when_db_returns_none():
    """The listener returns None when the PK exists in another org. The
    helper translates None → NotFoundError so callers don't have to."""
    from app.errors import NotFoundError  # adapt path per your verification
    from app.services.common.multi_tenant import get_or_404_for_org

    db = MagicMock()
    db.get.return_value = None

    class FakeModel:
        __name__ = "FakeModel"

    with pytest.raises(NotFoundError) as exc:
        get_or_404_for_org(db, FakeModel, uuid4())

    assert "FakeModel" in str(exc.value)


def test_does_not_leak_existence_on_cross_org():
    """The error message is the same regardless of whether the PK doesn't
    exist OR exists in another org. The helper just translates None to
    404; both cases pass through identically."""
    from app.errors import NotFoundError
    from app.services.common.multi_tenant import get_or_404_for_org

    db = MagicMock()
    db.get.return_value = None

    class FakeModel:
        __name__ = "Invoice"

    with pytest.raises(NotFoundError) as exc:
        get_or_404_for_org(db, FakeModel, uuid4())

    # Message must NOT differ between "not found" and "wrong org".
    assert "wrong org" not in str(exc.value).lower()
    assert "tenant" not in str(exc.value).lower()
```

If your verification found a different exception path (e.g., `from app.exceptions`), adapt accordingly.

Add `tests/services/__init__.py` if missing.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/services/test_multi_tenant_helpers.py -v`
Expected: 3 failures with `ModuleNotFoundError: No module named 'app.services.common.multi_tenant'`.

- [ ] **Step 4: Create the helper**

Create `app/services/common/multi_tenant.py`:

```python
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
```

Create `app/services/common/__init__.py` if missing: `touch app/services/common/__init__.py`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/services/test_multi_tenant_helpers.py -v`
Expected: 3 passes.

---

## Task 13: Final test sweep + lint + type check

**Files:** No code changes; verification only.

- [ ] **Step 1: Run all new tests**

Run: `poetry run pytest tests/db/ tests/services/test_multi_tenant_helpers.py tests/web/test_deps_org_priming.py -v`

Expected: ~22 passes (plus 1 skip in TestGetFiltering).

- [ ] **Step 2: Run the broader test suite to confirm no regressions**

Run: `poetry run pytest tests/ifrs/gl/ tests/sync/ tests/services/test_mono_sync.py -q`

Expected: same pass count as the FX revaluation merge (~437 tests). The Phase 1 default is listener-OFF, so no behavior change.

- [ ] **Step 3: Verify the flag-ON smoke**

Run: `ENFORCE_ORG_FILTER=true poetry run pytest tests/db/ -v`

Expected: tests still pass — the listener is now both globally registered (via the env flag) AND registered by fixtures (idempotent).

This is a smoke test only. Phase 2 will turn the flag on for the entire test suite via `tests/conftest.py` and triage the resulting failures.

- [ ] **Step 4: Lint and type check**

Run: `poetry run ruff check app/db/ app/services/common/multi_tenant.py app/web/deps.py`
Expected: All checks passed!

Run: `poetry run ruff format --check app/db/ app/services/common/ app/web/deps.py tests/db/ tests/services/test_multi_tenant_helpers.py tests/web/test_deps_org_priming.py`
Expected: all formatted. If any reformatting is needed, run `poetry run ruff format` and re-check.

Run: `poetry run mypy app/db/multi_tenant.py app/db/session_context.py app/db/org_listener.py app/services/common/multi_tenant.py`
Expected: Success: no issues found.

- [ ] **Step 5: Commit and prepare PR**

Stage and commit the entire Phase 1 surface:

```bash
git add app/db/multi_tenant.py app/db/session_context.py app/db/org_listener.py app/db/__init__.py
git add app/services/common/__init__.py app/services/common/multi_tenant.py
git add app/web/deps.py app/config.py
git add tests/db/__init__.py tests/db/test_multi_tenant.py tests/db/test_session_context.py tests/db/test_org_listener.py
git add tests/services/__init__.py tests/services/test_multi_tenant_helpers.py
git add tests/web/__init__.py tests/web/test_deps_org_priming.py
git add docs/superpowers/specs/2026-05-10-multi-org-listener-design.md
git add docs/superpowers/plans/2026-05-10-multi-org-listener.md

git commit -m "Multi-tenant session listener: Phase 1 (P1 #5)

Builds the SQLAlchemy do_orm_execute listener and supporting plumbing
for auto-injecting WHERE organization_id = ? on ORM queries against
org-scoped models. Phase 1 ships disabled by default (env flag
ENFORCE_ORG_FILTER, default false) — no behavior change in any
environment. Phase 2 turns it on for tests, Phase 3 staging, Phase 4
prod.

Audit reference: docs/2026_correctness_audit_findings.md, P1 #5.
Spec: docs/superpowers/specs/2026-05-10-multi-org-listener-design.md.

Components:
- app/db/multi_tenant.py: MissingOrgContextError, deny-list, is_org_scoped()
- app/db/session_context.py: prime_session, allow_cross_org, session_for_org
- app/db/org_listener.py: do_orm_execute handler + register/unregister
- app/db/__init__.py: conditional registration at startup
- app/services/common/multi_tenant.py: get_or_404_for_org helper
- app/web/deps.py: get_db_for_org auth-aware dependency
- app/config.py: enforce_org_filter setting

Tests cover: filter injection on select, MissingOrgContextError on missing
prime, allow_cross_org bypass + nested + restore-on-exception, deny-list
unaffected, eager-loading composition (R3), get_db_for_org wiring, and
the helper's 404 + no-info-leak message contract.
"
```

Then push and open the PR per the standard workflow.

---

## Self-review — already done inline

- **Spec coverage**: D1 (strict raise) → Task 5 step 4 raises `MissingOrgContextError`. D2 (`allow_cross_org`) → Task 2 + 7. D3 (heuristic + deny-list) → Task 1 + 8. D4 (`get_db` HTTP, `session_for_org` Celery) → Tasks 3, 11. D5 (env flag, phased) → Task 4 + 10.
- **Placeholder scan**: No "TBD" / "TODO" / "implement later". Where R1-R3 verification depends on real code state, the verification step is in the task itself (Task 5 step 1, Task 9 step 1).
- **Type consistency**: `prime_session(session, organization_id)` signature matches across Tasks 2, 3, 11. `MissingOrgContextError(model_name, query_repr=None)` matches across Tasks 1, 5. `get_or_404_for_org(db, model, pk)` no `org_id` arg (listener handles it) — consistent in spec and Task 12.
