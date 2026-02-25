"""
Tests for app/services/audit.py

Tests for AuditEvents service class that manages audit event CRUD operations
and request logging functionality.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.models.audit import AuditActorType
from app.services.audit import AuditEvents

# ============ TestParseActorType ============


class TestParseActorType:
    """Tests for the parse_actor_type method."""

    def test_parse_actor_type_none(self):
        """None input should return None."""
        result = AuditEvents.parse_actor_type(None)
        assert result is None

    def test_parse_actor_type_user(self):
        """'user' string should return AuditActorType.user."""
        result = AuditEvents.parse_actor_type("user")
        assert result == AuditActorType.user

    def test_parse_actor_type_system(self):
        """'system' string should return AuditActorType.system."""
        result = AuditEvents.parse_actor_type("system")
        assert result == AuditActorType.system

    def test_parse_actor_type_api_key(self):
        """'api_key' string should return AuditActorType.api_key."""
        result = AuditEvents.parse_actor_type("api_key")
        assert result == AuditActorType.api_key

    def test_parse_actor_type_service(self):
        """'service' string should return AuditActorType.service."""
        result = AuditEvents.parse_actor_type("service")
        assert result == AuditActorType.service

    def test_parse_actor_type_invalid_raises_value_error(self):
        """Invalid actor type should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid actor_type") as exc_info:
            AuditEvents.parse_actor_type("invalid_type")

        assert "user" in str(exc_info.value)  # Should list allowed values

    def test_parse_actor_type_already_enum(self):
        """AuditActorType enum value should be resolved correctly."""
        # AuditActorType(AuditActorType.user) succeeds in Python enums,
        # returning the same member, so the function handles it gracefully.
        result = AuditEvents.parse_actor_type(AuditActorType.user)
        assert result == AuditActorType.user


# ============ TestAuditEventsCreate ============


class TestAuditEventsCreate:
    """Tests for AuditEvents.create method."""

    def test_create_basic_event(self, mock_db):
        """Should create a basic audit event."""
        payload = MagicMock()
        payload.occurred_at = datetime.now(UTC)
        payload.model_dump.return_value = {
            "actor_type": AuditActorType.user,
            "actor_id": str(uuid.uuid4()),
            "action": "test_action",
            "entity_type": "test_entity",
            "status_code": 200,
            "is_success": True,
            "occurred_at": datetime.now(UTC),
        }

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.create(mock_db, payload)

            mock_db.add.assert_called_once_with(mock_event)
            mock_db.flush.assert_called_once()
            mock_db.refresh.assert_called_once_with(mock_event)

    def test_create_with_occurred_at(self, mock_db):
        """Should use provided occurred_at timestamp."""
        specific_time = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        payload = MagicMock()
        payload.occurred_at = specific_time
        payload.model_dump.return_value = {
            "actor_type": AuditActorType.user,
            "action": "test",
            "entity_type": "test",
            "status_code": 200,
            "occurred_at": specific_time,
        }

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.create(mock_db, payload)

            # Verify occurred_at is included in the call
            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("occurred_at") == specific_time

    def test_create_without_occurred_at_uses_default(self, mock_db):
        """None occurred_at should be removed from data (DB uses default)."""
        payload = MagicMock()
        payload.occurred_at = None
        payload.model_dump.return_value = {
            "actor_type": AuditActorType.user,
            "action": "test",
            "entity_type": "test",
            "status_code": 200,
            "occurred_at": None,
        }

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.create(mock_db, payload)

            # occurred_at should be popped from data
            call_kwargs = MockEvent.call_args[1]
            assert "occurred_at" not in call_kwargs

    def test_create_refreshes_from_db(self, mock_db):
        """Created event should be refreshed from database."""
        payload = MagicMock()
        payload.occurred_at = datetime.now(UTC)
        payload.model_dump.return_value = {
            "actor_type": AuditActorType.user,
            "action": "test",
            "entity_type": "test",
            "status_code": 200,
            "occurred_at": datetime.now(UTC),
        }

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.create(mock_db, payload)

            mock_db.refresh.assert_called_once_with(mock_event)


# ============ TestAuditEventsGet ============


class TestAuditEventsGet:
    """Tests for AuditEvents.get method."""

    def test_get_existing_event(self, mock_db, mock_audit_event):
        """Should return existing event by ID."""
        mock_db.get.return_value = mock_audit_event
        event_id = str(uuid.uuid4())

        result = AuditEvents.get(mock_db, event_id)

        assert result == mock_audit_event

    def test_get_nonexistent_raises_not_found(self, mock_db):
        """Nonexistent event ID should raise NotFoundError."""
        from app.services.common import NotFoundError

        mock_db.get.return_value = None
        event_id = str(uuid.uuid4())

        with pytest.raises(NotFoundError, match="Audit event not found"):
            AuditEvents.get(mock_db, event_id)

    def test_get_invalid_uuid(self, mock_db):
        """Invalid UUID format should raise HTTPException(400)."""
        with pytest.raises(HTTPException) as exc_info:
            AuditEvents.get(mock_db, "not-a-uuid")
        assert exc_info.value.status_code == 400


