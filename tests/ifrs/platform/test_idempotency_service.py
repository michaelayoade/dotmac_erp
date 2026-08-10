"""
Tests for IdempotencyService.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.ifrs.platform.conftest import MockColumn, MockIdempotencyRecord

# ---------------------------------------------------------------------------
# Import IdempotencyService once at module level with model mocks active
# ---------------------------------------------------------------------------
_idem_modules_patch = patch.dict(
    "sys.modules",
    {
        "app.models.ifrs.platform.idempotency_record": MagicMock(),
    },
)
_idem_modules_patch.start()
from app.services.finance.platform.idempotency import IdempotencyService  # noqa: E402

# NOTE: do NOT call stop() — patch must remain active for module path resolution.


@contextmanager
def patch_idempotency_service():
    """Helper context manager that sets up all required patches for IdempotencyService."""
    with patch(
        "app.services.finance.platform.idempotency.IdempotencyRecord"
    ) as mock_record:
        mock_record.organization_id = MockColumn()
        mock_record.idempotency_key = MockColumn()
        mock_record.endpoint = MockColumn()
        mock_record.expires_at = MockColumn()
        mock_record.record_id = MockColumn()
        mock_record.created_at = MockColumn()
        with (
            patch(
                "app.services.finance.platform.idempotency.and_",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.finance.platform.idempotency.coerce_uuid",
                side_effect=lambda x: x,
            ),
            patch(
                "app.services.finance.platform.idempotency.select",
                return_value=MagicMock(),
            ),
        ):
            yield mock_record


class TestIdempotencyService:
    """Tests for IdempotencyService."""

    @pytest.fixture
    def service(self):
        """Return the pre-imported IdempotencyService class."""
        return IdempotencyService

    def test_check_returns_none_for_new_key(
        self, service, mock_db_session, organization_id
    ):
        """New idempotency keys should return None."""
        mock_db_session.scalars.return_value.first.return_value = None

        with (
            patch("app.services.finance.platform.idempotency.IdempotencyRecord"),
            patch(
                "app.services.finance.platform.idempotency.select",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.finance.platform.idempotency.coerce_uuid",
                side_effect=lambda x: x,
            ),
        ):
            result = service.check(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="new-key-123",
                endpoint="/api/v1/invoices",
                request_hash="abc123",
            )

        assert result is None

    def test_check_returns_record_for_existing_valid_key(
        self, service, mock_db_session, organization_id
    ):
        """Existing valid keys should return the record."""
        future_expiry = datetime.now(UTC) + timedelta(hours=12)
        mock_record = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="existing-key",
            endpoint="/api/v1/invoices",
            request_hash="abc123",
            response_status=201,
            expires_at=future_expiry,
        )
        mock_db_session.scalars.return_value.first.return_value = mock_record

        with (
            patch("app.services.finance.platform.idempotency.IdempotencyRecord"),
            patch(
                "app.services.finance.platform.idempotency.select",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.finance.platform.idempotency.coerce_uuid",
                side_effect=lambda x: x,
            ),
        ):
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

    def test_check_raises_conflict_for_different_hash(
        self, service, mock_db_session, organization_id
    ):
        """Same key with different request hash should raise 409 Conflict."""
        future_expiry = datetime.now(UTC) + timedelta(hours=12)
        mock_record = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="conflict-key",
            endpoint="/api/v1/invoices",
            request_hash="original-hash",
            expires_at=future_expiry,
        )
        mock_db_session.scalars.return_value.first.return_value = mock_record

        with (
            patch("app.services.finance.platform.idempotency.IdempotencyRecord"),
            patch(
                "app.services.finance.platform.idempotency.select",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.finance.platform.idempotency.coerce_uuid",
                side_effect=lambda x: x,
            ),
        ):
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

    def test_check_deletes_expired_record(
        self, service, mock_db_session, organization_id
    ):
        """Expired records should be deleted and return None."""
        past_expiry = datetime.now(UTC) - timedelta(hours=1)
        mock_record = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="expired-key",
            endpoint="/api/v1/invoices",
            request_hash="abc123",
            expires_at=past_expiry,
        )
        mock_db_session.scalars.return_value.first.return_value = mock_record

        with (
            patch("app.services.finance.platform.idempotency.IdempotencyRecord"),
            patch(
                "app.services.finance.platform.idempotency.select",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.finance.platform.idempotency.coerce_uuid",
                side_effect=lambda x: x,
            ),
        ):
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

    def test_store_response_creates_record(
        self, service, mock_db_session, organization_id
    ):
        """store_response should create a new idempotency record."""
        with (
            patch(
                "app.services.finance.platform.idempotency.IdempotencyRecord"
            ) as MockRecord,
            patch(
                "app.services.finance.platform.idempotency.coerce_uuid",
                side_effect=lambda x: x,
            ),
        ):
            mock_instance = MagicMock()
            MockRecord.return_value = mock_instance

            service.store_response(
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

    def test_get_cached_response_returns_tuple(
        self, service, mock_db_session, organization_id
    ):
        """get_cached_response should return (status, body) tuple."""
        future_expiry = datetime.now(UTC) + timedelta(hours=12)
        mock_record = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="cached-key",
            endpoint="/api/v1/invoices",
            request_hash="abc123",
            response_status=200,
            response_body={"data": "test"},
            expires_at=future_expiry,
        )
        mock_db_session.scalars.return_value.first.return_value = mock_record

        with patch_idempotency_service():
            result = service.get_cached_response(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="cached-key",
                endpoint="/api/v1/invoices",
                request_hash="abc123",
            )

        assert result is not None
        assert result[0] == 200
        assert result[1] == {"data": "test"}

    def test_get_cached_response_returns_none_for_missing(
        self, service, mock_db_session, organization_id
    ):
        """get_cached_response should return None for missing keys."""
        mock_db_session.scalars.return_value.first.return_value = None

        with patch_idempotency_service():
            result = service.get_cached_response(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="missing-key",
                endpoint="/api/v1/invoices",
                request_hash="abc123",
            )

        assert result is None

    def test_cleanup_expired_deletes_old_records(self, service, mock_db_session):
        """cleanup_expired should delete expired records."""
        expired_id = uuid.uuid4()
        mock_select_result = MagicMock()
        mock_select_result.all.return_value = [(expired_id,)]
        mock_delete_result = MagicMock()
        mock_delete_result.rowcount = 1
        mock_db_session.execute.side_effect = [mock_select_result, mock_delete_result]

        with (
            patch_idempotency_service(),
            patch("app.services.finance.platform.idempotency.delete"),
        ):
            result = service.cleanup_expired(mock_db_session, batch_size=100)

        assert result == 1
        mock_db_session.commit.assert_called_once()

    def test_cleanup_expired_returns_zero_when_none_expired(
        self, service, mock_db_session
    ):
        """cleanup_expired should return 0 when no records expired."""
        mock_db_session.execute.return_value.all.return_value = []

        with patch_idempotency_service():
            result = service.cleanup_expired(mock_db_session)

        assert result == 0

    def test_get_raises_404_for_missing_record(self, service, mock_db_session):
        """get should raise 404 for missing records."""
        mock_db_session.get.return_value = None

        with (
            patch("app.services.finance.platform.idempotency.IdempotencyRecord"),
            patch(
                "app.services.finance.platform.idempotency.coerce_uuid",
                side_effect=lambda x: x,
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                service.get(mock_db_session, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_filters_by_organization(
        self, service, mock_db_session, organization_id
    ):
        """list should filter by organization_id."""
        mock_records = [
            MockIdempotencyRecord(organization_id=organization_id),
            MockIdempotencyRecord(organization_id=organization_id),
        ]
        mock_db_session.scalars.return_value.all.return_value = mock_records

        with patch_idempotency_service():
            result = service.list(
                mock_db_session,
                organization_id=str(organization_id),
                limit=50,
                offset=0,
            )

        assert len(result) == 2


class TestReservationLease:
    """`reserve` writes its `202 "Request in progress"` placeholder BEFORE the
    side effect. A request that died in between left a row that every retry
    replayed for the full 24h TTL, with no lease, no stale detector and no way
    to re-drive the work. A lapsed reservation is now takeable."""

    def test_a_fresh_reservation_is_not_stale(self):
        record = MockIdempotencyRecord(
            response_status=202,
            response_body={"detail": "Request in progress"},
            created_at=datetime.now(UTC),
        )
        assert IdempotencyService.is_stale_reservation(record) is False

    def test_a_lapsed_reservation_is_stale(self):
        record = MockIdempotencyRecord(
            response_status=202,
            response_body={"detail": "Request in progress"},
            created_at=datetime.now(UTC) - timedelta(hours=2),
        )
        assert IdempotencyService.is_stale_reservation(record) is True

    def test_a_completed_response_is_never_stale(self):
        """A recorded OUTCOME stays replayable for its whole TTL — only an
        unfinished claim is subject to the lease."""
        record = MockIdempotencyRecord(
            response_status=201,
            response_body={"id": "created"},
            created_at=datetime.now(UTC) - timedelta(hours=23),
        )
        assert IdempotencyService.is_stale_reservation(record) is False

    def test_the_lease_is_far_shorter_than_the_record_ttl(self):
        """If the lease ever reached the TTL the stuck-placeholder failure would
        be back: the row would block retries for as long as it survives."""
        assert (
            IdempotencyService.RESERVATION_LEASE_MINUTES
            < IdempotencyService.DEFAULT_TTL_HOURS * 60
        )

    def test_naive_created_at_is_treated_as_utc(self):
        """SQLite hands back naive datetimes; comparing one against an aware
        `now` would raise rather than answer."""
        record = MockIdempotencyRecord(
            response_status=202,
            created_at=(datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None),
        )
        assert IdempotencyService.is_stale_reservation(record) is True


class TestRequestHashConflicts:
    """`update_response` writes `request_hash=""` when it has to create a record
    it expected to already exist. Comparing a real hash against that sentinel
    made every later retry of a legitimate request 409 forever."""

    def test_an_unknown_stored_hash_replays_instead_of_conflicting(
        self, service, mock_db_session, organization_id
    ):
        poisoned = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="k",
            endpoint="/api/v1/invoices",
            request_hash="",  # the sentinel written by update_response
            response_status=200,
            response_body={"data": "test"},
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
        mock_db_session.scalars.return_value.first.return_value = poisoned

        with patch_idempotency_service():
            result = service.check(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="k",
                endpoint="/api/v1/invoices",
                request_hash="a-real-hash",
            )

        assert result is poisoned

    def test_a_genuinely_different_hash_still_conflicts(
        self, service, mock_db_session, organization_id
    ):
        """The fix must not weaken the real conflict rule."""
        record = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="k",
            endpoint="/api/v1/invoices",
            request_hash="hash-of-request-one",
            response_status=200,
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
        mock_db_session.scalars.return_value.first.return_value = record

        with patch_idempotency_service(), pytest.raises(HTTPException) as exc:
            service.check(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="k",
                endpoint="/api/v1/invoices",
                request_hash="hash-of-request-two",
            )
        assert exc.value.status_code == 409

    def test_get_cached_response_now_enforces_the_hash_too(
        self, service, mock_db_session, organization_id
    ):
        """This read path skipped the comparison `check` performs, so it would
        hand one request's recorded response to a DIFFERENT request presenting
        the same key."""
        record = MockIdempotencyRecord(
            organization_id=organization_id,
            idempotency_key="k",
            endpoint="/api/v1/invoices",
            request_hash="hash-of-request-one",
            response_status=200,
            response_body={"data": "test"},
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
        mock_db_session.scalars.return_value.first.return_value = record

        with patch_idempotency_service(), pytest.raises(HTTPException) as exc:
            service.get_cached_response(
                mock_db_session,
                organization_id=organization_id,
                idempotency_key="k",
                endpoint="/api/v1/invoices",
                request_hash="hash-of-request-two",
            )
        assert exc.value.status_code == 409
