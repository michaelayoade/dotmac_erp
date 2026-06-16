"""Tests for CustomerFamilyResolver (consolidated reseller account families)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from app.services.finance.ar.customer_family import CustomerFamilyResolver


def _resolver_with_children(child_ids: list[uuid.UUID]):
    db = MagicMock()
    db.scalars.return_value.all.return_value = child_ids
    return CustomerFamilyResolver(db), db


class TestChildIds:
    def test_returns_child_ids(self) -> None:
        c1, c2 = uuid.uuid4(), uuid.uuid4()
        resolver, _ = _resolver_with_children([c1, c2])
        assert resolver.child_ids(uuid.uuid4(), uuid.uuid4()) == [c1, c2]

    def test_no_children_returns_empty(self) -> None:
        resolver, _ = _resolver_with_children([])
        assert resolver.child_ids(uuid.uuid4(), uuid.uuid4()) == []


class TestFamilyIds:
    def test_parent_includes_self_first_then_children(self) -> None:
        parent = uuid.uuid4()
        c1, c2 = uuid.uuid4(), uuid.uuid4()
        resolver, _ = _resolver_with_children([c1, c2])
        family = resolver.family_ids(uuid.uuid4(), parent)
        assert family[0] == parent  # self is always first
        assert set(family) == {parent, c1, c2}
        assert len(family) == 3

    def test_standalone_or_subaccount_returns_only_self(self) -> None:
        # A standalone customer or a sub-account has no children of its own,
        # so its family is just itself -> the detail view is not consolidated.
        cust = uuid.uuid4()
        resolver, _ = _resolver_with_children([])
        assert resolver.family_ids(uuid.uuid4(), cust) == [cust]


class TestIsConsolidatedParent:
    def test_true_when_a_child_exists(self) -> None:
        db = MagicMock()
        db.scalar.return_value = uuid.uuid4()
        resolver = CustomerFamilyResolver(db)
        assert resolver.is_consolidated_parent(uuid.uuid4(), uuid.uuid4()) is True

    def test_false_when_no_child(self) -> None:
        db = MagicMock()
        db.scalar.return_value = None
        resolver = CustomerFamilyResolver(db)
        assert resolver.is_consolidated_parent(uuid.uuid4(), uuid.uuid4()) is False


class TestAttributionMap:
    def test_maps_each_member_to_code_and_name(self) -> None:
        id1, id2 = uuid.uuid4(), uuid.uuid4()
        row1 = MagicMock(
            customer_id=id1, customer_code="CUST-00318", legal_name="HYPERIA"
        )
        row2 = MagicMock(
            customer_id=id2, customer_code="CUST-00400", legal_name="Sub A"
        )
        db = MagicMock()
        db.execute.return_value.all.return_value = [row1, row2]
        result = CustomerFamilyResolver(db).attribution_map(uuid.uuid4(), [id1, id2])
        assert result[id1] == {"code": "CUST-00318", "name": "HYPERIA"}
        assert result[id2] == {"code": "CUST-00400", "name": "Sub A"}

    def test_empty_family_short_circuits_without_query(self) -> None:
        db = MagicMock()
        result = CustomerFamilyResolver(db).attribution_map(uuid.uuid4(), [])
        assert result == {}
        db.execute.assert_not_called()
