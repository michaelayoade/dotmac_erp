"""
Tests for app/services/audit_dispatcher.py

Tests for the fire_audit_event() dispatcher function that logs
data changes to the audit trail.
"""

import uuid
from unittest.mock import MagicMock, patch

from app.models.finance.audit.audit_log import AuditAction
from app.services.audit_dispatcher import fire_audit_event


# Patch targets — imports are inside fire_audit_event() so we patch
# them at the module where they're looked up.
_SVC_PATCH = "app.services.finance.platform.audit_log.AuditLogService"
_ACTOR_PATCH = "app.observability.actor_id_var"
_REQ_PATCH = "app.observability.request_id_var"
_IP_PATCH = "app.observability.ip_address_var"
_UA_PATCH = "app.observability.user_agent_var"


class TestFireAuditEvent:
    """Tests for fire_audit_event() dispatcher."""

    def setup_method(self) -> None:
        self.db = MagicMock()
        self.org_id = uuid.uuid4()
        self.user_id = uuid.uuid4()

    @patch(_SVC_PATCH)
    def test_basic_insert_event(self, mock_service_cls: MagicMock) -> None:
        """fire_audit_event should call AuditLogService.log_change with correct args."""
        mock_service_cls.log_change = MagicMock(return_value=uuid.uuid4())

        fire_audit_event(
            self.db,
            self.org_id,
            "ar",
            "invoice",
            str(uuid.uuid4()),
            AuditAction.INSERT,
            new_values={"amount": "1000.00", "status": "DRAFT"},
            user_id=self.user_id,
        )

        mock_service_cls.log_change.assert_called_once()
        call_kwargs = mock_service_cls.log_change.call_args
        assert call_kwargs.kwargs["table_schema"] == "ar"
        assert call_kwargs.kwargs["table_name"] == "invoice"
        assert call_kwargs.kwargs["action"] == AuditAction.INSERT

    @patch(_SVC_PATCH)
    def test_update_event_with_old_new_values(
        self, mock_service_cls: MagicMock
    ) -> None:
        """UPDATE events should pass old_values and new_values."""
        mock_service_cls.log_change = MagicMock(return_value=uuid.uuid4())

        old = {"status": "DRAFT"}
        new = {"status": "SUBMITTED"}

        fire_audit_event(
            self.db,
            self.org_id,
            "gl",
            "journal_entry",
            str(uuid.uuid4()),
            AuditAction.UPDATE,
            old_values=old,
            new_values=new,
        )

        call_kwargs = mock_service_cls.log_change.call_args.kwargs
        assert call_kwargs["old_values"] == old
        assert call_kwargs["new_values"] == new

    @patch(_SVC_PATCH)
    def test_delete_event(self, mock_service_cls: MagicMock) -> None:
        """DELETE events should work without old/new values."""
        mock_service_cls.log_change = MagicMock(return_value=uuid.uuid4())

        fire_audit_event(
            self.db,
            self.org_id,
            "fleet",
            "vehicle_incident",
            str(uuid.uuid4()),
            AuditAction.DELETE,
            reason="Incident soft deleted",
        )

        call_kwargs = mock_service_cls.log_change.call_args.kwargs
        assert call_kwargs["action"] == AuditAction.DELETE
        assert call_kwargs["reason"] == "Incident soft deleted"

    @patch(_SVC_PATCH)
    def test_fire_and_forget_does_not_raise(self, mock_service_cls: MagicMock) -> None:
        """fire_audit_event must never raise, even if log_change fails."""
        mock_service_cls.log_change = MagicMock(
            side_effect=Exception("DB connection lost")
        )

        # Should not raise
        fire_audit_event(
            self.db,
            self.org_id,
            "ar",
            "invoice",
            str(uuid.uuid4()),
            AuditAction.INSERT,
        )

    @patch(_SVC_PATCH)
    def test_reason_passed_through(self, mock_service_cls: MagicMock) -> None:
        """The reason parameter should be forwarded to log_change."""
        mock_service_cls.log_change = MagicMock(return_value=uuid.uuid4())

        fire_audit_event(
            self.db,
            self.org_id,
            "ap",
            "supplier_invoice",
            str(uuid.uuid4()),
            AuditAction.UPDATE,
            reason="Invoice approved by finance manager",
        )

        call_kwargs = mock_service_cls.log_change.call_args.kwargs
        assert call_kwargs["reason"] == "Invoice approved by finance manager"

    @patch(_SVC_PATCH)
    @patch(_ACTOR_PATCH)
    def test_actor_id_from_context_var(
        self, mock_actor_var: MagicMock, mock_service_cls: MagicMock
    ) -> None:
        """When no explicit user_id is given, should fall back to actor_id_var."""
        mock_actor_var.get.return_value = str(self.user_id)
        mock_service_cls.log_change = MagicMock(return_value=uuid.uuid4())

        fire_audit_event(
            self.db,
            self.org_id,
            "hr",
            "employee",
            str(uuid.uuid4()),
            AuditAction.INSERT,
            # no explicit user_id
        )

        call_kwargs = mock_service_cls.log_change.call_args.kwargs
        assert call_kwargs["user_id"] == self.user_id

    @patch(_SVC_PATCH)
    @patch(_REQ_PATCH)
    def test_correlation_id_from_context_var(
        self, mock_request_var: MagicMock, mock_service_cls: MagicMock
    ) -> None:
        """correlation_id should be read from request_id_var."""
        mock_request_var.get.return_value = "req-abc-123"
        mock_service_cls.log_change = MagicMock(return_value=uuid.uuid4())

        fire_audit_event(
            self.db,
            self.org_id,
            "ar",
            "invoice",
            str(uuid.uuid4()),
            AuditAction.INSERT,
        )

        call_kwargs = mock_service_cls.log_change.call_args.kwargs
        assert call_kwargs["correlation_id"] == "req-abc-123"

    @patch(_SVC_PATCH)
    @patch(_IP_PATCH)
    @patch(_UA_PATCH)
    def test_ip_and_user_agent_from_context_vars(
        self,
        mock_ua_var: MagicMock,
        mock_ip_var: MagicMock,
        mock_service_cls: MagicMock,
    ) -> None:
        """IP address and user agent should be read from context vars."""
        mock_ip_var.get.return_value = "192.168.1.100"
        mock_ua_var.get.return_value = "Mozilla/5.0"
        mock_service_cls.log_change = MagicMock(return_value=uuid.uuid4())

        fire_audit_event(
            self.db,
            self.org_id,
            "auth",
            "session",
            str(uuid.uuid4()),
            AuditAction.INSERT,
        )

        call_kwargs = mock_service_cls.log_change.call_args.kwargs
        assert call_kwargs["ip_address"] == "192.168.1.100"
        assert call_kwargs["user_agent"] == "Mozilla/5.0"

    @patch(_SVC_PATCH)
    def test_uuid_record_id_converted_to_string(
        self, mock_service_cls: MagicMock
    ) -> None:
        """A UUID record_id should be converted to string."""
        mock_service_cls.log_change = MagicMock(return_value=uuid.uuid4())
        record_uuid = uuid.uuid4()

        fire_audit_event(
            self.db,
            self.org_id,
            "recruit",
            "job_opening",
            record_uuid,  # UUID, not string
            AuditAction.INSERT,
        )

        call_kwargs = mock_service_cls.log_change.call_args.kwargs
        assert call_kwargs["record_id"] == str(record_uuid)
