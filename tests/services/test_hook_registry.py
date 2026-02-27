"""Tests for hook registry dispatch."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.models.finance.platform.service_hook import (
    HookExecutionMode,
    HookHandlerType,
    ServiceHook,
)
from app.services.hooks.registry import HookEvent, HookRegistry, _execute_hook_handler


def _hook(**overrides) -> ServiceHook:
    data = {
        "event_name": "inventory.stock.reserved",
        "handler_type": HookHandlerType.EVENT_OUTBOX,
        "execution_mode": HookExecutionMode.ASYNC,
        "handler_config": {},
        "conditions": {},
        "name": "Test Hook",
        "organization_id": None,
    }
    data.update(overrides)
    return ServiceHook(**data)


class TestHookRegistry:
    @patch("app.services.hooks.registry.is_feature_enabled")
    def test_emit_disabled_feature_returns_empty(self, mock_enabled):
        mock_enabled.return_value = False
        db = MagicMock()
        registry = HookRegistry(db)

        result = registry.emit(
            HookEvent(
                event_name="inventory.stock.reserved",
                organization_id=uuid4(),
                entity_type="StockReservation",
                entity_id=uuid4(),
                actor_user_id=None,
                payload={"status": "ok"},
            )
        )

        assert result == []

    @patch("app.tasks.hooks.execute_async_hook.delay")
    @patch("app.services.hooks.registry.is_feature_enabled")
    def test_emit_queues_async_hooks(self, mock_enabled, mock_delay):
        mock_enabled.return_value = True
        db = MagicMock()
        db.scalars.return_value.all.return_value = [_hook()]
        registry = HookRegistry(db)

        result = registry.emit(
            HookEvent(
                event_name="inventory.stock.reserved",
                organization_id=uuid4(),
                entity_type="StockReservation",
                entity_id=uuid4(),
                actor_user_id=None,
                payload={"status": "ok"},
            )
        )

        assert len(result) == 1
        assert db.add.call_count == 1
        assert mock_delay.called

    def test_condition_matching(self):
        payload = {"status": "RESERVED", "amount": 25, "kind": "A"}
        assert HookRegistry._conditions_match({"status": "RESERVED"}, payload)
        assert HookRegistry._conditions_match({"amount_gt": 10}, payload)
        assert HookRegistry._conditions_match({"amount_gte": 25}, payload)
        assert HookRegistry._conditions_match({"amount_lt": 30}, payload)
        assert HookRegistry._conditions_match({"amount_lte": 25}, payload)
        assert HookRegistry._conditions_match({"kind_in": ["A", "B"]}, payload)
        assert not HookRegistry._conditions_match({"status": "FAILED"}, payload)
        assert not HookRegistry._conditions_match({"amount_gt": 30}, payload)

    @patch("app.services.notification.NotificationService.create")
    def test_execute_notification_handler(self, mock_create):
        db = MagicMock()
        notification = SimpleNamespace(notification_id=uuid4())
        mock_create.return_value = notification
        event = HookEvent(
            event_name="inventory.stock.reserved",
            organization_id=uuid4(),
            entity_type="StockReservation",
            entity_id=uuid4(),
            actor_user_id=uuid4(),
            payload={"quantity": "5"},
        )
        hook = _hook(
            handler_type=HookHandlerType.NOTIFICATION,
            handler_config={
                "recipient_id": str(uuid4()),
                "title": "Reserved ${quantity}",
                "message": "Entity ${entity_type} ${entity_id}",
                "notification_type": "INFO",
                "entity_type": "SYSTEM",
            },
            execution_mode=HookExecutionMode.SYNC,
        )

        result = _execute_hook_handler(db, hook, event)

        assert "notification_id" in result
        assert mock_create.called

    @patch("app.services.email.send_email")
    def test_execute_email_handler(self, mock_send_email):
        mock_send_email.return_value = True
        db = MagicMock()
        event = HookEvent(
            event_name="sales.order.confirmed",
            organization_id=uuid4(),
            entity_type="SalesOrder",
            entity_id=uuid4(),
            actor_user_id=None,
            payload={"so_number": "SO-1001"},
        )
        hook = _hook(
            handler_type=HookHandlerType.EMAIL,
            handler_config={
                "to_email": "ops@example.com",
                "subject": "SO ${so_number} confirmed",
                "body_html": "<p>${event_name}</p>",
            },
            execution_mode=HookExecutionMode.SYNC,
        )

        result = _execute_hook_handler(db, hook, event)

        assert result["sent"] is True
        assert result["to_email"] == "ops@example.com"
        assert mock_send_email.called

    @patch("app.services.hooks.registry.import_module")
    def test_execute_internal_service_handler(self, mock_import_module):
        db = MagicMock()
        event = HookEvent(
            event_name="gl.journal.posted",
            organization_id=uuid4(),
            entity_type="JournalEntry",
            entity_id=uuid4(),
            actor_user_id=uuid4(),
            payload={"journal_number": "JE-0001"},
        )

        def _callback(*, db, event, hook, **kwargs):
            assert kwargs["x"] == 1
            return f"ok:{event.event_name}:{hook.name}"

        mock_import_module.return_value = SimpleNamespace(run=_callback)
        hook = _hook(
            handler_type=HookHandlerType.INTERNAL_SERVICE,
            handler_config={
                "target": "app.services.hooks.callbacks:run",
                "kwargs": {"x": 1},
            },
            execution_mode=HookExecutionMode.SYNC,
        )

        result = _execute_hook_handler(db, hook, event)

        assert "ok:gl.journal.posted" in result["result"]

    @patch("app.services.hooks.registry._validate_webhook_target")
    @patch("httpx.Client")
    def test_execute_webhook_handler_posts(self, mock_client_cls, mock_validate):
        db = MagicMock()
        mock_validate.return_value = (True, None)
        event = HookEvent(
            event_name="sales.order.confirmed",
            organization_id=uuid4(),
            entity_type="SalesOrder",
            entity_id=uuid4(),
            actor_user_id=None,
            payload={"so_number": "SO-1001"},
        )
        response = MagicMock()
        response.status_code = 202
        response.text = "accepted"
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = response
        mock_client_cls.return_value.__enter__.return_value = client

        hook = _hook(
            handler_type=HookHandlerType.WEBHOOK,
            handler_config={
                "url": "https://example.com/webhook",
                "method": "POST",
                "timeout_seconds": 5,
            },
            execution_mode=HookExecutionMode.SYNC,
        )

        result = _execute_hook_handler(db, hook, event)

        mock_validate.assert_called_once_with(
            "https://example.com/webhook",
            db,
            allow_localhost=False,
        )
        assert result["status_code"] == 202
        assert "accepted" in result["body"]
        post_kwargs = client.post.call_args.kwargs
        assert post_kwargs["headers"] == {}
        assert post_kwargs["json"]["event"] == "sales.order.confirmed"
        assert post_kwargs["json"]["payload"]["so_number"] == "SO-1001"

    @patch("app.services.hooks.registry._validate_webhook_target")
    @patch("httpx.Client")
    def test_execute_webhook_handler_surfaces_http_errors(
        self, mock_client_cls, mock_validate
    ):
        db = MagicMock()
        mock_validate.return_value = (True, None)
        event = HookEvent(
            event_name="sales.order.confirmed",
            organization_id=uuid4(),
            entity_type="SalesOrder",
            entity_id=uuid4(),
            actor_user_id=None,
            payload={},
        )
        request = httpx.Request("POST", "https://example.com/webhook")
        response = httpx.Response(status_code=500, request=request)
        client = MagicMock()
        client.post.return_value = response
        mock_client_cls.return_value.__enter__.return_value = client
        error = httpx.HTTPStatusError("boom", request=request, response=response)
        response.raise_for_status = MagicMock(side_effect=error)

        hook = _hook(
            handler_type=HookHandlerType.WEBHOOK,
            handler_config={"url": "https://example.com/webhook", "method": "POST"},
            execution_mode=HookExecutionMode.SYNC,
        )

        with pytest.raises(httpx.HTTPStatusError):
            _execute_hook_handler(db, hook, event)

    @patch("app.services.hooks.registry._validate_webhook_target")
    @patch("httpx.Client")
    def test_execute_webhook_handler_rejects_disallowed_targets(
        self, mock_client_cls, mock_validate
    ):
        db = MagicMock()
        mock_validate.return_value = (False, "Webhook target is not allowed")
        event = HookEvent(
            event_name="sales.order.confirmed",
            organization_id=uuid4(),
            entity_type="SalesOrder",
            entity_id=uuid4(),
            actor_user_id=None,
            payload={},
        )
        hook = _hook(
            handler_type=HookHandlerType.WEBHOOK,
            handler_config={"url": "http://127.0.0.1/webhook", "method": "POST"},
            execution_mode=HookExecutionMode.SYNC,
        )

        with pytest.raises(ValueError, match="Webhook target is not allowed"):
            _execute_hook_handler(db, hook, event)

        mock_validate.assert_called_once_with(
            "http://127.0.0.1/webhook",
            db,
            allow_localhost=False,
        )
        mock_client_cls.assert_not_called()

    def test_execute_event_outbox_handler_writes_entry(self):
        db = MagicMock()
        event = HookEvent(
            event_name="inventory.stock.reserved",
            organization_id=uuid4(),
            entity_type="StockReservation",
            entity_id=uuid4(),
            actor_user_id=uuid4(),
            payload={"quantity": "2"},
        )
        hook = _hook(
            handler_type=HookHandlerType.EVENT_OUTBOX,
            handler_config={"event_name_override": "custom.stock.reserved"},
            execution_mode=HookExecutionMode.SYNC,
        )

        result = _execute_hook_handler(db, hook, event)

        assert "outbox_event_id" in result
        assert db.add.called
        assert db.flush.called
