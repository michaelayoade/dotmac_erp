"""Tenant context must survive a commit inside the block.

Both canonical helpers set their PostgreSQL layer with ``SET LOCAL``, which is
TRANSACTION-scoped: the transaction that commits takes the setting with it.
Layer 1 (`session.info`) is session-scoped and does not, so after the first
commit the session read as *scoped* and behaved as *unscoped* — the exact
asymmetry that makes this class of bug silent.

The helpers now re-arm on SQLAlchemy's ``after_begin``, which fires before the
statement that opened the transaction runs. These tests assert the SQL is
actually re-emitted, since "we added a listener" is not the same claim as "the
context is present after a commit".

`app/tools/` has three real callers that commit inside a loop
(`post_and_match_paystack_opex_expense_reimbursements`,
`enforce_paystack_opex_acc_pay_matches`,
`rematch_paystack_opex_expense_claim_payments`) — one of them streaming with
`yield_per(200)`, so it keeps fetching after the first commit. They are why
this is a fix rather than a precaution.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import sessionmaker

ORG = uuid.uuid4()


@pytest.fixture
def engine():
    # SQLite accepts neither SET LOCAL nor GUCs, so the assertion is on the SQL
    # the helpers emit rather than on Postgres' resulting state. What is being
    # proven here is re-arming behaviour, which is dialect-independent; the
    # end-to-end GUC behaviour belongs in the Postgres integration lane.
    return create_engine("sqlite+pysqlite:///:memory:")


@pytest.fixture
def recorded(engine, monkeypatch):
    """Every SET LOCAL the helpers emit, in order."""
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, params, context, executemany):
        if "SET LOCAL" in statement.upper():
            statements.append(statement.strip())

    # The real helpers no-op on any non-PostgreSQL dialect, so on the SQLite
    # unit lane they would emit nothing to observe. Record the intent instead;
    # the event plumbing under test is dialect-independent.
    def _fake_set_org(connection: Connection, organization_id: uuid.UUID) -> None:
        statements.append(
            f"SET LOCAL app.current_organization_id = '{organization_id}'"
        )

    def _fake_bypass(connection: Connection) -> None:
        statements.append("SET LOCAL app.bypass_rls = 'true'")

    import app.db.session_context as sc

    monkeypatch.setattr(sc, "set_current_organization_on_connection", _fake_set_org)
    monkeypatch.setattr(sc, "enable_rls_bypass_on_connection", _fake_bypass)
    return statements


@pytest.fixture
def session_factory(engine, monkeypatch):
    factory = sessionmaker(bind=engine)
    import app.db as db_module

    monkeypatch.setattr(db_module, "SessionLocal", factory)
    return factory


def test_org_context_is_armed_before_the_first_query(recorded, session_factory):
    from app.db.session_context import session_for_org

    with session_for_org(ORG) as db:
        db.execute(text("SELECT 1"))

    assert any(str(ORG) in s for s in recorded), (
        "the organization GUC was never emitted"
    )


def test_org_context_is_re_armed_after_every_commit(recorded, session_factory):
    """The load-bearing test.

    Three iterations open three transactions, so the context is armed exactly
    three times — once per transaction. There is deliberately no up-front
    arming: the listener fires when the first transaction begins, which is
    both sufficient and one statement cheaper. The old code armed ONCE for
    the whole block, so anything above 1 is the property under test.
    """
    from app.db.session_context import session_for_org

    with session_for_org(ORG) as db:
        for _ in range(3):
            db.execute(text("SELECT 1"))
            db.commit()

    armings = [s for s in recorded if str(ORG) in s]
    assert len(armings) == 3, (
        f"expected one arming per transaction (3), saw {len(armings)}: {armings}"
    )


def test_the_bypass_is_re_armed_after_every_commit(recorded, session_factory):
    from app.db.session_context import cross_org_session

    with cross_org_session() as db:
        for _ in range(3):
            db.execute(text("SELECT 1"))
            db.commit()

    armings = [s for s in recorded if "bypass_rls" in s]
    assert len(armings) == 3, (
        f"expected one arming per transaction (3), saw {len(armings)}"
    )


def test_the_listener_is_removed_when_the_block_exits(recorded, session_factory):
    """A session returned to the pool must not carry a re-arming listener that
    would set another tenant's context on a later borrower."""
    from app.db.session_context import session_for_org

    with session_for_org(ORG) as db:
        session = db
        db.execute(text("SELECT 1"))

    before = len([s for s in recorded if str(ORG) in s])
    session.execute(text("SELECT 1"))
    session.commit()
    after = len([s for s in recorded if str(ORG) in s])
    assert after == before, "context was re-armed after the block exited"


def test_arming_emits_real_sql_without_recursing(session_factory, monkeypatch):
    """The arming handler executes SQL on the session it is arming.

    `after_begin` fires as a transaction opens, and the handler then runs a
    statement — which could plausibly re-enter the same event. It does not:
    the transaction has already begun by the time the handler runs, so no
    further `after_begin` is emitted. The other tests stub the emitter, so
    this one uses a real `db.execute` to prove the flow terminates rather
    than assuming it.
    """
    calls: list[int] = []

    def _real_sql_arm(connection: Connection, organization_id: uuid.UUID) -> None:
        calls.append(1)
        if len(calls) > 10:  # a recursion would blow past this long before
            raise AssertionError("arming recursed")
        # Executes on the CONNECTION — going through the Session here is what
        # raised InvalidRequestError ("provisioning a new connection").
        connection.execute(text("SELECT 1"))

    import app.db.session_context as sc

    monkeypatch.setattr(sc, "set_current_organization_on_connection", _real_sql_arm)

    from app.db.session_context import session_for_org

    with session_for_org(ORG) as db:
        db.execute(text("SELECT 1"))
        db.commit()
        db.execute(text("SELECT 1"))
        db.commit()

    assert calls == [1, 1], f"expected one arming per transaction, got {len(calls)}"


def test_sensitivity_without_re_arming_only_one_arming_happens(
    recorded, session_factory, monkeypatch
):
    """Sensitivity proof: the assertions above must be able to fail.

    With the listener suppressed the GUC is never armed at all, because the
    listener is now the ONLY thing that arms it — there is no up-front call
    left to fall back on. So the re-arming tests cannot be passing for some
    incidental reason.
    """
    import app.db.session_context as sc

    monkeypatch.setattr(sc.event, "listen", lambda *a, **k: None)
    monkeypatch.setattr(sc.event, "remove", lambda *a, **k: None)

    from app.db.session_context import session_for_org

    with session_for_org(ORG) as db:
        for _ in range(3):
            db.execute(text("SELECT 1"))
            db.commit()

    armings = [s for s in recorded if str(ORG) in s]
    assert len(armings) == 0, (
        "with the listener suppressed the GUC is never armed at all — which is "
        f"what made the old single up-front call load-bearing; saw {armings}"
    )
