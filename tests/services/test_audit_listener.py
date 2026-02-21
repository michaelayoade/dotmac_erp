"""Tests for the automatic ORM audit listener."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from unittest.mock import MagicMock, patch

from app.services.audit_listener import (
    _get_org_id,
    _get_pk_value,
    _serialise_value,
    _should_skip,
    register_audit_listeners,
)

# ── Serialisation tests ────────────────────────────────────────────────────


class FakeStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DRAFT = "DRAFT"


class TestSerialiseValue:
    def test_none(self) -> None:
        assert _serialise_value(None) is None

    def test_uuid(self) -> None:
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        assert _serialise_value(uid) == "12345678-1234-5678-1234-567812345678"

    def test_datetime(self) -> None:
        dt = datetime(2026, 2, 21, 14, 30, 0)
        assert _serialise_value(dt) == "2026-02-21T14:30:00"

    def test_date(self) -> None:
        d = date(2026, 2, 21)
        assert _serialise_value(d) == "2026-02-21"

    def test_decimal(self) -> None:
        assert _serialise_value(Decimal("1234.56")) == "1234.56"

    def test_enum(self) -> None:
        assert _serialise_value(FakeStatus.ACTIVE) == "ACTIVE"

    def test_bytes(self) -> None:
        assert _serialise_value(b"binary") == "<binary>"

    def test_primitives(self) -> None:
        assert _serialise_value(42) == 42
        assert _serialise_value(3.14) == 3.14
        assert _serialise_value(True) is True
        assert _serialise_value("hello") == "hello"

    def test_fallback(self) -> None:
        # Arbitrary objects get str() fallback
        result = _serialise_value([1, 2, 3])
        assert result == "[1, 2, 3]"


# ── Skip logic tests ──────────────────────────────────────────────────────


class TestShouldSkip:
    def test_audit_log_skipped(self) -> None:
        assert _should_skip("audit", "audit_log") is True

    def test_notification_skipped(self) -> None:
        assert _should_skip("public", "notification") is True

    def test_outbox_skipped(self) -> None:
        assert _should_skip("platform", "event_outbox") is True

    def test_regular_table_not_skipped(self) -> None:
        assert _should_skip("ar", "invoice") is False
        assert _should_skip("gl", "account") is False

    def test_account_balance_skipped(self) -> None:
        assert _should_skip("gl", "account_balance") is True


# ── Org ID extraction tests ───────────────────────────────────────────────


class TestGetOrgId:
    def test_uuid_attr(self) -> None:
        obj = MagicMock()
        uid = uuid.uuid4()
        obj.organization_id = uid
        assert _get_org_id(obj) == uid

    def test_string_attr(self) -> None:
        obj = MagicMock()
        uid = uuid.uuid4()
        obj.organization_id = str(uid)
        assert _get_org_id(obj) == uid

    def test_no_attr(self) -> None:
        obj = MagicMock(spec=[])  # Empty spec — no attributes
        assert _get_org_id(obj) is None

    def test_invalid_string(self) -> None:
        obj = MagicMock()
        obj.organization_id = "not-a-uuid"
        assert _get_org_id(obj) is None


# ── PK extraction tests ──────────────────────────────────────────────────


class TestGetPkValue:
    def test_single_pk(self) -> None:
        obj = MagicMock()
        obj.invoice_id = uuid.uuid4()
        col = MagicMock()
        col.name = "invoice_id"
        mapper = MagicMock()
        mapper.primary_key = [col]
        assert _get_pk_value(obj, mapper) == str(obj.invoice_id)

    def test_composite_pk(self) -> None:
        obj = MagicMock()
        obj.org_id = "org1"
        obj.code = "CODE1"
        col1 = MagicMock()
        col1.name = "org_id"
        col2 = MagicMock()
        col2.name = "code"
        mapper = MagicMock()
        mapper.primary_key = [col1, col2]
        assert _get_pk_value(obj, mapper) == "org1:CODE1"

    def test_none_pk(self) -> None:
        obj = MagicMock()
        obj.id = None
        col = MagicMock()
        col.name = "id"
        mapper = MagicMock()
        mapper.primary_key = [col]
        assert _get_pk_value(obj, mapper) is None


# ── Registration test ────────────────────────────────────────────────────


class TestRegisterListeners:
    @patch("app.services.audit_listener.event")
    def test_registers_both_hooks(self, mock_event: MagicMock) -> None:
        register_audit_listeners()
        calls = mock_event.listen.call_args_list
        assert len(calls) == 2
        # First call: before_flush
        assert calls[0][0][1] == "before_flush"
        # Second call: after_flush
        assert calls[1][0][1] == "after_flush"
