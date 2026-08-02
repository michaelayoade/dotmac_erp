"""
Tests for OutboxPublisher.
"""

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from tests.ifrs.platform.conftest import MockColumn, MockEventOutbox

# ---------------------------------------------------------------------------
# Import OutboxPublisher once at module level with model mocks active
# ---------------------------------------------------------------------------
_outbox_modules_patch = patch.dict(
    "sys.modules",
    {
        "app.models.ifrs.platform.event_outbox": MagicMock(),
    },
)
_outbox_modules_patch.start()
from app.services.finance.platform.outbox_publisher import OutboxPublisher  # noqa: E402

# NOTE: do NOT call stop() — patch must remain active for module path resolution.


@contextmanager
def patch_outbox_publisher():
    """Helper context manager that sets up all required patches for OutboxPublisher."""
    with patch(
        "app.services.finance.platform.outbox_publisher.EventOutbox"
    ) as mock_outbox:
        mock_outbox.status = MockColumn()
        mock_outbox.next_retry_at = MockColumn()
        mock_outbox.lease_expires_at = MockColumn()
        mock_outbox.retry_count = MockColumn()
        mock_outbox.producer_module = MockColumn()
        mock_outbox.occurred_at = MockColumn()
        mock_outbox.correlation_id = MockColumn()
        mock_outbox.aggregate_type = MockColumn()
        mock_outbox.aggregate_id = MockColumn()
        with (
            patch(
                "app.services.finance.platform.outbox_publisher.and_",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.finance.platform.outbox_publisher.or_",
                return_value=MagicMock(),
            ),
            patch(
                "app.services.finance.platform.outbox_publisher.coerce_uuid",
                side_effect=lambda x: x,
            ),
            patch(
                "app.services.finance.platform.outbox_publisher.select",
                return_value=MagicMock(),
            ),
        ):
            yield mock_outbox


