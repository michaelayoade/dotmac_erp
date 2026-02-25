"""
Tests for Field-Level Change Tracking.

Verifies the TrackedMixin, field change extraction, value serialisation,
and the before_flush event listener behaviour.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from unittest.mock import patch

from app.services.audit.field_tracker import (
    _extract_changes,
    _serialise,
    change_source_var,
    set_change_source,
)

# ── Value Serialisation Tests ────────────────────────────────────────


class TestSerialise:
    """Test _serialise converts values to display-friendly strings."""

    def test_none_returns_none(self) -> None:
        assert _serialise(None) is None

    def test_string(self) -> None:
        assert _serialise("hello") == "hello"

    def test_integer(self) -> None:
        assert _serialise(42) == "42"

    def test_decimal(self) -> None:
        assert _serialise(Decimal("1234.56")) == "1234.56"

    def test_uuid(self) -> None:
        uid = uuid.uuid4()
        assert _serialise(uid) == str(uid)

    def test_datetime(self) -> None:
        dt = datetime(2026, 2, 24, 14, 30, 0)
        result = _serialise(dt)
        assert "2026-02-24" in result
        assert "14:30" in result

    def test_date(self) -> None:
        d = date(2026, 2, 24)
        assert _serialise(d) == "2026-02-24"

    def test_bool_true(self) -> None:
        assert _serialise(True) == "true"

    def test_bool_false(self) -> None:
        assert _serialise(False) == "false"

    def test_enum(self) -> None:
        class Status(str, Enum):
            DRAFT = "DRAFT"
            POSTED = "POSTED"

        assert _serialise(Status.DRAFT) == "DRAFT"
        assert _serialise(Status.POSTED) == "POSTED"

    def test_float(self) -> None:
        assert _serialise(3.14) == "3.14"


# ── Change Source Context Var Tests ──────────────────────────────────


class TestChangeSource:
    """Test the change_source context variable."""

    def test_set_and_get(self) -> None:
        set_change_source("web_form")
        assert change_source_var.get() == "web_form"

    def test_default_is_empty(self) -> None:
        # Reset by setting empty
        set_change_source("")
        assert change_source_var.get() == ""

    def test_api_source(self) -> None:
        set_change_source("api")
        assert change_source_var.get() == "api"

    def test_celery_source(self) -> None:
        set_change_source("celery_task")
        assert change_source_var.get() == "celery_task"


# ── Change Extraction Tests ──────────────────────────────────────────


class MockHistory:
    """Mock SQLAlchemy attribute history."""

    def __init__(
        self,
        added: list[Any] | None = None,
        deleted: list[Any] | None = None,
        has_changes_val: bool = True,
    ):
        self.added = added or []
        self.deleted = deleted or []
        self._has_changes = has_changes_val

    def has_changes(self) -> bool:
        return self._has_changes


class MockAttr:
    """Mock a SQLAlchemy mapped attribute."""

    def __init__(self, history: MockHistory):
        self.history = history


class MockAttrs:
    """Mock the attrs dict-like on an inspected instance."""

    def __init__(self, attrs: dict[str, MockAttr]):
        self._attrs = attrs

    def get(self, key: str) -> MockAttr | None:
        return self._attrs.get(key)


class MockTrackedInstance:
    """Fake tracked model instance for testing _extract_changes."""

    __tracked_fields__ = {
        "status": {"label": "Status"},
        "amount": {"label": "Amount"},
        "notes": {"label": "Notes"},
    }
    __tracking_entity_type__ = "TestEntity"
    __tracking_pk_field__ = "entity_id"

    def __init__(self) -> None:
        self.entity_id = uuid.uuid4()
        self.organization_id = uuid.uuid4()


class TestExtractChanges:
    """Test _extract_changes with mocked SQLAlchemy inspection."""

    def test_detects_status_change(self) -> None:
        instance = MockTrackedInstance()

        mock_attrs = MockAttrs(
            {
                "status": MockAttr(MockHistory(deleted=["DRAFT"], added=["POSTED"])),
                "amount": MockAttr(MockHistory(has_changes_val=False)),
                "notes": MockAttr(MockHistory(has_changes_val=False)),
            }
        )

        with patch("app.services.audit.field_tracker.inspect") as mock_inspect:
            mock_inspect.return_value.attrs = mock_attrs
            changes = _extract_changes(instance)

        assert len(changes) == 1
        assert changes[0]["field_name"] == "status"
        assert changes[0]["field_label"] == "Status"
        assert changes[0]["old_value"] == "DRAFT"
        assert changes[0]["new_value"] == "POSTED"

    def test_detects_multiple_changes(self) -> None:
        instance = MockTrackedInstance()

        mock_attrs = MockAttrs(
            {
                "status": MockAttr(MockHistory(deleted=["DRAFT"], added=["POSTED"])),
                "amount": MockAttr(
                    MockHistory(
                        deleted=[Decimal("100.00")],
                        added=[Decimal("200.00")],
                    )
                ),
                "notes": MockAttr(MockHistory(has_changes_val=False)),
            }
        )

        with patch("app.services.audit.field_tracker.inspect") as mock_inspect:
            mock_inspect.return_value.attrs = mock_attrs
            changes = _extract_changes(instance)

        assert len(changes) == 2
        field_names = {c["field_name"] for c in changes}
        assert field_names == {"status", "amount"}

    def test_skips_false_positive(self) -> None:
        """If old and new serialise to the same string, skip the change."""
        instance = MockTrackedInstance()

        mock_attrs = MockAttrs(
            {
                "status": MockAttr(MockHistory(deleted=["DRAFT"], added=["DRAFT"])),
                "amount": MockAttr(MockHistory(has_changes_val=False)),
                "notes": MockAttr(MockHistory(has_changes_val=False)),
            }
        )

        with patch("app.services.audit.field_tracker.inspect") as mock_inspect:
            mock_inspect.return_value.attrs = mock_attrs
            changes = _extract_changes(instance)

        assert len(changes) == 0

    def test_handles_none_to_value(self) -> None:
        """Change from None to a value should be captured."""
        instance = MockTrackedInstance()

        mock_attrs = MockAttrs(
            {
                "status": MockAttr(MockHistory(has_changes_val=False)),
                "amount": MockAttr(MockHistory(has_changes_val=False)),
                "notes": MockAttr(
                    MockHistory(deleted=[None], added=["Important note"])
                ),
            }
        )

        with patch("app.services.audit.field_tracker.inspect") as mock_inspect:
            mock_inspect.return_value.attrs = mock_attrs
            changes = _extract_changes(instance)

        assert len(changes) == 1
        assert changes[0]["field_name"] == "notes"
        assert changes[0]["old_value"] is None
        assert changes[0]["new_value"] == "Important note"

    def test_empty_tracked_fields_returns_empty(self) -> None:
        """Instance with no tracked fields returns no changes."""

        class EmptyTracked:
            __tracked_fields__: dict[str, dict[str, Any]] = {}

        instance = EmptyTracked()
        changes = _extract_changes(instance)
        assert changes == []

    def test_nonexistent_field_skipped(self) -> None:
        """If a tracked field doesn't exist on the model, skip it."""
        instance = MockTrackedInstance()

        mock_attrs = MockAttrs(
            {
                "status": MockAttr(MockHistory(has_changes_val=False)),
                # "amount" missing from attrs — should be skipped
                "notes": MockAttr(MockHistory(has_changes_val=False)),
            }
        )

        with patch("app.services.audit.field_tracker.inspect") as mock_inspect:
            mock_inspect.return_value.attrs = mock_attrs
            changes = _extract_changes(instance)

        assert len(changes) == 0


