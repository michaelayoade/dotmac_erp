"""
Tests for IdempotencyService.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from fastapi import HTTPException

from tests.ifrs.platform.conftest import MockColumn, MockIdempotencyRecord


@contextmanager
def patch_idempotency_service():
    """Helper context manager that sets up all required patches for IdempotencyService."""
    with patch('app.services.ifrs.platform.idempotency.IdempotencyRecord') as mock_record:
        mock_record.organization_id = MockColumn()
        mock_record.idempotency_key = MockColumn()
        mock_record.endpoint = MockColumn()
        mock_record.expires_at = MockColumn()
        mock_record.record_id = MockColumn()
        with patch('app.services.ifrs.platform.idempotency.and_', return_value=MagicMock()):
            with patch('app.services.ifrs.platform.idempotency.coerce_uuid', side_effect=lambda x: x):
                yield mock_record


class TestIdempotencyService:
    """Tests for IdempotencyService."""

    @pytest.fixture
    def service(self):
        """Import the service with mocked dependencies."""
        with patch.dict('sys.modules', {
            'app.models.ifrs.platform.idempotency_record': MagicMock(),
        }):
            from app.services.finance.platform.idempotency import IdempotencyService
            return IdempotencyService

    def test_check_returns_none_for_new_key(self, service, mock_db_session, organization_id):
        """New idempotency keys should return None."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        with patch('app.services.ifrs.platform.idempotency.IdempotencyRecord'):
            result = service.check(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="new-key-123",
                endpoint="/api/v1/invoices",
                request_hash="abc123",
            )

        assert result is None

    def test_check_returns_record_for_existing_valid_key(self, service, mock_db_session, organization_id):
        """Existing valid keys should return the record."""
        future_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        mock_record = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="existing-key",
            endpoint="/api/v1/invoices",
            request_hash="abc123",
            response_status=201,
            expires_at=future_expiry,
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_record

        with patch('app.services.ifrs.platform.idempotency.IdempotencyRecord'):
            result = service.check(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="existing-key",
                endpoint="/api/v1/invoices",
                request_hash="abc123",
            )

        assert result is not None
        assert result.response_status == 201
        assert result.idempotency_key == "existing-key"

    def test_check_raises_conflict_for_different_hash(self, service, mock_db_session, organization_id):
        """Same key with different request hash should raise 409 Conflict."""
        future_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        mock_record = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="conflict-key",
            endpoint="/api/v1/invoices",
            request_hash="original-hash",
            expires_at=future_expiry,
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_record

        with patch('app.services.ifrs.platform.idempotency.IdempotencyRecord'):
            with pytest.raises(HTTPException) as exc_info:
                service.check(
                    mock_db_session,
                    organization_id=organization_id,
                    idempotency_key="conflict-key",
                    endpoint="/api/v1/invoices",
                    request_hash="different-hash",
                )

        assert exc_info.value.status_code == 409
        assert "different request body" in exc_info.value.detail

    def test_check_deletes_expired_record(self, service, mock_db_session, organization_id):
        """Expired records should be deleted and return None."""
        past_expiry = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_record = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="expired-key",
            endpoint="/api/v1/invoices",
            request_hash="abc123",
            expires_at=past_expiry,
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_record

        with patch('app.services.ifrs.platform.idempotency.IdempotencyRecord'):
            result = service.check(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="expired-key",
                endpoint="/api/v1/invoices",
                request_hash="abc123",
            )

        assert result is None
        mock_db_session.delete.assert_called_once_with(mock_record)
        mock_db_session.commit.assert_called_once()

    def test_store_response_creates_record(self, service, mock_db_session, organization_id):
        """store_response should create a new idempotency record."""
        with patch('app.services.ifrs.platform.idempotency.IdempotencyRecord') as MockRecord:
            mock_instance = MagicMock()
            MockRecord.return_value = mock_instance

            result = service.store_response(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="new-key",
                endpoint="/api/v1/invoices",
                request_hash="hash123",
                response_status=201,
                response_body={"id": "123"},
                ttl_hours=24,
            )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()
        mock_db_session.refresh.assert_called_once()

    def test_get_cached_response_returns_tuple(self, service, mock_db_session, organization_id):
        """get_cached_response should return (status, body) tuple."""
        future_expiry = datetime.now(timezone.utc) + timedelta(hours=12)
        mock_record = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="cached-key",
            endpoint="/api/v1/invoices",
            request_hash="abc123",
            response_status=200,
            response_body={"data": "test"},
            expires_at=future_expiry,
        )
        mock_db_session.query.return_value.filter.return_value.first.return_value = mock_record

        with patch_idempotency_service():
            result = service.get_cached_response(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="cached-key",
                endpoint="/api/v1/invoices",
            )

        assert result is not None
        assert result[0] == 200
        assert result[1] == {"data": "test"}

    def test_get_cached_response_returns_none_for_missing(self, service, mock_db_session, organization_id):
        """get_cached_response should return None for missing keys."""
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        with patch_idempotency_service():
            result = service.get_cached_response(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="missing-key",
                endpoint="/api/v1/invoices",
            )

        assert result is None

    def test_cleanup_expired_deletes_old_records(self, service, mock_db_session):
        """cleanup_expired should delete expired records."""
        expired_id = uuid.uuid4()
        mock_db_session.query.return_value.filter.return_value.limit.return_value.all.return_value = [
            (expired_id,),
        ]
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_db_session.execute.return_value = mock_result

        with patch_idempotency_service():
            with patch('app.services.ifrs.platform.idempotency.delete') as mock_delete:
                result = service.cleanup_expired(mock_db_session, batch_size=100)

        assert result == 1
        mock_db_session.commit.assert_called_once()

    def test_cleanup_expired_returns_zero_when_none_expired(self, service, mock_db_session):
        """cleanup_expired should return 0 when no records expired."""
        mock_db_session.query.return_value.filter.return_value.limit.return_value.all.return_value = []

        with patch_idempotency_service():
            result = service.cleanup_expired(mock_db_session)

        assert result == 0

    def test_get_raises_404_for_missing_record(self, service, mock_db_session):
        """get should raise 404 for missing records."""
        mock_db_session.get.return_value = None

        with patch('app.services.ifrs.platform.idempotency.IdempotencyRecord'):
            with pytest.raises(HTTPException) as exc_info:
                service.get(mock_db_session, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_filters_by_organization(self, service, mock_db_session, organization_id):
        """list should filter by organization_id."""
        mock_records = [
            MockIdempotencyRecord(organization_id=organization_id),
            MockIdempotencyRecord(organization_id=organization_id),
        ]
        mock_db_session.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_records

        with patch_idempotency_service():
            result = service.list(
                mock_db_session,
                organization_id=str(organization_id),
                limit=50,
                offset=0,
            )

        assert len(result) == 2