class TestOutboxPublisher:
    """Tests for OutboxPublisher."""

    @pytest.fixture
    def service(self):
        """Return the pre-imported OutboxPublisher class."""
        return OutboxPublisher

    @pytest.fixture
    def mock_event_status(self):
        """Create mock EventStatus enum values."""
        status = MagicMock()
        status.PENDING = "PENDING"
        status.PUBLISHED = "PUBLISHED"
        status.FAILED = "FAILED"
        status.DEAD = "DEAD"
        return status

    def test_publish_event_creates_record(self, service, mock_db_session):
        """publish_event should create an event outbox record."""
        with patch(
            "app.services.finance.platform.outbox_publisher.EventOutbox"
        ) as MockModel:
            mock_instance = MagicMock()
            MockModel.return_value = mock_instance
            with patch(
                "app.services.finance.platform.outbox_publisher.EventStatus"
            ) as MockStatus:
                MockStatus.PENDING = "PENDING"
                with patch(
                    "app.services.finance.platform.outbox_publisher.coerce_uuid",
                    side_effect=lambda x: x,
                ):
                    service.publish_event(
                        mock_db_session,
                        event_name="journal.posted",
                        aggregate_type="JournalEntry",
                        aggregate_id="123",
                        payload={"journal_id": "123"},
                        headers={"organization_id": str(uuid.uuid4())},
                        producer_module="GL",
                        correlation_id="corr-123",
                        idempotency_key="idemp-456",
                    )

        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_called_once()
        mock_db_session.commit.assert_not_called()

    @pytest.mark.skip(
        reason="Complex SQLAlchemy expression mocking with | operator - tested via integration"
    )
    def test_get_pending_events_returns_ready_events(
        self, service, mock_db_session, mock_event_status
    ):
        """get_pending_events should return events ready for publishing."""
        pass

    @pytest.mark.skip(
        reason="Complex SQLAlchemy expression mocking with | operator - tested via integration"
    )
    def test_get_pending_events_includes_failed_status(
        self, service, mock_db_session, mock_event_status
    ):
        """get_pending_events should include FAILED events."""
        pass

    def test_get_pending_events_falls_back_on_scalar_result_error(
        self, service, mock_db_session, mock_event_status
    ) -> None:
        """Fallback to ORM Query API if scalar result materialization fails."""
        expected_events = [MockEventOutbox(event_id=uuid.uuid4())]
        mock_query = mock_db_session.query.return_value
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.limit.return_value = mock_query
        mock_query.all.return_value = expected_events
        mock_db_session.scalars.side_effect = NotImplementedError("scalar failure")

        with patch_outbox_publisher() as mock_event_model:
            with patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ):
                result = service.get_pending_events(
                    mock_db_session, batch_size=25, max_retry_count=5
                )

        assert result == expected_events
        mock_db_session.query.assert_called_once_with(mock_event_model)

    def test_mark_published_updates_status(
        self, service, mock_db_session, mock_event_status
    ):
        """mark_published should update event status to PUBLISHED (flush only)."""
        event_id = uuid.uuid4()
        claim_token = uuid.uuid4()
        mock_event = MockEventOutbox(
            event_id=event_id, status="PENDING", claim_token=claim_token
        )
        mock_db_session.get.return_value = mock_event

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ):
                with patch(
                    "app.services.finance.platform.outbox_publisher.coerce_uuid",
                    side_effect=lambda x: x,
                ):
                    service.mark_published(
                        mock_db_session,
                        event_id=event_id,
                        claim_token=claim_token,
                    )

        assert mock_event.status == "PUBLISHED"
        assert mock_event.published_at is not None
        assert mock_event.claim_token is None
        assert mock_event.lease_expires_at is None
        # Services flush; the calling task owns the commit.
        mock_db_session.flush.assert_called_once()
        mock_db_session.commit.assert_not_called()

    def test_mark_published_raises_for_missing_event(self, service, mock_db_session):
        """mark_published should raise for non-existent event."""
        mock_db_session.get.return_value = None

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.coerce_uuid",
                side_effect=lambda x: x,
            ):
                with pytest.raises(ValueError) as exc_info:
                    service.mark_published(
                        mock_db_session,
                        event_id=uuid.uuid4(),
                        claim_token=uuid.uuid4(),
                    )

        assert "Event not found" in str(exc_info.value)

    def test_mark_published_rejects_stale_claim_token(
        self, service, mock_db_session, mock_event_status
    ):
        """A stale claimant (token mismatch after reclaim) must not settle."""
        from app.services.finance.platform.outbox_publisher import StaleClaimError

        event_id = uuid.uuid4()
        mock_event = MockEventOutbox(
            event_id=event_id, status="PENDING", claim_token=uuid.uuid4()
        )
        mock_db_session.get.return_value = mock_event

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.coerce_uuid",
                side_effect=lambda x: x,
            ):
                with pytest.raises(StaleClaimError):
                    service.mark_published(
                        mock_db_session,
                        event_id=event_id,
                        claim_token=uuid.uuid4(),  # different token
                    )

        assert mock_event.status == "PENDING"
        mock_db_session.flush.assert_not_called()
        mock_db_session.commit.assert_not_called()

    def test_mark_unsupported_dead_letters_with_reason(
        self, service, mock_db_session, mock_event_status
    ):
        """Unknown events fail closed: DEAD with terminal_reason, never PUBLISHED."""
        from app.models.finance.platform.event_outbox import TerminalReason

        event_id = uuid.uuid4()
        claim_token = uuid.uuid4()
        mock_event = MockEventOutbox(
            event_id=event_id, status="PENDING", claim_token=claim_token
        )
        mock_db_session.get.return_value = mock_event

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ):
                with patch(
                    "app.services.finance.platform.outbox_publisher.coerce_uuid",
                    side_effect=lambda x: x,
                ):
                    service.mark_unsupported(
                        mock_db_session,
                        event_id=event_id,
                        claim_token=claim_token,
                    )

        assert mock_event.status == "DEAD"
        assert mock_event.terminal_reason == TerminalReason.UNSUPPORTED_EVENT
        assert mock_event.error_class == "UnsupportedEventError"
        mock_db_session.commit.assert_not_called()

    def test_handle_retry_increments_retry_count(
        self, service, mock_db_session, mock_event_status
    ):
        """handle_retry should increment retry count (flush only)."""
        event_id = uuid.uuid4()
        claim_token = uuid.uuid4()
        mock_event = MockEventOutbox(
            event_id=event_id,
            status="PENDING",
            retry_count=0,
            claim_token=claim_token,
        )
        mock_db_session.get.return_value = mock_event

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ):
                with patch(
                    "app.services.finance.platform.outbox_publisher.coerce_uuid",
                    side_effect=lambda x: x,
                ):
                    service.handle_retry(
                        mock_db_session,
                        event_id=event_id,
                        claim_token=claim_token,
                        error_message="Connection timeout",
                        error_class="TimeoutError",
                    )

        assert mock_event.retry_count == 1
        assert mock_event.last_error == "Connection timeout"
        assert mock_event.error_class == "TimeoutError"
        # The lease is released so the event is reclaimable at next_retry_at.
        assert mock_event.claim_token is None
        mock_db_session.flush.assert_called_once()
        mock_db_session.commit.assert_not_called()

    def test_handle_retry_marks_dead_after_max_retries(
        self, service, mock_db_session, mock_event_status
    ):
        """handle_retry should mark event DEAD after max retries."""
        from app.models.finance.platform.event_outbox import TerminalReason

        event_id = uuid.uuid4()
        claim_token = uuid.uuid4()
        mock_event = MockEventOutbox(
            event_id=event_id,
            status="PENDING",
            retry_count=4,  # One more retry will hit MAX_RETRY_COUNT (5)
            claim_token=claim_token,
        )
        mock_db_session.get.return_value = mock_event

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ):
                with patch(
                    "app.services.finance.platform.outbox_publisher.coerce_uuid",
                    side_effect=lambda x: x,
                ):
                    service.handle_retry(
                        mock_db_session,
                        event_id=event_id,
                        claim_token=claim_token,
                        error_message="Max retries exceeded",
                    )

        assert mock_event.status == "DEAD"
        assert mock_event.terminal_reason == TerminalReason.MAX_RETRIES_EXCEEDED

    def test_handle_retry_schedules_next_retry(
        self, service, mock_db_session, mock_event_status
    ):
        """handle_retry should schedule next retry with exponential delay."""
        event_id = uuid.uuid4()
        claim_token = uuid.uuid4()
        mock_event = MockEventOutbox(
            event_id=event_id,
            status="PENDING",
            retry_count=0,
            claim_token=claim_token,
        )
        mock_db_session.get.return_value = mock_event

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ):
                with patch(
                    "app.services.finance.platform.outbox_publisher.coerce_uuid",
                    side_effect=lambda x: x,
                ):
                    service.handle_retry(
                        mock_db_session,
                        event_id=event_id,
                        claim_token=claim_token,
                        error_message="Temporary error",
                    )

        assert mock_event.next_retry_at is not None
        assert mock_event.status == "FAILED"

    def test_mark_dead_permanently_fails_event(
        self, service, mock_db_session, mock_event_status
    ):
        """mark_dead should permanently fail an event."""
        event_id = uuid.uuid4()
        mock_event = MockEventOutbox(event_id=event_id, status="FAILED")
        mock_db_session.get.return_value = mock_event

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ):
                with patch(
                    "app.services.finance.platform.outbox_publisher.coerce_uuid",
                    side_effect=lambda x: x,
                ):
                    service.mark_dead(
                        mock_db_session,
                        event_id=event_id,
                        error_message="Unrecoverable error",
                    )

        assert mock_event.status == "DEAD"
        assert mock_event.last_error == "Unrecoverable error"
        assert mock_event.terminal_reason is not None
        mock_db_session.commit.assert_not_called()

    def test_get_failed_events_returns_failed_events(
        self, service, mock_db_session, mock_event_status
    ):
        """get_failed_events should return failed events."""
        mock_events = [MockEventOutbox(status="FAILED")]
        mock_db_session.scalars.return_value.all.return_value = mock_events

        with (
            patch("app.services.finance.platform.outbox_publisher.EventOutbox"),
            patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ),
            patch(
                "app.services.finance.platform.outbox_publisher.select",
                return_value=MagicMock(),
            ),
        ):
            result = service.get_failed_events(
                mock_db_session,
                status=mock_event_status.FAILED,
            )

        assert len(result) == 1

    def test_retry_dead_event_resets_status(
        self, service, mock_db_session, mock_event_status
    ):
        """retry_dead_event (authorized replay) resets a dead event and
        stages audit evidence in the same transaction."""
        event_id = uuid.uuid4()
        mock_event = MockEventOutbox(
            event_id=event_id,
            status="DEAD",
            retry_count=5,
            last_error="Previous error",
            terminal_reason="max_retries_exceeded",
        )
        mock_db_session.get.return_value = mock_event

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ):
                with patch(
                    "app.services.finance.platform.outbox_publisher.coerce_uuid",
                    side_effect=lambda x: x,
                ):
                    service.retry_dead_event(
                        mock_db_session,
                        event_id=event_id,
                        actor_id="ops@example.test",
                        reason="Upstream outage resolved",
                    )

        assert mock_event.status == "PENDING"
        assert mock_event.retry_count == 0
        assert mock_event.next_retry_at is None
        assert mock_event.last_error is None
        assert mock_event.terminal_reason is None
        # Audit evidence staged in the SAME transaction (add + flush, no commit).
        mock_db_session.add.assert_called_once()
        audit = mock_db_session.add.call_args.args[0]
        assert audit.action == "outbox.replay_dead_event"
        assert audit.actor_id == "ops@example.test"
        assert audit.metadata_["reason"] == "Upstream outage resolved"
        mock_db_session.commit.assert_not_called()

    def test_retry_dead_event_requires_actor_and_reason(
        self, service, mock_db_session, mock_event_status
    ):
        """Replay is an authorized operation — actor and reason are mandatory."""
        with pytest.raises(ValueError, match="actor_id"):
            service.retry_dead_event(
                mock_db_session, event_id=uuid.uuid4(), actor_id="", reason="x"
            )
        with pytest.raises(ValueError, match="reason"):
            service.retry_dead_event(
                mock_db_session, event_id=uuid.uuid4(), actor_id="ops", reason=" "
            )

    def test_retry_dead_event_rejects_non_dead_event(
        self, service, mock_db_session, mock_event_status
    ):
        """Only DEAD events are replayable."""
        event_id = uuid.uuid4()
        mock_event = MockEventOutbox(event_id=event_id, status="PENDING")
        mock_db_session.get.return_value = mock_event

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ):
                with patch(
                    "app.services.finance.platform.outbox_publisher.coerce_uuid",
                    side_effect=lambda x: x,
                ):
                    with pytest.raises(ValueError, match="Only DEAD"):
                        service.retry_dead_event(
                            mock_db_session,
                            event_id=event_id,
                            actor_id="ops",
                            reason="should fail",
                        )

    def test_get_event_returns_event(self, service, mock_db_session):
        """get_event should return an event by ID."""
        event_id = uuid.uuid4()
        mock_event = MockEventOutbox(event_id=event_id)
        mock_db_session.get.return_value = mock_event

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.coerce_uuid",
                side_effect=lambda x: x,
            ):
                result = service.get_event(
                    mock_db_session,
                    event_id=str(event_id),
                )

        assert result == mock_event

    def test_get_event_raises_for_missing(self, service, mock_db_session):
        """get_event should raise for non-existent event."""
        mock_db_session.get.return_value = None

        with patch("app.services.finance.platform.outbox_publisher.EventOutbox"):
            with patch(
                "app.services.finance.platform.outbox_publisher.coerce_uuid",
                side_effect=lambda x: x,
            ):
                with pytest.raises(ValueError) as exc_info:
                    service.get_event(
                        mock_db_session,
                        event_id=str(uuid.uuid4()),
                    )

        assert "Event not found" in str(exc_info.value)

    def test_get_events_by_aggregate_filters_correctly(self, service, mock_db_session):
        """get_events_by_aggregate should filter by aggregate."""
        mock_events = [
            MockEventOutbox(aggregate_type="JournalEntry", aggregate_id="123")
        ]
        mock_db_session.scalars.return_value.all.return_value = mock_events

        with (
            patch("app.services.finance.platform.outbox_publisher.EventOutbox"),
            patch(
                "app.services.finance.platform.outbox_publisher.select",
                return_value=MagicMock(),
            ),
        ):
            result = service.get_events_by_aggregate(
                mock_db_session,
                aggregate_type="JournalEntry",
                aggregate_id="123",
            )

        assert len(result) == 1

    def test_get_events_by_correlation_filters_correctly(
        self, service, mock_db_session
    ):
        """get_events_by_correlation should filter by correlation ID."""
        mock_events = [MockEventOutbox(correlation_id="corr-123")]
        mock_db_session.scalars.return_value.all.return_value = mock_events

        with (
            patch("app.services.finance.platform.outbox_publisher.EventOutbox"),
            patch(
                "app.services.finance.platform.outbox_publisher.select",
                return_value=MagicMock(),
            ),
        ):
            result = service.get_events_by_correlation(
                mock_db_session,
                correlation_id="corr-123",
            )

        assert len(result) == 1

    def test_list_returns_events(self, service, mock_db_session, mock_event_status):
        """list should return filtered events."""
        mock_events = [MockEventOutbox(), MockEventOutbox()]
        mock_db_session.scalars.return_value.all.return_value = mock_events

        with (
            patch("app.services.finance.platform.outbox_publisher.EventOutbox"),
            patch(
                "app.services.finance.platform.outbox_publisher.EventStatus",
                mock_event_status,
            ),
            patch(
                "app.services.finance.platform.outbox_publisher.select",
                return_value=MagicMock(),
            ),
        ):
            result = service.list(
                mock_db_session,
                status=mock_event_status.PENDING,
                producer_module="GL",
                limit=50,
                offset=0,
            )

        assert len(result) == 2