# ── TrackedMixin Tests ───────────────────────────────────────────────


class TestTrackedMixin:
    """Test the TrackedMixin class attributes."""

    def test_journal_entry_tracked_fields(self) -> None:
        """JournalEntry should have correct tracked fields."""
        from app.models.finance.gl.journal_entry import JournalEntry

        assert "status" in JournalEntry.__tracked_fields__
        assert "entry_date" in JournalEntry.__tracked_fields__
        assert JournalEntry.__tracking_entity_type__ == "JournalEntry"
        assert JournalEntry.__tracking_pk_field__ == "journal_entry_id"

    def test_invoice_tracked_fields(self) -> None:
        """Invoice should have correct tracked fields."""
        from app.models.finance.ar.invoice import Invoice

        assert "status" in Invoice.__tracked_fields__
        assert "total_amount" in Invoice.__tracked_fields__
        assert "customer_id" in Invoice.__tracked_fields__
        assert "due_date" in Invoice.__tracked_fields__
        assert Invoice.__tracking_entity_type__ == "Invoice"
        assert Invoice.__tracking_pk_field__ == "invoice_id"

    def test_bank_reconciliation_tracked_fields(self) -> None:
        """BankReconciliation should have correct tracked fields."""
        from app.models.finance.banking.bank_reconciliation import BankReconciliation

        assert "status" in BankReconciliation.__tracked_fields__
        assert "reconciliation_date" in BankReconciliation.__tracked_fields__
        assert "statement_closing_balance" in BankReconciliation.__tracked_fields__
        assert BankReconciliation.__tracking_entity_type__ == "BankReconciliation"
        assert BankReconciliation.__tracking_pk_field__ == "reconciliation_id"
