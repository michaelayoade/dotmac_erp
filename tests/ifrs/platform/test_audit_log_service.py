"""
Tests for AuditLogService.
"""

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from tests.ifrs.platform.conftest import MockColumn


@contextmanager
def patch_audit_log_service():
    """Helper context manager that sets up all required patches for AuditLogService."""
    with patch("app.services.finance.platform.audit_log.AuditLog") as mock_log:
        mock_log.organization_id = MockColumn()
        mock_log.table_schema = MockColumn()
        mock_log.table_name = MockColumn()
        mock_log.record_id = MockColumn()
        mock_log.occurred_at = MockColumn()
        mock_log.user_id = MockColumn()
        with (
            patch(
                "app.services.finance.platform.audit_log.and_", return_value=MagicMock()
            ),
            patch(
                "app.services.finance.platform.audit_log.coerce_uuid",
                side_effect=lambda x: x,
            ),
            patch(
                "app.services.finance.platform.audit_log.select",
                return_value=MagicMock(),
            ),
        ):
            yield mock_log


_SELECT_PATCH = "app.services.finance.platform.audit_log.select"
_COERCE_PATCH = "app.services.finance.platform.audit_log.coerce_uuid"
_MODEL_PATCH = "app.services.finance.platform.audit_log.AuditLog"


class MockAuditLog:
    """Mock AuditLog model."""

    def __init__(
        self,
        audit_id: uuid.UUID = None,
        organization_id: uuid.UUID = None,
        table_schema: str = "gl",
        table_name: str = "journal_entry",
        record_id: str = "123",
        action: MagicMock = None,
        old_values: dict = None,
        new_values: dict = None,
        changed_fields: list = None,
        user_id: uuid.UUID = None,
        occurred_at: datetime = None,
        hash_chain: str = None,
    ):
        self.audit_id = audit_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.table_schema = table_schema
        self.table_name = table_name
        self.record_id = record_id
        self.action = action or MagicMock(value="UPDATE")
        self.old_values = old_values
        self.new_values = new_values
        self.changed_fields = changed_fields or []
        self.user_id = user_id
        self.occurred_at = occurred_at or datetime.now(UTC)
        self.hash_chain = hash_chain


