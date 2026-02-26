"""Tests for ServiceHookWebService."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.services.hooks.web import ServiceHookWebService


class TestServiceHookWebService:
    @patch("app.services.hooks.web.is_feature_enabled")
    @patch("app.services.hooks.web.ServiceHookService")
    def test_settings_context(self, mock_service_cls, mock_enabled):
        db = MagicMock()
        org_id = uuid4()
        mock_enabled.return_value = True

        hook = MagicMock()
        hook.hook_id = uuid4()
        hook.name = "Test"
        hook.event_name = "sales.order.confirmed"
        hook.handler_type.value = "EVENT_OUTBOX"
        hook.execution_mode.value = "ASYNC"
        hook.is_active = True
        hook.priority = 10
        hook.max_retries = 3
        hook.retry_backoff_seconds = 60
        hook.handler_config = {
            "event_name_override": "x.y.z",
            "circuit_breaker_failures": 3,
            "timeout_seconds": 20,
        }

        svc = MagicMock()
        svc.list_for_org.return_value = [hook]
        svc.execution_stats.return_value = {"SUCCESS": 12, "FAILED": 2, "DEAD": 0}
        execution = MagicMock()
        execution.execution_id = uuid4()
        execution.status.value = "FAILED"
        execution.event_name = "sales.order.confirmed"
        execution.duration_ms = 125
        execution.response_status_code = 500
        execution.retry_count = 1
        execution.error_message = "boom"
        execution.created_at = None
        execution.executed_at = None
        svc.list_executions.return_value = [execution]
        mock_service_cls.return_value = svc

        ctx = ServiceHookWebService.settings_context(db, org_id)

        assert ctx["service_hooks_enabled"] is True
        assert len(ctx["hooks"]) == 1
        assert ctx["hooks"][0]["stats_success"] == 12
        assert ctx["hooks"][0]["stats_failed"] == 2
        assert len(ctx["hooks"][0]["recent_executions"]) == 1
        assert ctx["hooks"][0]["recent_executions"][0]["status"] == "FAILED"
        assert ctx["hooks"][0]["recent_executions"][0]["response_status_code"] == 500
        assert ctx["hooks"][0]["circuit_breaker_failures"] == 3
        assert ctx["hooks"][0]["webhook_timeout_seconds"] == 20
        assert "sales.order.confirmed" in ctx["available_events"]

    @patch("app.services.hooks.web.is_feature_enabled")
    @patch("app.services.hooks.web.ServiceHookService")
    def test_settings_context_filtered_applies_filters(
        self, mock_service_cls, mock_enabled
    ):
        db = MagicMock()
        org_id = uuid4()
        mock_enabled.return_value = True
        svc = MagicMock()
        svc.list_for_org.return_value = []
        mock_service_cls.return_value = svc

        ctx = ServiceHookWebService.settings_context_filtered(
            db,
            org_id,
            q="crm",
            handler_type="WEBHOOK",
            is_active="true",
        )

        assert ctx["filters"]["q"] == "crm"
        kwargs = svc.list_for_org.call_args.kwargs
        assert kwargs["name_contains"] == "crm"
        assert kwargs["handler_type"].value == "WEBHOOK"
        assert kwargs["is_active"] is True

    @patch("app.services.hooks.web.ServiceHookService")
    def test_create_from_form_webhook_success(self, mock_service_cls):
        db = MagicMock()
        org_id = uuid4()

        ok, error = ServiceHookWebService.create_from_form(
            db,
            org_id,
            uuid4(),
            {
                "name": "Webhook Hook",
                "event_name": "sales.order.confirmed",
                "handler_type": "WEBHOOK",
                "execution_mode": "ASYNC",
                "webhook_url": "https://example.com/hook",
                "priority": "10",
                "max_retries": "3",
                "retry_backoff_seconds": "60",
                "circuit_breaker_failures": "2",
                "webhook_timeout_seconds": "12",
                "is_active": "true",
            },
        )

        assert ok is True
        assert error is None
        mock_service_cls.return_value.create.assert_called_once()
        create_kwargs = mock_service_cls.return_value.create.call_args.kwargs
        assert create_kwargs["handler_config"]["circuit_breaker_failures"] == 2
        assert create_kwargs["handler_config"]["timeout_seconds"] == 12
        db.commit.assert_called_once()

    def test_create_from_form_missing_webhook_url(self):
        db = MagicMock()
        org_id = uuid4()

        ok, error = ServiceHookWebService.create_from_form(
            db,
            org_id,
            uuid4(),
            {
                "name": "Webhook Hook",
                "event_name": "sales.order.confirmed",
                "handler_type": "WEBHOOK",
                "execution_mode": "ASYNC",
                "priority": "10",
                "max_retries": "3",
                "retry_backoff_seconds": "60",
            },
        )

        assert ok is False
        assert error == "Webhook URL is required for WEBHOOK handler."

    @patch("app.services.hooks.web.ServiceHookService")
    def test_toggle(self, mock_service_cls):
        db = MagicMock()
        org_id = uuid4()
        hook_id = str(uuid4())
        db.get.return_value = MagicMock(organization_id=org_id)

        ok, error = ServiceHookWebService.toggle(db, org_id, hook_id, True)

        assert ok is True
        assert error is None
        mock_service_cls.return_value.toggle.assert_called_once()
        db.commit.assert_called_once()

    @patch("app.services.hooks.web.ServiceHookService")
    def test_delete(self, mock_service_cls):
        db = MagicMock()
        org_id = uuid4()
        hook_id = str(uuid4())
        db.get.return_value = MagicMock(organization_id=org_id)

        ok, error = ServiceHookWebService.delete(db, org_id, hook_id)

        assert ok is True
        assert error is None
        mock_service_cls.return_value.delete.assert_called_once()
        db.commit.assert_called_once()

    def test_toggle_enforces_org_scope(self):
        db = MagicMock()
        hook_id = str(uuid4())
        db.get.return_value = MagicMock(organization_id=uuid4())

        ok, error = ServiceHookWebService.toggle(db, uuid4(), hook_id, True)

        assert ok is False
        assert error == "Hook not found."

    @patch("app.services.hooks.web.ServiceHookService")
    def test_bulk_toggle_success(self, mock_service_cls):
        db = MagicMock()
        org_id = uuid4()
        hook_ids = [str(uuid4()), str(uuid4())]
        mock_service_cls.return_value.bulk_toggle.return_value = {
            "requested": 2,
            "updated": 1,
            "not_found_ids": [str(uuid4())],
        }

        ok, error, result = ServiceHookWebService.bulk_toggle(
            db, org_id, hook_ids, enabled=False
        )

        assert ok is True
        assert error is None
        assert result is not None
        assert result["requested"] == 2
        mock_service_cls.return_value.bulk_toggle.assert_called_once()
        db.commit.assert_called_once()

    @patch("app.services.hooks.web.ServiceHookService")
    def test_bulk_delete_success(self, mock_service_cls):
        db = MagicMock()
        org_id = uuid4()
        hook_ids = [str(uuid4()), str(uuid4())]
        mock_service_cls.return_value.bulk_delete.return_value = {
            "requested": 2,
            "deleted": 1,
            "not_found_ids": [str(uuid4())],
        }

        ok, error, result = ServiceHookWebService.bulk_delete(db, org_id, hook_ids)

        assert ok is True
        assert error is None
        assert result is not None
        assert result["requested"] == 2
        mock_service_cls.return_value.bulk_delete.assert_called_once()
        db.commit.assert_called_once()

    @patch("app.services.hooks.web.ServiceHookService")
    def test_update_from_form_success(self, mock_service_cls):
        db = MagicMock()
        org_id = uuid4()
        hook_id = str(uuid4())
        db.get.return_value = MagicMock(organization_id=org_id)

        ok, error = ServiceHookWebService.update_from_form(
            db,
            org_id,
            hook_id,
            {
                "name": "Updated Hook",
                "event_name": "sales.order.confirmed",
                "handler_type": "WEBHOOK",
                "execution_mode": "ASYNC",
                "webhook_url": "https://example.com/new-hook",
                "priority": "5",
                "max_retries": "4",
                "retry_backoff_seconds": "30",
                "is_active": "true",
                "description": "Updated",
            },
        )

        assert ok is True
        assert error is None
        mock_service_cls.return_value.update.assert_called_once()
        db.commit.assert_called_once()

    def test_update_from_form_enforces_org_scope(self):
        db = MagicMock()
        db.get.return_value = MagicMock(organization_id=uuid4())

        ok, error = ServiceHookWebService.update_from_form(
            db,
            uuid4(),
            str(uuid4()),
            {
                "name": "Updated Hook",
                "event_name": "sales.order.confirmed",
                "handler_type": "EVENT_OUTBOX",
                "execution_mode": "ASYNC",
                "priority": "10",
                "max_retries": "3",
                "retry_backoff_seconds": "60",
            },
        )

        assert ok is False
        assert error == "Hook not found."

    @patch("app.services.hooks.web.ServiceHookService")
    def test_retry_execution_success(self, mock_service_cls):
        db = MagicMock()
        org_id = uuid4()
        hook_id = str(uuid4())
        execution_id = str(uuid4())
        db.get.return_value = MagicMock(organization_id=org_id)

        ok, error = ServiceHookWebService.retry_execution(
            db, org_id, hook_id, execution_id
        )

        assert ok is True
        assert error is None
        mock_service_cls.return_value.retry_execution.assert_called_once()
        db.commit.assert_called_once()

    @patch("app.services.hooks.web.ServiceHookService")
    def test_execution_detail_success(self, mock_service_cls):
        db = MagicMock()
        org_id = uuid4()
        hook_id = str(uuid4())
        execution_id = str(uuid4())
        execution = MagicMock()
        execution.execution_id = uuid4()
        execution.status.value = "FAILED"
        execution.event_name = "sales.order.confirmed"
        execution.duration_ms = 120
        execution.response_status_code = 500
        execution.retry_count = 1
        execution.error_message = "boom"
        execution.created_at = None
        execution.executed_at = None
        execution.event_payload = {"invoice_number": "INV-1"}
        execution.response_body = "error body"
        mock_service_cls.return_value.get_execution.return_value = execution

        detail, error = ServiceHookWebService.execution_detail(
            db, org_id, hook_id, execution_id
        )

        assert error is None
        assert detail is not None
        assert detail["event_payload"]["invoice_number"] == "INV-1"
        assert detail["response_body"] == "error body"
