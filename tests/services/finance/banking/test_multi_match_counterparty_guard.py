"""Counterparty-consistency guard for bank-reconciliation split matches.

Audit-standards-aligned policy:
- HARD block: a one-to-many split's GL legs must resolve to ONE economic
  counterparty *by parent* (a reseller/aggregator or multi-branch customer
  paying for its own child accounts in one deposit is allowed); reject only
  when legs roll up to genuinely different parents.
- SOFT signal: a wide date span across legs is a review flag (timing
  differences like deposits-in-transit are legitimate), never a hard block.

These cover the ``_counterparty_key`` resolver (incl. parent walk) and the
set-cardinality logic the guard relies on.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.finance.banking.reconciliation_parts.matching import (
    ReconciliationMatchingService,
)

resolve = ReconciliationMatchingService._counterparty_key


def _db(get_map: dict) -> MagicMock:
    """Mock session whose .get(Model, pk) returns get_map[pk]."""
    db = MagicMock()
    db.get.side_effect = lambda _model, pk: get_map.get(pk)
    return db


def test_customer_payment_resolves_to_root_parent() -> None:
    """A child customer's receipt resolves to the ultimate parent."""
    je_id, pay_id, child_id, parent_id = (uuid.uuid4() for _ in range(4))
    je = SimpleNamespace(
        journal_entry_id=je_id,
        source_document_type="CUSTOMER_PAYMENT",
        source_document_id=pay_id,
    )
    pay = SimpleNamespace(payment_id=pay_id, customer_id=child_id)
    child = SimpleNamespace(customer_id=child_id, parent_customer_id=parent_id)
    parent = SimpleNamespace(customer_id=parent_id, parent_customer_id=None)
    line = SimpleNamespace(journal_entry_id=je_id)
    db = _db({je_id: je, pay_id: pay, child_id: child, parent_id: parent})
    assert resolve(db, line) == ("customer", parent_id)


def test_two_children_same_parent_collapse_to_one_key() -> None:
    """Aggregator/branch batch: legs from two children of one parent -> 1 key."""
    parent_id = uuid.uuid4()
    keys = set()
    for _ in range(2):
        je_id, pay_id, child_id = (uuid.uuid4() for _ in range(3))
        je = SimpleNamespace(
            journal_entry_id=je_id,
            source_document_type="CUSTOMER_PAYMENT",
            source_document_id=pay_id,
        )
        pay = SimpleNamespace(payment_id=pay_id, customer_id=child_id)
        child = SimpleNamespace(customer_id=child_id, parent_customer_id=parent_id)
        parent = SimpleNamespace(customer_id=parent_id, parent_customer_id=None)
        line = SimpleNamespace(journal_entry_id=je_id)
        db = _db({je_id: je, pay_id: pay, child_id: child, parent_id: parent})
        keys.add(resolve(db, line))
    assert len(keys) == 1  # -> guard ALLOWS (one parent)


def test_unrelated_parents_produce_distinct_keys() -> None:
    """Legs rolling up to different parents -> >1 key -> guard rejects."""
    keys = set()
    for _ in range(2):
        je_id, pay_id, cust_id = (uuid.uuid4() for _ in range(3))
        je = SimpleNamespace(
            journal_entry_id=je_id,
            source_document_type="CUSTOMER_PAYMENT",
            source_document_id=pay_id,
        )
        pay = SimpleNamespace(payment_id=pay_id, customer_id=cust_id)
        cust = SimpleNamespace(customer_id=cust_id, parent_customer_id=None)
        line = SimpleNamespace(journal_entry_id=je_id)
        keys.add(resolve(_db({je_id: je, pay_id: pay, cust_id: cust}), line))
    assert len(keys) == 2  # -> guard REJECTS (unrelated payers)


def test_supplier_payment_resolves_to_supplier() -> None:
    je_id, pay_id, sup_id = (uuid.uuid4() for _ in range(3))
    je = SimpleNamespace(
        journal_entry_id=je_id,
        source_document_type="SUPPLIER_PAYMENT",
        source_document_id=pay_id,
    )
    pay = SimpleNamespace(payment_id=pay_id, supplier_id=sup_id)
    line = SimpleNamespace(journal_entry_id=je_id)
    assert resolve(_db({je_id: je, pay_id: pay}), line) == ("supplier", sup_id)


def test_null_source_document_is_unconstrained() -> None:
    """Legacy imported journals (NULL source_document_id) -> None (skipped)."""
    je_id = uuid.uuid4()
    je = SimpleNamespace(
        journal_entry_id=je_id,
        source_document_type="CUSTOMER_PAYMENT",
        source_document_id=None,
    )
    line = SimpleNamespace(journal_entry_id=je_id)
    assert resolve(_db({je_id: je}), line) is None


def test_customer_root_is_cycle_safe() -> None:
    """A parent cycle terminates (depth cap) rather than looping forever."""
    a, b = uuid.uuid4(), uuid.uuid4()
    ca = SimpleNamespace(customer_id=a, parent_customer_id=b)
    cb = SimpleNamespace(customer_id=b, parent_customer_id=a)  # cycle
    root = ReconciliationMatchingService._customer_root(_db({a: ca, b: cb}), a)
    assert root in (a, b)  # terminates, no infinite loop