class TestAuditLogService:
    """Tests for AuditLogService."""

    @pytest.fixture
    def service(self):
        """Import the service class."""
        from app.services.finance.platform.audit_log import AuditLogService

        return AuditLogService

    @pytest.fixture
    def mock_audit_action(self):
        """Create a mock AuditAction enum value."""
        mock_action = MagicMock()
        mock_action.value = "UPDATE"
        return mock_action

    def test_log_change_creates_audit_record(
        self, service, mock_db_session, organization_id, user_id, mock_audit_action
    ):
        """log_change should create an audit log record."""
        mock_model = MagicMock()
        mock_instance = MagicMock()
        mock_instance.audit_id = uuid.uuid4()
        mock_model.return_value = mock_instance

        with (
            patch("app.services.finance.platform.audit_log.AuditLog", mock_model),
            patch(
                "app.services.finance.platform.audit_log.coerce_uuid",
                side_effect=lambda x: x,
            ),
            patch.object(service, "_get_previous_hash", return_value=None),
            patch(
                "app.services.finance.platform.audit_log.select",
                return_value=MagicMock(),
            ),
        ):
            result = service.log_change(
                mock_db_session,
                organization_id=organization_id,
                table_schema="gl",
                table_name="journal_entry",
                record_id="123",
                action=mock_audit_action,
                old_values={"status": "DRAFT"},
                new_values={"status": "POSTED"},
                user_id=user_id,
            )

        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_called_once()
        assert result == mock_instance.audit_id

    def test_log_change_computes_changed_fields(
        self, service, mock_db_session, organization_id, mock_audit_action
    ):
        """log_change should compute changed fields."""
        with (
            patch(_MODEL_PATCH) as MockModel,
            patch(_COERCE_PATCH, side_effect=lambda x: x),
            patch(_SELECT_PATCH, return_value=MagicMock()),
        ):
            mock_instance = MagicMock()
            MockModel.return_value = mock_instance
            service.log_change(
                mock_db_session,
                organization_id=organization_id,
                table_schema="gl",
                table_name="account",
                record_id="456",
                action=mock_audit_action,
                old_values={"name": "Old Name", "code": "A001"},
                new_values={"name": "New Name", "code": "A001"},
            )

        # Check that changed_fields was set correctly
        call_kwargs = MockModel.call_args[1]
        assert "name" in call_kwargs.get("changed_fields", [])
        assert "code" not in call_kwargs.get("changed_fields", [])

    def test_log_change_builds_hash_chain(
        self, service, mock_db_session, organization_id, mock_audit_action
    ):
        """log_change should compute hash chain."""
        prev_log = MockAuditLog(
            organization_id=organization_id,
            hash_chain="previous_hash_value",
        )
        mock_db_session.scalars.return_value.first.return_value = prev_log

        with (
            patch(_MODEL_PATCH) as MockModel,
            patch(_COERCE_PATCH, side_effect=lambda x: x),
            patch(_SELECT_PATCH, return_value=MagicMock()),
        ):
            mock_instance = MagicMock()
            MockModel.return_value = mock_instance
            service.log_change(
                mock_db_session,
                organization_id=organization_id,
                table_schema="gl",
                table_name="journal_entry",
                record_id="789",
                action=mock_audit_action,
                compute_hash=True,
            )

        call_kwargs = MockModel.call_args[1]
        assert call_kwargs.get("hash_chain") is not None

    def test_log_change_skips_hash_when_disabled(
        self, service, mock_db_session, organization_id, mock_audit_action
    ):
        """log_change should skip hash computation when disabled."""
        with (
            patch(_MODEL_PATCH) as MockModel,
            patch(_COERCE_PATCH, side_effect=lambda x: x),
            patch(_SELECT_PATCH, return_value=MagicMock()),
        ):
            mock_instance = MagicMock()
            MockModel.return_value = mock_instance
            service.log_change(
                mock_db_session,
                organization_id=organization_id,
                table_schema="gl",
                table_name="journal_entry",
                record_id="123",
                action=mock_audit_action,
                compute_hash=False,
            )

        call_kwargs = MockModel.call_args[1]
        assert call_kwargs.get("hash_chain") is None

    def test_get_audit_trail_filters_by_org(
        self, service, mock_db_session, organization_id
    ):
        """get_audit_trail should filter by organization."""
        mock_logs = [MockAuditLog(organization_id=organization_id)]
        mock_db_session.scalars.return_value.all.return_value = mock_logs

        with (
            patch(_MODEL_PATCH),
            patch(_COERCE_PATCH, side_effect=lambda x: x),
            patch(_SELECT_PATCH, return_value=MagicMock()),
        ):
            result = service.get_audit_trail(
                mock_db_session,
                organization_id=organization_id,
            )

        assert len(result) == 1

    def test_get_record_history_returns_chronological_list(
        self, service, mock_db_session, organization_id
    ):
        """get_record_history should return entries in chronological order."""
        mock_logs = [
            MockAuditLog(organization_id=organization_id),
            MockAuditLog(organization_id=organization_id),
        ]
        mock_db_session.scalars.return_value.all.return_value = mock_logs

        with (
            patch(_MODEL_PATCH),
            patch(_COERCE_PATCH, side_effect=lambda x: x),
            patch(_SELECT_PATCH, return_value=MagicMock()),
        ):
            result = service.get_record_history(
                mock_db_session,
                organization_id=organization_id,
                table_schema="gl",
                table_name="journal_entry",
                record_id="123",
            )

        assert len(result) == 2

    def test_build_hash_chain_returns_final_hash(
        self, service, mock_db_session, organization_id
    ):
        """build_hash_chain should return the final hash in chain."""
        mock_logs = [
            MockAuditLog(
                organization_id=organization_id,
                old_values=None,
                new_values={"field": "value1"},
            ),
            MockAuditLog(
                organization_id=organization_id,
                old_values={"field": "value1"},
                new_values={"field": "value2"},
            ),
        ]
        mock_db_session.scalars.return_value.all.return_value = mock_logs

        with patch_audit_log_service():
            result = service.build_hash_chain(
                mock_db_session,
                organization_id=organization_id,
                from_date=datetime.now(UTC),
                to_date=datetime.now(UTC),
            )

        assert result is not None
        assert len(result) == 64  # SHA256 hex length

    def test_build_hash_chain_returns_empty_for_no_records(
        self, service, mock_db_session, organization_id
    ):
        """build_hash_chain should return empty string for no records."""
        mock_db_session.scalars.return_value.all.return_value = []

        with patch_audit_log_service():
            result = service.build_hash_chain(
                mock_db_session,
                organization_id=organization_id,
                from_date=datetime.now(UTC),
                to_date=datetime.now(UTC),
            )

        assert result == ""

    def test_verify_hash_chain_returns_true_for_valid(
        self, service, mock_db_session, organization_id
    ):
        """verify_hash_chain should return True for valid chain."""
        # Empty chain is valid
        mock_db_session.scalars.return_value.all.return_value = []

        with patch_audit_log_service():
            result = service.verify_hash_chain(
                mock_db_session,
                organization_id=organization_id,
                from_date=datetime.now(UTC),
                to_date=datetime.now(UTC),
            )

        assert result == (True, None)

    def test_verify_hash_chain_detects_tampering(
        self, service, mock_db_session, organization_id
    ):
        """verify_hash_chain should detect hash mismatches."""
        records = [
            MockAuditLog(
                organization_id=organization_id,
                old_values=None,
                new_values={"field": "value1"},
            ),
            MockAuditLog(
                organization_id=organization_id,
                old_values={"field": "value1"},
                new_values={"field": "value2"},
            ),
        ]

        prev_hash = None
        for record in records:
            payload = {
                "audit_id": str(record.audit_id),
                "organization_id": str(record.organization_id),
                "table_schema": record.table_schema,
                "table_name": record.table_name,
                "record_id": record.record_id,
                "action": record.action.value,
                "old_values": record.old_values,
                "new_values": record.new_values,
                "user_id": str(record.user_id) if record.user_id else None,
                "occurred_at": record.occurred_at.isoformat(),
            }
            record.hash_chain = service._compute_hash(prev_hash, payload)
            prev_hash = record.hash_chain

        records[1].hash_chain = "tampered"
        mock_db_session.scalars.return_value.all.return_value = records

        with patch_audit_log_service():
            result = service.verify_hash_chain(
                mock_db_session,
                organization_id=organization_id,
                from_date=datetime.now(UTC),
                to_date=datetime.now(UTC),
            )

        assert result == (False, str(records[1].audit_id))

    def test_compute_changed_fields_detects_changes(self, service):
        """_compute_changed_fields should detect changed fields."""
        old_values = {"a": 1, "b": 2, "c": 3}
        new_values = {"a": 1, "b": 5, "d": 4}

        result = service._compute_changed_fields(old_values, new_values)

        assert "b" in result  # Changed
        assert "c" in result  # Removed
        assert "d" in result  # Added
        assert "a" not in result  # Unchanged

    def test_compute_changed_fields_handles_none_values(self, service):
        """_compute_changed_fields should handle None inputs."""
        assert service._compute_changed_fields(None, None) == []
        assert "a" in service._compute_changed_fields(None, {"a": 1})
        assert "a" in service._compute_changed_fields({"a": 1}, None)

    def test_compute_hash_produces_consistent_output(self, service):
        """_compute_hash should produce consistent output."""
        payload = {"test": "data"}

        hash1 = service._compute_hash(None, payload)
        hash2 = service._compute_hash(None, payload)

        assert hash1 == hash2
        assert len(hash1) == 64

    def test_compute_hash_includes_prev_hash(self, service):
        """_compute_hash should include previous hash in chain."""
        payload = {"test": "data"}

        hash_no_prev = service._compute_hash(None, payload)
        hash_with_prev = service._compute_hash("previous", payload)

        assert hash_no_prev != hash_with_prev

    def test_list_returns_audit_logs(self, service, mock_db_session, organization_id):
        """list should return filtered audit logs."""
        mock_logs = [
            MockAuditLog(organization_id=organization_id),
            MockAuditLog(organization_id=organization_id),
        ]
        mock_db_session.scalars.return_value.all.return_value = mock_logs

        with (
            patch(_MODEL_PATCH),
            patch(_COERCE_PATCH, side_effect=lambda x: x),
            patch(_SELECT_PATCH, return_value=MagicMock()),
        ):
            result = service.list(
                mock_db_session,
                organization_id=str(organization_id),
                table_schema="gl",
                limit=50,
                offset=0,
            )

        assert len(result) == 2
