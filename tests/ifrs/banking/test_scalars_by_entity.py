"""The reconciliation mock helper itself.

`scalars_by_entity` exists because the previous shape — one flat ordered list
answering every `db.scalars()` call in sequence — coupled every banking test
to the engine's internal query order ACROSS entities. Removing a single
Splynx `CustomerPayment` preload shifted every later `BankAccount` and
`BankStatementLine` answer by one, and six settlement tests failed for a
change that had nothing to do with settlements.

These tests pin the property that makes that impossible: entity queues are
independent. They are deliberately about the helper rather than about
reconciliation, because the helper is what the other 60-odd tests rest on.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy import select

from app.models.finance.ar.customer_payment import CustomerPayment
from app.models.finance.banking.bank_account import BankAccount
from app.models.finance.banking.bank_statement import BankStatementLine
from tests.ifrs.banking.test_auto_reconciliation import (
    _selected_entity_name,
    scalars_by_entity,
)


def _rows(db, statement):
    return db.scalars(statement).all()


# --------------------------------------------------------------------------
# Entity detection
# --------------------------------------------------------------------------


def test_an_entity_select_reports_its_class():
    assert _selected_entity_name(select(CustomerPayment)) == "CustomerPayment"


def test_a_column_select_reports_the_owning_class():
    """`select(BankAccount.bank_account_id)` must dispatch like
    `select(BankAccount)` — the settlement pass uses both forms."""
    assert _selected_entity_name(select(BankAccount.bank_account_id)) == "BankAccount"


def test_an_unrecognised_statement_is_not_an_error():
    assert _selected_entity_name(object()) == ""


# --------------------------------------------------------------------------
# The property that matters
# --------------------------------------------------------------------------


def test_entity_queues_are_independent():
    """The load-bearing test. Consuming one entity's queue must not advance
    another's — that cross-entity advance is exactly what broke six settlement
    tests when a CustomerPayment query was removed."""
    db = MagicMock()
    scalars_by_entity(
        db,
        BankAccount=[["first"], ["second"]],
        CustomerPayment=[["payments"]],
    )
    # Drain CustomerPayment first — under the old flat list this would have
    # eaten BankAccount's first answer.
    assert _rows(db, select(CustomerPayment)) == ["payments"]
    assert _rows(db, select(BankAccount)) == ["first"]
    assert _rows(db, select(BankAccount)) == ["second"]


def test_order_is_preserved_within_an_entity():
    """Ordering within one entity is real: the settlement pass selects
    BankAccount three times and means something different each time."""
    db = MagicMock()
    scalars_by_entity(db, BankAccount=[["a"], ["b"], ["c"]])
    assert [_rows(db, select(BankAccount)) for _ in range(3)] == [["a"], ["b"], ["c"]]


def test_an_exhausted_queue_yields_empty_not_an_error():
    """A real database answers a query it has no rows for; it does not raise.
    The old flat list raised StopIteration once the engine made one more call
    than the test anticipated."""
    db = MagicMock()
    scalars_by_entity(db, BankAccount=[["only"]])
    assert _rows(db, select(BankAccount)) == ["only"]
    assert _rows(db, select(BankAccount)) == []


def test_an_unqueued_entity_yields_empty():
    """A test that does not care about an entity should not have to enumerate
    it — which is what forced every test to list every pass."""
    db = MagicMock()
    scalars_by_entity(db, BankAccount=[["x"]])
    assert _rows(db, select(BankStatementLine)) == []


def test_a_query_the_engine_stops_making_disturbs_nothing():
    """Stated as the scenario rather than the mechanism: this is the Splynx
    preload removal, in miniature."""
    db = MagicMock()
    scalars_by_entity(
        db,
        CustomerPayment=[["splynx-preload"], ["ar-pass"]],
        BankStatementLine=[["lines"]],
    )
    # Engine no longer issues the preload — it simply never asks.
    assert _rows(db, select(BankStatementLine)) == ["lines"]
    assert _rows(db, select(CustomerPayment)) == ["splynx-preload"]