# ============ TestAuditEventsList ============


class TestAuditEventsList:
    """Tests for AuditEvents.list method."""

    def _setup_mock_scalars(self, mock_db, return_value=None):
        """Helper to setup mock for select() + db.scalars() pattern."""
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter(return_value or []))
        mock_db.scalars.return_value = mock_result
        return mock_result

    def test_list_all_active(self, mock_db, mock_audit_event):
        """Should list active events by default."""
        self._setup_mock_scalars(mock_db, [mock_audit_event])

        result = AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type=None,
            request_id=None,
            is_success=None,
            status_code=None,
            is_active=None,  # None means filter for active only
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        assert len(result) == 1
        mock_db.scalars.assert_called_once()

    def test_list_filter_by_actor_id(self, mock_db):
        """Should filter by actor_id."""
        self._setup_mock_scalars(mock_db)
        actor_id = str(uuid.uuid4())

        result = AuditEvents.list(
            mock_db,
            actor_id=actor_id,
            actor_type=None,
            action=None,
            entity_type=None,
            request_id=None,
            is_success=None,
            status_code=None,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()
        assert isinstance(result, list)

    def test_list_filter_by_actor_type(self, mock_db):
        """Should filter by actor_type."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=AuditActorType.user,
            action=None,
            entity_type=None,
            request_id=None,
            is_success=None,
            status_code=None,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()

    def test_list_filter_by_action(self, mock_db):
        """Should filter by action."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action="GET",
            entity_type=None,
            request_id=None,
            is_success=None,
            status_code=None,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()

    def test_list_filter_by_entity_type(self, mock_db):
        """Should filter by entity_type."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type="/api/users",
            request_id=None,
            is_success=None,
            status_code=None,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()

    def test_list_filter_by_request_id(self, mock_db):
        """Should filter by request_id."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type=None,
            request_id="req-123",
            is_success=None,
            status_code=None,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()

    def test_list_filter_by_is_success_true(self, mock_db):
        """Should filter by is_success=True."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type=None,
            request_id=None,
            is_success=True,
            status_code=None,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()

    def test_list_filter_by_is_success_false(self, mock_db):
        """Should filter by is_success=False."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type=None,
            request_id=None,
            is_success=False,
            status_code=None,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()

    def test_list_filter_by_status_code(self, mock_db):
        """Should filter by status_code."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type=None,
            request_id=None,
            is_success=None,
            status_code=500,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()

    def test_list_filter_by_is_active(self, mock_db):
        """Should filter by explicit is_active value."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type=None,
            request_id=None,
            is_success=None,
            status_code=None,
            is_active=False,  # Explicit False to include inactive
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()

    def test_list_order_by_occurred_at(self, mock_db):
        """Should order by occurred_at."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type=None,
            request_id=None,
            is_success=None,
            status_code=None,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()

    def test_list_order_by_action(self, mock_db):
        """Should order by action."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type=None,
            request_id=None,
            is_success=None,
            status_code=None,
            is_active=None,
            order_by="action",
            order_dir="asc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()

    def test_list_invalid_order_by_raises_value_error(self, mock_db):
        """Invalid order_by column should raise ValueError."""
        self._setup_mock_scalars(mock_db)

        with pytest.raises(ValueError, match="Invalid order_by"):
            AuditEvents.list(
                mock_db,
                actor_id=None,
                actor_type=None,
                action=None,
                entity_type=None,
                request_id=None,
                is_success=None,
                status_code=None,
                is_active=None,
                order_by="invalid_column",
                order_dir="asc",
                limit=10,
                offset=0,
            )

    def test_list_pagination(self, mock_db):
        """Should apply limit and offset correctly."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=None,
            actor_type=None,
            action=None,
            entity_type=None,
            request_id=None,
            is_success=None,
            status_code=None,
            is_active=None,
            order_by="occurred_at",
            order_dir="desc",
            limit=25,
            offset=50,
        )

        mock_db.scalars.assert_called_once()

    def test_list_combined_filters(self, mock_db):
        """Should apply multiple filters together."""
        self._setup_mock_scalars(mock_db)

        AuditEvents.list(
            mock_db,
            actor_id=str(uuid.uuid4()),
            actor_type=AuditActorType.user,
            action="POST",
            entity_type="/api/users",
            request_id="req-123",
            is_success=True,
            status_code=201,
            is_active=True,
            order_by="occurred_at",
            order_dir="desc",
            limit=10,
            offset=0,
        )

        mock_db.scalars.assert_called_once()


# ============ TestLogRequest ============


class TestLogRequest:
    """Tests for AuditEvents.log_request method."""

    def _create_mock_request(
        self,
        method="GET",
        path="/api/test",
        actor_type="user",
        actor_id=None,
        state_actor_id=None,
        state_actor_type=None,
        request_id=None,
        state_request_id=None,
        entity_id=None,
        ip_address="127.0.0.1",
        user_agent="pytest",
        query_params=None,
    ):
        """Helper to create mock request object."""
        request = MagicMock()
        request.method = method
        request.url = MagicMock()
        request.url.path = path

        headers = {
            "x-actor-type": actor_type,
            "x-actor-id": actor_id,
            "x-request-id": request_id,
            "x-entity-id": entity_id,
            "user-agent": user_agent,
        }
        request.headers = MagicMock()
        request.headers.get = lambda key, default=None: headers.get(key, default)

        request.client = MagicMock()
        request.client.host = ip_address

        request.query_params = query_params or {}
        request.state = SimpleNamespace()
        if state_actor_id is not None:
            request.state.actor_id = state_actor_id
        if state_actor_type is not None:
            request.state.actor_type = state_actor_type
        if state_request_id is not None:
            request.state.request_id = state_request_id

        return request

    def test_log_request_extracts_method_as_action(self, mock_db):
        """Request method should become audit action."""
        request = self._create_mock_request(method="POST")
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("action") == "POST"

    def test_log_request_extracts_path_as_entity_type(self, mock_db):
        """Request path should become audit entity_type."""
        request = self._create_mock_request(path="/api/users/123")
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("entity_type") == "/api/users/123"

    def test_log_request_extracts_actor_type_header(self, mock_db):
        """x-actor-type header should set actor_type."""
        request = self._create_mock_request(actor_type="api_key")
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("actor_type") == AuditActorType.api_key

    def test_log_request_extracts_actor_id_header(self, mock_db):
        """x-actor-id header should set actor_id."""
        actor_id = str(uuid.uuid4())
        request = self._create_mock_request(actor_id=actor_id)
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("actor_id") == actor_id

    def test_log_request_falls_back_to_request_state_actor(self, mock_db):
        """request.state.actor_id should be used when header is missing."""
        actor_id = str(uuid.uuid4())
        request = self._create_mock_request(
            actor_id=None,
            actor_type=None,
            state_actor_id=actor_id,
            state_actor_type="user",
        )
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("actor_id") == actor_id
            assert call_kwargs.get("actor_type") == AuditActorType.user

    def test_log_request_extracts_request_id_header(self, mock_db):
        """x-request-id header should set request_id."""
        request = self._create_mock_request(request_id="req-12345")
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("request_id") == "req-12345"

    def test_log_request_extracts_ip_address(self, mock_db):
        """Client IP address should be extracted."""
        request = self._create_mock_request(ip_address="192.168.1.100")
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("ip_address") == "192.168.1.100"

    def test_log_request_handles_none_client(self, mock_db):
        """Should handle None client gracefully."""
        request = self._create_mock_request()
        request.client = None
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            # Should not raise
            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("ip_address") is None

    def test_log_request_extracts_user_agent(self, mock_db):
        """User-Agent header should be extracted."""
        request = self._create_mock_request(user_agent="Mozilla/5.0")
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("user_agent") == "Mozilla/5.0"

    def test_log_request_is_success_true_under_400(self, mock_db):
        """Status code < 400 should set is_success=True."""
        request = self._create_mock_request()
        response = MagicMock(status_code=201)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("is_success") is True

    def test_log_request_is_success_false_400_plus(self, mock_db):
        """Status code >= 400 should set is_success=False."""
        request = self._create_mock_request()
        response = MagicMock(status_code=404)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("is_success") is False

    def test_log_request_query_params_in_metadata(self, mock_db):
        """Query params should be included in metadata."""
        request = self._create_mock_request(query_params={"page": "1", "limit": "10"})
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            metadata = call_kwargs.get("metadata_")
            assert metadata["query"] == {"page": "1", "limit": "10"}

    def test_log_request_commits(self, mock_db):
        """Log request should commit the event."""
        request = self._create_mock_request()
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent"):
            AuditEvents.log_request(mock_db, request, response)

            mock_db.flush.assert_called_once()

    def test_log_request_invalid_actor_type_defaults_to_system(self, mock_db):
        """Invalid actor_type header should default to system."""
        request = self._create_mock_request(actor_type="invalid_type")
        response = MagicMock(status_code=200)

        with patch("app.services.audit.AuditEvent") as MockEvent:
            mock_event = MagicMock()
            MockEvent.return_value = mock_event

            AuditEvents.log_request(mock_db, request, response)

            call_kwargs = MockEvent.call_args[1]
            assert call_kwargs.get("actor_type") == AuditActorType.system


# ============ TestAuditEventsDelete ============


class TestAuditEventsDelete:
    """Tests for AuditEvents.delete method."""

    def test_delete_soft_deletes(self, mock_db, mock_audit_event):
        """Delete should soft-delete by setting is_active=False."""
        mock_db.get.return_value = mock_audit_event

        AuditEvents.delete(mock_db, str(mock_audit_event.id))

        assert mock_audit_event.is_active is False
        mock_db.flush.assert_called_once()

    def test_delete_nonexistent_raises_not_found(self, mock_db):
        """Deleting nonexistent event should raise NotFoundError."""
        from app.services.common import NotFoundError

        mock_db.get.return_value = None

        with pytest.raises(NotFoundError, match="Audit event not found"):
            AuditEvents.delete(mock_db, str(uuid.uuid4()))
