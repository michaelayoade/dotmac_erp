import uuid

from app.models.audit import AuditActorType, AuditEvent


class TestAuditEventsAPI:
    """Tests for the /audit-events endpoints."""

    def test_get_audit_event(self, client, admin_headers, audit_event):
        """Test getting an audit event by ID."""
        response = client.get(f"/audit-events/{audit_event.id}", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(audit_event.id)
        assert data["action"] == audit_event.action

    def test_get_audit_event_not_found(self, client, admin_headers):
        """Test getting a non-existent audit event."""
        fake_id = str(uuid.uuid4())
        response = client.get(f"/audit-events/{fake_id}", headers=admin_headers)
        assert response.status_code == 404

    def test_get_audit_event_unauthorized(self, client, audit_event):
        """Test getting an audit event without auth."""
        response = client.get(f"/audit-events/{audit_event.id}", follow_redirects=False)
        # Returns 302 redirect to login when unauthorized
        assert response.status_code in [401, 302]

    def test_get_audit_event_insufficient_scope(
        self, client, auth_headers, audit_event
    ):
        """Test getting an audit event without audit scope."""
        response = client.get(
            f"/audit-events/{audit_event.id}",
            headers=auth_headers,
            follow_redirects=False,
        )
        # Returns 302 redirect or 403 forbidden when lacking scope
        assert response.status_code in [403, 302]

    def test_list_audit_events(self, client, admin_headers, audit_event):
        """Test listing audit events."""
        response = client.get("/audit-events", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data
        assert isinstance(data["items"], list)

    def test_list_audit_events_with_pagination(
        self, client, admin_headers, db_session, person
    ):
        """Test listing audit events with pagination."""
        # Create multiple audit events
        for i in range(5):
            event = AuditEvent(
                actor_id=str(person.id),
                actor_type=AuditActorType.user,
                action=f"test_action_{i}",
                entity_type="test_entity",
                entity_id=str(uuid.uuid4()),
                is_success=True,
                status_code=200,
            )
            db_session.add(event)
        db_session.commit()

        response = client.get("/audit-events?limit=2&offset=0", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 2

    def test_list_audit_events_filter_by_actor(
        self, client, admin_headers, audit_event
    ):
        """Test listing audit events filtered by actor_id."""
        response = client.get(
            f"/audit-events?actor_id={audit_event.actor_id}", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) >= 1

    def test_list_audit_events_filter_by_action(
        self, client, admin_headers, audit_event
    ):
        """Test listing audit events filtered by action."""
        response = client.get(
            f"/audit-events?action={audit_event.action}", headers=admin_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) >= 1

    def test_list_audit_events_filter_by_entity_type(
        self, client, admin_headers, audit_event
    ):
        """Test listing audit events filtered by entity_type."""
        response = client.get(
            f"/audit-events?entity_type={audit_event.entity_type}",
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) >= 1

    def test_list_audit_events_filter_by_success(
        self, client, admin_headers, audit_event
    ):
        """Test listing audit events filtered by is_success."""
        response = client.get("/audit-events?is_success=true", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["is_success"] is True

    def test_list_audit_events_filter_by_status_code(
        self, client, admin_headers, audit_event
    ):
        """Test listing audit events filtered by status_code."""
        response = client.get(
            f"/audit-events?status_code={audit_event.status_code}",
            headers=admin_headers,
        )
        assert response.status_code == 200

    def test_list_audit_events_with_ordering(self, client, admin_headers):
        """Test listing audit events with custom ordering."""
        response = client.get(
            "/audit-events?order_by=occurred_at&order_dir=asc", headers=admin_headers
        )
        assert response.status_code == 200

    def test_list_audit_events_unauthorized(self, client):
        """Test listing audit events without auth."""
        response = client.get("/audit-events", follow_redirects=False)
        # Returns 302 redirect to login when unauthorized
        assert response.status_code in [401, 302]

    def test_delete_audit_event(self, client, admin_headers, db_session, person):
        """Test deleting an audit event."""
        event = AuditEvent(
            actor_id=str(person.id),
            actor_type=AuditActorType.user,
            action="to_delete",
            entity_type="test_entity",
            entity_id=str(uuid.uuid4()),
            is_success=True,
            status_code=200,
        )
        db_session.add(event)
        db_session.commit()
        db_session.refresh(event)

        response = client.delete(f"/audit-events/{event.id}", headers=admin_headers)
        assert response.status_code == 204

    def test_delete_audit_event_not_found(self, client, admin_headers):
        """Test deleting a non-existent audit event."""
        fake_id = str(uuid.uuid4())
        response = client.delete(f"/audit-events/{fake_id}", headers=admin_headers)
        assert response.status_code == 404

    def test_delete_audit_event_unauthorized(self, client, audit_event):
        """Test deleting an audit event without auth."""
        response = client.delete(
            f"/audit-events/{audit_event.id}", follow_redirects=False
        )
        # Returns 302 redirect to login when unauthorized
        assert response.status_code in [401, 302]


class TestAuditEventsAPIV1:
    """Tests for the /api/v1/audit-events endpoints."""

    def test_get_audit_event_v1(self, client, admin_headers, audit_event):
        """Test getting an audit event via v1 API."""
        response = client.get(
            f"/api/v1/audit-events/{audit_event.id}", headers=admin_headers
        )
        assert response.status_code == 200

    def test_list_audit_events_v1(self, client, admin_headers):
        """Test listing audit events via v1 API."""
        response = client.get("/api/v1/audit-events", headers=admin_headers)
        assert response.status_code == 200


class TestAuditEventActorTypes:
    """Tests for different actor types in audit events."""

    def test_list_audit_events_filter_by_actor_type_user(
        self, client, admin_headers, db_session, person
    ):
        """Test filtering audit events by user actor type."""
        event = AuditEvent(
            actor_id=str(person.id),
            actor_type=AuditActorType.user,
            action="user_action",
            entity_type="test_entity",
            entity_id=str(uuid.uuid4()),
            is_success=True,
            status_code=200,
        )
        db_session.add(event)
        db_session.commit()

        response = client.get("/audit-events?actor_type=user", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["actor_type"] == "user"

    def test_list_audit_events_filter_by_actor_type_system(
        self, client, admin_headers, db_session
    ):
        """Test filtering audit events by system actor type."""
        event = AuditEvent(
            actor_id="system",
            actor_type=AuditActorType.system,
            action="system_action",
            entity_type="test_entity",
            entity_id=str(uuid.uuid4()),
            is_success=True,
            status_code=200,
        )
        db_session.add(event)
        db_session.commit()

        response = client.get("/audit-events?actor_type=system", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["actor_type"] == "system"

    def test_list_audit_events_filter_by_actor_type_api_key(
        self, client, admin_headers, db_session
    ):
        """Test filtering audit events by api_key actor type."""
        event = AuditEvent(
            actor_id=str(uuid.uuid4()),
            actor_type=AuditActorType.api_key,
            action="api_key_action",
            entity_type="test_entity",
            entity_id=str(uuid.uuid4()),
            is_success=True,
            status_code=200,
        )
        db_session.add(event)
        db_session.commit()

        response = client.get("/audit-events?actor_type=api_key", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["actor_type"] == "api_key"
