"""Tests for the automatic ORM audit listener."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from unittest.mock import MagicMock, patch

from app.services.audit_listener import (
    _PENDING_KEY,
    _get_org_id,
    _get_pk_value,
    _serialise_value,
    _should_skip,
    _write_audit_records,
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

    def test_org_metric_snapshot_skipped(self) -> None:
        assert _should_skip("public", "org_metric_snapshot") is True


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


# ── RLS context handling tests ─────────────────────────────────────────────


def _make_fake_session(
    pending_records: list[dict],
    original_org_setting: str | None = "",
) -> tuple[MagicMock, MagicMock, list[str]]:
    """Build a MagicMock Session whose connection records every SQL it executes.

    Returns (session, connection, executed_sql_list). The captured SQL list is
    populated in order — including the leading current_setting() probe, every
    SET LOCAL pin, the INSERT, and the trailing RESET / SET LOCAL restore.
    """
    executed_sql: list[str] = []

    def _record_execute(stmt, params=None):
        # SQLAlchemy text() instances stringify to the raw SQL
        executed_sql.append(str(stmt))
        result = MagicMock()
        result.scalar.return_value = original_org_setting
        return result

    connection = MagicMock()
    connection.execute.side_effect = _record_execute
    # begin_nested() returns a context-managerlike object that supports
    # .commit() / .rollback(). We don't need anything fancy.
    connection.begin_nested.return_value = MagicMock()

    session = MagicMock()
    session.connection.return_value = connection
    session.info = {_PENDING_KEY: pending_records}
    return session, connection, executed_sql


class TestWriteAuditRecordsRLSContext:
    """Regression coverage for the RLS gap in the Mono webhook path.

    The audit listener must pin app.current_organization_id to the audit
    row's own org before each INSERT, so audit_log's RLS WITH CHECK passes
    on sessions that never set the GUC (webhooks, two-session API routes).
    """

    def _make_record(self, org_id: uuid.UUID) -> dict:
        return {
            "obj": MagicMock(),
            "mapper": MagicMock(),
            "action": "UPDATE",
            "schema": "banking",
            "table_name": "bank_accounts",
            "organization_id": org_id,
            "record_id": "3f7fb574-6157-40d6-8a9d-60d2e2f3f5c3",
            "old_values": {"mono_last_sync_error": None},
            "new_values": {"mono_last_sync_error": "Bank connection expired."},
        }

    def test_sets_org_guc_before_insert(self) -> None:
        """Per-row SET LOCAL must run before each INSERT so RLS passes."""
        org_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        session, _conn, sql = _make_fake_session([self._make_record(org_id)])

        _write_audit_records(session)

        set_local_idx = next(
            i
            for i, s in enumerate(sql)
            if f"SET LOCAL app.current_organization_id = '{org_id}'" in s
        )
        insert_idx = next(
            i for i, s in enumerate(sql) if "INSERT INTO audit.audit_log" in s
        )
        assert set_local_idx < insert_idx, (
            "SET LOCAL must precede the audit INSERT so the WITH CHECK passes"
        )

    def test_uses_row_org_not_request_org(self) -> None:
        """The GUC is set from the row's own org_id, not the request's."""
        row_org = uuid.UUID("22222222-2222-2222-2222-222222222222")
        # Simulate a session where the request set a *different* org GUC
        # (e.g. the auth dep primed Session A while we're writing on B).
        session, _conn, sql = _make_fake_session(
            [self._make_record(row_org)],
            original_org_setting="99999999-9999-9999-9999-999999999999",
        )

        _write_audit_records(session)

        assert any(
            f"SET LOCAL app.current_organization_id = '{row_org}'" in s for s in sql
        )

    def test_restores_original_guc_after_batch(self) -> None:
        """After the batch, the GUC must return to its pre-batch value."""
        original = "33333333-3333-3333-3333-333333333333"
        row_org = uuid.UUID("44444444-4444-4444-4444-444444444444")
        session, _conn, sql = _make_fake_session(
            [self._make_record(row_org)],
            original_org_setting=original,
        )

        _write_audit_records(session)

        # The restore must be the *last* GUC mutation
        guc_mutations = [
            s
            for s in sql
            if "SET LOCAL app.current_organization_id" in s
            or "RESET app.current_organization_id" in s
        ]
        assert guc_mutations[-1] == (
            f"SET LOCAL app.current_organization_id = '{original}'"
        )

    def test_resets_when_no_prior_guc(self) -> None:
        """If no GUC was set on the session, the listener must RESET, not
        leave the connection pinned to the last audit row's org."""
        row_org = uuid.UUID("55555555-5555-5555-5555-555555555555")
        session, _conn, sql = _make_fake_session(
            [self._make_record(row_org)],
            original_org_setting="",  # webhook path — nothing primed it
        )

        _write_audit_records(session)

        guc_mutations = [
            s
            for s in sql
            if "SET LOCAL app.current_organization_id" in s
            or "RESET app.current_organization_id" in s
        ]
        assert guc_mutations[-1] == "RESET app.current_organization_id"
